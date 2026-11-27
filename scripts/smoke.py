#!/usr/bin/env python3
"""Real-stack smoke test: drive the main EstateAI flows over plain HTTP (cookie + CSRF).

It creates accounts and Listings, so point it at a THROWAWAY database only (docs/testing.md).
The base URL must be loopback; mail is read from the local Mailpit. Standard library only.

    python3 scripts/smoke.py --base-url http://127.0.0.1:8100

The administrator flows need an administrator in the same database as the server. Either set
SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD, or pass --provision-admin, which runs
`manage.py provision_admin` with the current environment (POSTGRES_DB must name a database other
than the development one). Without either, the administrator flows are skipped.

Registration is limited to 10 per hour per client: one run uses three.
"""

import argparse
import base64
import concurrent.futures
import http.cookiejar
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class Failure(Exception):
    pass


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


class Client:
    """One browser: its own cookie jar and CSRF token."""

    def __init__(self, base):
        self.base = base.rstrip("/") + "/api/v1"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.token = None

    def raw(self, method, path, data=None, headers=None):
        request = urllib.request.Request(  # noqa: S310 - loopback only, see main()
            self.base + path, data=data, method=method, headers=headers or {}
        )
        try:
            with self.opener.open(request, timeout=60) as answer:
                return Response(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.headers, error.read())

    def call(self, method, path, body=None, expect=None, headers=None, data=None):
        headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if method != "GET":
            if self.token is None:
                self.token = self.raw("GET", "/csrf").json()["csrf_token"]
            headers["X-CSRFToken"] = self.token
        answer = self.raw(method, path, data, headers)
        if answer.status >= 500 and answer.status != 503:
            raise Failure(f"{method} {path} -> {answer.status} {answer.body[:300]!r}")
        if expect is not None and answer.status not in (
            expect if isinstance(expect, tuple) else (expect,)
        ):
            raise Failure(f"{method} {path} -> {answer.status} {answer.body[:600]!r}")
        return answer

    def get(self, path, expect=200):
        return self.call("GET", path, expect=expect)

    def post(self, path, body=None, expect=200, headers=None):
        return self.call("POST", path, body, expect, headers)

    def upload(
        self,
        path,
        image,
        method="POST",
        expect=None,
        name="file",
        filename="smoke.png",
        content_type="image/png",
    ):
        boundary = uuid.uuid4().hex
        data = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
            ).encode()
            + image
            + f"\r\n--{boundary}--\r\n".encode()
        )
        kind = f"multipart/form-data; boundary={boundary}"
        return self.call(method, path, expect=expect, headers={"Content-Type": kind}, data=data)

    def sign_in(self, email, password, path="/session"):
        answer = self.post(path, {"email": email, "password": password})
        self.token = None  # rotated on sign-in
        return answer


class LiveSocket:
    """A minimal WebSocket client (standard library only) for `/ws`: it reads small unfragmented
    frames, answers pings and never sends data."""

    def __init__(self, base, client, origin):
        parts = urllib.parse.urlsplit(base)
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=10)
        cookies = "; ".join(f"{c.name}={c.value}" for c in client.jar) if client else ""
        lines = [
            "GET /ws HTTP/1.1",
            f"Host: {parts.netloc}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {base64.b64encode(secrets.token_bytes(16)).decode()}",
            "Sec-WebSocket-Version: 13",
            f"Origin: {origin}",
            f"Cookie: {cookies}",
        ]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        self.accepted = head.startswith(b"HTTP/1.1 101")

    def _exactly(self, count):
        while len(self.buffer) < count:
            left = self.deadline - time.monotonic()
            if left <= 0:  # pings keep arriving, so one socket timeout would never expire
                raise TimeoutError
            self.sock.settimeout(left)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise Failure("the socket was dropped")
            self.buffer += chunk
        taken, self.buffer = self.buffer[:count], self.buffer[count:]
        return taken

    def receive(self, timeout=5):
        """("event", dict), ("close", code) or ("silent", None) after `timeout` seconds."""
        self.deadline = time.monotonic() + timeout
        try:
            while True:
                first, second = self._exactly(2)
                length = second & 0x7F
                if length >= 126:
                    length = int.from_bytes(self._exactly(2 if length == 126 else 8))
                payload = self._exactly(length)
                opcode = first & 0x0F
                if opcode == 1:
                    return "event", json.loads(payload)
                if opcode == 8:
                    return "close", int.from_bytes(payload[:2])
                if opcode == 9:  # ping: a masked pong
                    self.sock.sendall(bytes([0x8A, 0x80 | len(payload), 0, 0, 0, 0]) + payload)
        except TimeoutError:
            return "silent", None

    def close(self):
        self.sock.close()


def png(width=640, height=480, seed=0):
    """A small valid RGB PNG with a gradient, generated here so there is no fixture file."""

    def chunk(kind, payload):
        crc = zlib.crc32(kind + payload)
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    row = bytes((x + seed * 37) % 256 for x in range(width * 3))
    raw = b"".join(b"\x00" + row[y % 64 :] + row[: y % 64] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def check(condition, message):
    if not condition:
        raise Failure(message)


class Smoke:
    def __init__(self, args):
        self.args = args
        self.base = args.base_url
        self.run = secrets.token_hex(4)
        self.password = f"correct horse battery {secrets.token_hex(6)}"
        self.results = []
        self.ctx = {}

    # -- helpers ---------------------------------------------------------------------------

    def flow(self, name, fn, needs=()):
        missing = [key for key in needs if key not in self.ctx]
        if missing:
            self.results.append((name, "SKIP", f"needs {', '.join(missing)}"))
            print(f"SKIP  {name} (needs {', '.join(missing)})", flush=True)
            return
        started = time.monotonic()
        try:
            note = fn() or ""
            status = "PASS"
        except Failure as error:
            note, status = str(error), "FAIL"
        except Exception as error:
            note, status = f"{type(error).__name__}: {error}", "FAIL"
            traceback.print_exc()
        self.results.append((name, status, note))
        print(f"{status}  {name} [{time.monotonic() - started:.1f}s] {note}", flush=True)

    def mail_token(self, address, subject_part):
        query = urllib.parse.urlencode({"query": f'to:"{address}"'})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            url = f"{self.args.mailpit}/api/v1/search?{query}"
            with urllib.request.urlopen(url, timeout=10) as answer:  # noqa: S310
                found = json.load(answer)["messages"]
            for item in found:
                if subject_part in item["Subject"]:
                    url = f"{self.args.mailpit}/api/v1/message/{item['ID']}"
                    with urllib.request.urlopen(url, timeout=10) as answer:  # noqa: S310
                        text = json.load(answer)["Text"]
                    match = re.search(r"https?://\S+[?&]token=([^\s&]+)", text)
                    check(match, "mail has no token link")
                    return urllib.parse.unquote(match.group(1))
            time.sleep(0.3)
        raise Failure(f"no '{subject_part}' mail arrived")

    def account(self, name, agency=False):
        email = f"smoke-{self.run}-{name}@smoke.estate-ai.invalid"
        client = Client(self.base)
        if agency:
            body = {
                "agency_name": f"Smoke Estates {self.run}",
                "email": email,
                "phone": "+923001234567",
                "office_address": "Office 1, Smoke Plaza, Smoke Road",
                "city_id": self.ctx["city"],
                "password": self.password,
            }
            client.post("/registrations/agencies", body, expect=202)
        else:
            body = {
                "full_name": f"Smoke Person {name}",
                "email": email,
                "phone": "+923001234567",
                "password": self.password,
            }
            client.post("/registrations/individuals", body, expect=202)
        token = self.mail_token(email, "Verify")
        client.post("/email-verifications/confirm", {"token": token})
        client.sign_in(email, self.password)
        me = client.get("/me").json()
        return client, email, me

    def listing(self, client, listing_id):
        return client.get(f"/listings/{listing_id}").json()

    def patch(self, client, listing_id, fields, expect=200):
        revision = self.listing(client, listing_id)["revision"]
        return client.call(
            "PATCH",
            f"/listings/{listing_id}",
            {"base_revision": revision, "fields": fields},
            expect,
        ).json()

    def publish(self, client, listing_id, action="publish", expect=200):
        revision = self.listing(client, listing_id)["revision"]
        return client.post(
            f"/listings/{listing_id}/{action}",
            {"base_revision": revision},
            expect,
            {"Idempotency-Key": str(uuid.uuid4())},
        ).json()

    def status(self, client, listing_id, status):
        revision = self.listing(client, listing_id)["revision"]
        body = {"base_revision": revision, "status": status}
        return client.post(f"/listings/{listing_id}/status", body).json()

    def house_fields(self, title, **extra):
        return {
            "title": title,
            "description": "A bright family house with a small lawn and a covered porch.",
            "asking_price": "25000000",
            "price_basis": "total",
            "area_value": "5",
            "area_unit": "marla",
            "marla_basis": self.ctx["marla_basis"],
            "bedrooms": 3,
            "bathrooms": 3,
            "city": self.ctx["city"],
            "locality": self.ctx["locality"],
            **extra,
        }

    def ready_draft(self, client, title):
        body = {"purpose": "sale", "category": "house", "subtype": "house"}
        listing_id = client.post("/listings", body, 201).json()["id"]
        self.patch(client, listing_id, self.house_fields(title))
        return listing_id

    # -- flows -----------------------------------------------------------------------------

    def reference(self):
        visitor = Client(self.base)
        visitor.get("/health")
        registry = visitor.get("/registry").json()
        fields = {item["id"]: item for item in registry["fields"]}
        self.ctx["marla_basis"] = fields["marla_basis"]["allowed_values"][0]
        self.ctx["city"] = visitor.get("/reference/cities").json()["items"][0]["id"]
        path = f"/reference/cities/{self.ctx['city']}/localities"
        names = {item["name"]: item["id"] for item in visitor.get(path).json()["items"]}
        self.ctx["locality"] = names.get("Gulshan-e-Iqbal") or next(iter(names.values()))
        self.ctx["locality2"] = names.get("North Nazimabad") or list(names.values())[1]
        visitor.get("/reference/amenities")
        visitor.get("/public/filters")

    def register_individual(self):
        self.ctx["owner"], self.ctx["owner_email"], me = self.account("owner")
        self.ctx["owner_me"] = me
        self.ctx["buyer"], self.ctx["buyer_email"], _ = self.account("buyer")

    def register_agency(self):
        self.ctx["agency"], self.ctx["agency_email"], me = self.account("agency", agency=True)
        self.ctx["agency_me"] = me

    def profile(self):
        owner = self.ctx["owner"]
        owner.get("/me/profile")
        owner.call("PATCH", "/me/profile", {"full_name": "Smoke Owner Renamed"}, 200)
        owner.upload("/me/profile/image", png(400, 400), "PUT", expect=200)
        owner.get("/me/profile/image/file")
        agency = self.ctx.get("agency")
        if agency:
            agency.get("/me/agency-profile")
            agency.call("PATCH", "/me/agency-profile", {"description": "Homes in Karachi."}, 200)
            agency.upload("/me/agency-profile/image", png(400, 400, 1), "PUT", expect=200)
            agency.get("/me/agency-profile/image/file")

    def draft_and_edit(self):
        owner = self.ctx["owner"]
        body = {"purpose": "sale", "category": "house", "subtype": "house"}
        listing_id = owner.post("/listings", body, 201).json()["id"]
        self.ctx["listing"] = listing_id
        self.patch(owner, listing_id, self.house_fields(f"Smoke house {self.run} in Gulshan"))
        self.patch(
            owner,
            listing_id,
            {
                "construction_year": 2018,
                "furnishing": self.first_choice("furnishing"),
                "parking_spaces": 1,
                "covered_area_sqft": "1400",
                "drawing_rooms": 1,
                "price_negotiable": True,
            },
        )
        view = self.patch(
            owner,
            listing_id,
            {
                "phase": "Phase 1",
                "block": "Block 7",
                "street": "Smoke Street 12",
                "house_number": "45-B",
                "latitude": "24.92",
                "longitude": "67.09",
            },
        )
        view = self.patch(
            owner,
            listing_id,
            {"amenities": {"electricity_backup": "present", "nearby_mosque": "present"}},
        )
        check(view["fields"]["amenities"], "amenities were not saved")
        stale = owner.call(
            "PATCH",
            f"/listings/{listing_id}",
            {"base_revision": 1, "fields": {"bedrooms": 4}},
        )
        check(stale.status == 409, f"stale revision answered {stale.status}")
        owner.get("/me/listings")
        owner.get("/me/listings/counts")

    def first_choice(self, field):
        registry = Client(self.base).get("/registry").json()
        return next(f for f in registry["fields"] if f["id"] == field)["allowed_values"][0]

    def images(self):
        owner, listing_id = self.ctx["owner"], self.ctx["listing"]
        path = f"/listings/{listing_id}/images"
        statuses, ids = [], []
        for number in range(4):  # one after another, as the frontend does
            answer = owner.upload(path, png(2000, 1500, number))
            statuses.append(answer.status)
        check(statuses == [201] * 4, f"sequential uploads answered {statuses}")
        view = self.listing(owner, listing_id)
        ids = [item["id"] for item in view["media"]]
        owner.call(
            "PATCH",
            path,
            {"base_revision": view["revision"], "order": ids[::-1], "cover_id": ids[1]},
            200,
        )
        owner.get(f"{path}/{ids[0]}/file")
        owner.call("DELETE", f"{path}/{ids[3]}", expect=204)
        with concurrent.futures.ThreadPoolExecutor(3) as pool:  # three at once fit the executor
            owner.token = owner.token or owner.get("/csrf").json()["csrf_token"]
            answers = list(pool.map(lambda n: owner.upload(path, png(2000, 1500, n)), range(3)))
        parallel = [answer.status for answer in answers]
        check(parallel == [201] * 3, f"three parallel uploads answered {parallel}")
        self.ctx["image"] = ids[1]

    def preview_and_estimate(self):
        owner, listing_id = self.ctx["owner"], self.ctx["listing"]
        owner.get(f"/listings/{listing_id}/preview")
        mine = owner.post(f"/listings/{listing_id}/estimate").json()
        body = {
            "purpose": "sale",
            "category": "house",
            "subtype": "house",
            "fields": {
                "locality": self.ctx["locality"],
                "area_value": "5",
                "area_unit": "marla",
                "marla_basis": self.ctx["marla_basis"],
                "bedrooms": 3,
                "bathrooms": 3,
            },
        }
        entered = owner.post("/estimates", body).json()
        return f"listing: {self._estimate(mine)}; entered: {self._estimate(entered)}"

    @staticmethod
    def _estimate(answer):
        return f"{answer.get('status')} {answer.get('estimated_value') or answer.get('reason')}"

    def publish_around_uploads(self):
        owner, listing_id = self.ctx["owner"], self.ctx["listing"]
        # upload then publish at once
        owner.upload(f"/listings/{listing_id}/images", png(2000, 1500, 9), expect=201)
        view = self.publish(owner, listing_id)
        check(view["status"] == "published", f"status is {view['status']}")
        # publishing started a background estimate; uploads right after must still be accepted
        other = self.ready_draft(owner, f"Second smoke house {self.run}")
        path = f"/listings/{other}/images"
        first = owner.upload(path, png(2000, 1500, 1))
        check(first.status == 201, f"upload right after publish answered {first.status}")
        self.publish(owner, other)
        third = self.ready_draft(owner, f"Third smoke house {self.run}")
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            owner.token = owner.token or owner.get("/csrf").json()["csrf_token"]
            path = f"/listings/{third}/images"
            answers = list(pool.map(lambda n: owner.upload(path, png(2000, 1500, n)), range(2)))
        statuses = [answer.status for answer in answers]
        check(statuses == [201, 201], f"two uploads right after publish answered {statuses}")
        self.ctx["second"], self.ctx["third"] = other, third
        agency = self.ctx.get("agency")
        if agency:
            mine = self.ready_draft(agency, f"Agency smoke house {self.run}")
            agency.upload(f"/listings/{mine}/images", png(), expect=201)
            self.publish(agency, mine)
            self.ctx["agency_listing"] = mine
        self.ctx["published"] = listing_id

    def search(self):
        """Search by description (Story 6.1). The stub understands nothing, so it must fall back to
        the filters; with a real provider the filters it returns must work on the public list."""
        visitor, buyer = Client(self.base), self.ctx["buyer"]
        statuses = []
        for client, who in ((visitor, "visitor"), (buyer, "buyer")):
            query = "a house for sale with at least 3 bedrooms under 3 crore"
            body = client.post("/public/search", {"query": query}).json()
            check(body["status"] in ("interpreted", "fallback"), f"{who}: {body}")
            check(body["status"] == "interpreted" or body["message"], f"{who}: no message")
            client.get(f"/public/listings?{urllib.parse.urlencode(body['params'])}")
            statuses.append(body["status"])
        visitor.post("/public/search", {"query": " "}, expect=400)
        heard = visitor.upload(  # silence: the stub answers its fixed text, a model may hear none
            "/public/search/transcription",
            b"RIFF$\x7d\x00\x00WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data"
            + struct.pack("<I", 32000)
            + b"\x00" * 32000,
            expect=200,
            name="audio",
            filename="smoke.wav",
            content_type="audio/wav",
        ).json()
        check(heard["status"] in ("transcribed", "fallback"), f"voice: {heard}")
        statuses.append(f"voice {heard['status']}")
        return f"visitor and account: {', '.join(statuses)}"

    def inquiry(self):
        """Message seller (Story 7.1): send, retry, reply, and nobody else reads it."""
        owner, buyer = self.ctx["owner"], self.ctx["buyer"]
        listing_id = self.ctx["published"]
        body = {"listing_id": listing_id, "body": "Is it still available?"}
        key = {"Idempotency-Key": str(uuid.uuid4())}
        sent = buyer.post("/conversations", body, (200, 201), key).json()
        conversation = sent["conversation"]["id"]
        again = buyer.post("/conversations", body, 200, key).json()
        check(again["message"]["id"] == sent["message"]["id"], "a retry made a second message")
        owner.post("/conversations", body, 409, {"Idempotency-Key": str(uuid.uuid4())})
        path = f"/conversations/{conversation}/messages"
        owner.post(path, {"body": "Yes it is."}, 201, {"Idempotency-Key": str(uuid.uuid4())})
        seen = buyer.get(path).json()["results"]
        check([m["mine"] for m in seen][-2:] == [True, False], f"history: {seen}")
        found = buyer.get(f"/conversations?listing_id={listing_id}").json()["results"]
        check([c["id"] for c in found] == [conversation], "the inquiry was not found again")
        check("+92300" not in json.dumps([found, seen]), "a phone number in a payload")
        Client(self.base).get(path, expect=401)
        stranger = self.ctx.get("agency")
        if stranger:
            stranger.get(path, expect=404)
        self.ctx["conversation"] = conversation
        return f"{len(seen)} messages"

    def live(self):
        """Live delivery (Stories 7.2 and 7.3): only the participants hear of a message, within a
        second; a foreign Origin and a Visitor are refused; signing out closes the socket."""
        owner, buyer = Client(self.base), self.ctx["buyer"]
        owner.sign_in(self.ctx["owner_email"], self.password)
        conversation, origin = self.ctx["conversation"], self.args.origin
        for client, sent_origin, what in (
            (None, origin, "a Visitor"),
            (owner, "http://evil.example", "a foreign Origin"),
        ):
            refused = LiveSocket(self.base, client, sent_origin)
            check(not refused.accepted, f"{what} was given a socket")
            refused.close()
        sockets = {"owner": LiveSocket(self.base, owner, origin)}
        if self.ctx.get("agency"):
            sockets["stranger"] = LiveSocket(self.base, self.ctx["agency"], origin)
        try:
            for name, one in sockets.items():
                check(one.accepted, f"{name}: the handshake was refused")
                check(one.receive() == ("event", {"type": "ready"}), f"{name}: not ready")
            started = time.monotonic()
            saved = buyer.post(
                f"/conversations/{conversation}/messages",
                {"body": "Are you there?"},
                201,
                {"Idempotency-Key": str(uuid.uuid4())},
            ).json()
            kind, event = sockets["owner"].receive()
            took = time.monotonic() - started
            check(kind == "event" and event["message_id"] == saved["id"], f"owner got {event}")
            check("body" not in event and took < 1, f"after {took:.2f}s: {event}")
            if "stranger" in sockets:
                heard = sockets["stranger"].receive(1)
                check(heard == ("silent", None), f"a stranger heard {heard}")
            # longer than the proxy's 30 s and Daphne's 20 s ping: an idle socket stays open
            if self.args.live_idle:
                idle = sockets["owner"].receive(self.args.live_idle)
                check(idle == ("silent", None), f"an idle socket got {idle}")
            owner.call("DELETE", "/session", expect=204)
            ended = sockets["owner"].receive()
            check(ended == ("close", 4401), f"after sign out: {ended}")
        finally:
            for one in sockets.values():
                one.close()
        return f"announced in {took * 1000:.0f} ms"

    def browse(self):
        visitor, buyer = Client(self.base), self.ctx["buyer"]
        listing_id = self.ctx["published"]
        for client, who in ((visitor, "visitor"), (buyer, "buyer")):
            found = client.get("/public/listings?sort=newest").json()
            check(any(i["id"] == listing_id for i in found["results"]), f"{who}: not in browse")
            query = (
                f"purpose=sale&category=house&city={self.ctx['city']}&locality={self.ctx['locality']}"
                "&bedrooms_min=3&asking_price_max=30000000&amenities=electricity_backup"
                "&sort=price_asc"
            )
            found = client.get(f"/public/listings?{query}").json()
            check(any(i["id"] == listing_id for i in found["results"]), f"{who}: not in filtered")
            client.get("/public/listings?prefer_bedrooms_min=3&sort=relevance&limit=2")
            detail = client.get(f"/public/listings/{listing_id}")
            for secret in (b"Smoke Street 12", b"45-B", b"+923001234567", b"@smoke."):
                check(secret not in detail.body, f"{who}: detail leaks {secret!r}")
            body = detail.json()
            media = body.get("media") or []
            check(media, f"{who}: no media in detail")
            client.get(f"/listings/{listing_id}/images/{media[0]['id']}/file")
            deadline = time.monotonic() + 30
            while True:
                estimate = client.get(f"/public/listings/{listing_id}/estimate").json()
                if estimate.get("status") == "available" or time.monotonic() > deadline:
                    break
                time.sleep(1)
        agency_listing = self.ctx.get("agency_listing")
        if agency_listing:
            shown = visitor.get(f"/public/listings/{agency_listing}").json()
            agency_id = shown["advertiser"]["agency_id"]
            visitor.get(f"/public/agencies/{agency_id}")
            found = visitor.get(f"/public/agencies/{agency_id}/listings").json()
            check(found["results"], "agency page lists nothing")
            visitor.get(f"/agencies/{agency_id}/logo")
            self.ctx["agency_id"] = agency_id
        return f"public estimate: {self._estimate(estimate)}"

    def favourites(self):
        buyer, listing_id = self.ctx["buyer"], self.ctx["published"]
        Client(self.base).call("PUT", f"/me/favourites/{listing_id}", expect=401)
        buyer.call("PUT", f"/me/favourites/{listing_id}", expect=204)
        buyer.call("PUT", f"/me/favourites/{listing_id}", expect=204)
        check(listing_id in json.dumps(buyer.get("/me/favourites").json()), "not in favourites")
        check(listing_id in json.dumps(buyer.get("/me/favourites/ids").json()), "id missing")
        buyer.call("DELETE", f"/me/favourites/{listing_id}", expect=204)
        buyer.call("PUT", f"/me/favourites/{listing_id}", expect=204)
        buyer.get(f"/listings/{listing_id}", expect=404)
        buyer.get(f"/listings/{listing_id}/preview", expect=404)

    def edit_and_lifecycle(self):
        owner, listing_id = self.ctx["owner"], self.ctx["published"]
        self.patch(owner, listing_id, {"asking_price": "26000000"})
        shown = Client(self.base).get(f"/public/listings/{listing_id}").json()
        check("26000000" in json.dumps(shown), "public detail did not take the new price")
        owner.upload(f"/listings/{listing_id}/images", png(800, 600, 3), expect=201)
        self.status(owner, listing_id, "archived")
        Client(self.base).get(f"/public/listings/{listing_id}", expect=404)
        self.ctx["buyer"].get("/me/favourites")
        self.publish(owner, listing_id, "republish")
        Client(self.base).get(f"/public/listings/{listing_id}")
        window = owner.get(f"/listings/{listing_id}/correction").json()
        revision = self.listing(owner, listing_id)["revision"]
        owner.post(
            f"/listings/{listing_id}/correction",
            {"base_revision": revision, "fields": {"locality": self.ctx["locality2"]}},
        )
        self.status(owner, listing_id, "sold")
        Client(self.base).get(f"/public/listings/{listing_id}", expect=404)
        owner.get("/me/listings?status=sold")
        owner.call("DELETE", f"/listings/{self.ctx['third']}", expect=204)
        return f"correction window: {window}"

    def notifications_and_quota(self):
        owner = self.ctx["owner"]
        body = {"current_password": self.password, "new_password": self.password + " again"}
        owner.post("/me/password", body)
        owner.token = None
        deadline = time.monotonic() + 10
        while True:
            items = owner.get("/notifications").json()["results"]
            if items or time.monotonic() > deadline:
                break
            time.sleep(0.3)
        check(items, "no notification after a password change")
        count = owner.get("/notifications/unread-count").json()
        owner.post(f"/notifications/{items[0]['id']}/read", expect=200)
        owner.post("/notifications/read-all", expect=200)
        allowance = owner.get("/me/publication-allowance").json()
        return f"{len(items)} notification(s), unread {count}, allowance {allowance}"

    def password_reset(self):
        email = self.ctx["buyer_email"]
        client = Client(self.base)
        client.post("/password-reset-requests", {"email": email}, 202)
        token = self.mail_token(email, "Reset")
        password = self.password + " reset"
        client.post("/password-resets/confirm", {"token": token, "password": password})
        client.sign_in(email, password)
        self.ctx["buyer"] = client
        client.call("DELETE", "/session", expect=204)
        client.token = None
        client.sign_in(email, password)

    def admin_credentials(self):
        email = os.environ.get("SMOKE_ADMIN_EMAIL")
        password = os.environ.get("SMOKE_ADMIN_PASSWORD")
        if email and password:
            return email, password
        if not self.args.provision_admin:
            return None
        if os.environ.get("POSTGRES_DB", "estate_ai") == "estate_ai":
            raise Failure("--provision-admin needs POSTGRES_DB set to a throwaway database")
        email = f"smoke-{self.run}-admin@smoke.estate-ai.invalid"
        password = f"correct horse battery {secrets.token_hex(8)}"
        command = [*self.args.manage_python.split(), "manage.py", "provision_admin", "create"]
        done = subprocess.run(  # noqa: S603 - fixed command, our own arguments
            [*command, "--email", email, "--password-stdin"],
            input=password + "\n",
            text=True,
            capture_output=True,
            cwd=ROOT,
        )
        check(done.returncode == 0, f"provision_admin: {done.stderr.strip()[-300:]}")
        return email, password

    def admin(self):
        credentials = self.admin_credentials()
        if credentials is None:
            return "skipped: no administrator credentials"
        admin = Client(self.base)
        admin.sign_in(*credentials, path="/admin-session")
        admin.get("/admin/me")
        self.ctx["owner"].get("/admin/me", expect=404)
        policy = admin.get("/admin/quota-policy").json()
        before = self._limit(policy, "individual")
        admin.call("PUT", "/admin/quota-policy/individual", {"monthly_limit": before + 1}, 200)
        try:
            check(
                self._limit(admin.get("/admin/quota-policy").json(), "individual") == before + 1,
                "quota limit did not change",
            )
            admin.get("/admin/quota-usage")
        finally:
            admin.call("PUT", "/admin/quota-policy/individual", {"monthly_limit": before}, 200)
        found = admin.post(
            "/admin/accounts/lookup", {"reason": "smoke test", "email": self.ctx["owner_email"]}
        ).json()
        account_id = (found.get("account") or found).get("id") or found.get("account_id")
        check(account_id, f"no account id in lookup: {list(found)}")
        shown = self.ctx.get("second")
        admin.post(f"/admin/accounts/{account_id}/suspend", {"reason": "smoke test"})
        try:
            if shown:
                Client(self.base).get(f"/public/listings/{shown}", expect=404)
            body = {"purpose": "sale", "category": "house", "subtype": "house"}
            self.ctx["owner"].post("/listings", body, (401, 403))
        finally:
            admin.post(f"/admin/accounts/{account_id}/reactivate", {"reason": "smoke test"})
        if shown:
            Client(self.base).get(f"/public/listings/{shown}")
        admin.call("DELETE", "/session", expect=204)

    @staticmethod
    def _limit(policy, account_type):
        for item in policy.get("items") or policy.get("policies") or []:
            if item.get("account_type") == account_type:
                return item.get("monthly_limit")
        raise Failure(f"no quota policy for {account_type}: {policy}")

    def assistant(self):
        owner = self.ctx.get("assistant_owner")
        if owner is None:
            owner = self.ctx["buyer"]
        body = {"purpose": "sale", "category": "house", "subtype": "house"}
        listing_id = owner.post("/listings", body, 201).json()["id"]
        session = owner.post("/assistant/sessions", {"listing_id": listing_id}, (200, 201)).json()
        session_id = session["id"]
        messages = [
            "It is a 5 marla house in Gulshan-e-Iqbal with 3 bedrooms and 3 bathrooms.",
            "The asking price is 2.5 crore. It has electricity backup and a mosque nearby.",
            "What does marla mean?",
            "ghar 2015 mein bana tha, parking mein do gaariyan aa sakti hain",
        ]
        for message in messages:
            started = time.monotonic()
            answer = owner.post(
                f"/assistant/sessions/{session_id}/turns",
                {"message": message, "client_turn_id": str(uuid.uuid4())},
            ).json()
            took = time.monotonic() - started
            if self.args.verbose:
                shown = {k: v for k, v in answer.items() if k != "listing"}
                print(f"  turn {took:.2f}s: {message}\n    {json.dumps(shown, ensure_ascii=False)}")
        view = self.listing(owner, listing_id)
        if self.args.verbose:
            filled = {k: v for k, v in view["fields"].items() if v not in (None, "", {})}
            print(f"  listing fields: {json.dumps(filled, ensure_ascii=False)}")
        pending = [
            name
            for name, stamp in (view.get("provenance") or {}).items()
            if not stamp.get("confirmed")
        ]
        if pending:
            owner.post(
                f"/listings/{listing_id}/confirm",
                {"base_revision": view["revision"], "fields": pending},
            )
        owner.get(f"/assistant/sessions/{session_id}")
        # a description needs confirmed details; the stub extracts little, so fill what is missing
        current = self.listing(owner, listing_id)["fields"]
        manual = self.house_fields(f"Assistant smoke house {self.run}")
        missing = {k: v for k, v in manual.items() if k != "description" and not current.get(k)}
        if missing:
            self.patch(owner, listing_id, missing)
        started = time.monotonic()
        revision = self.listing(owner, listing_id)["revision"]
        described = owner.post(
            f"/assistant/sessions/{session_id}/description", {"base_revision": revision}
        ).json()
        if self.args.verbose:
            shown = {k: v for k, v in described.items() if k not in ("listing", "session")}
            print(
                f"  description {time.monotonic() - started:.2f}s: "
                f"{json.dumps(shown, ensure_ascii=False)}"
            )
            saved = self.listing(owner, listing_id)["fields"]["description"]
            print(f"  saved description: {saved}")
        return f"extracted and confirmed: {sorted(pending)}"

    # -- run -------------------------------------------------------------------------------

    def main(self):
        self.flow("reference data", self.reference)
        if self.args.only_assistant:

            def setup():
                self.ctx["assistant_owner"], _, _ = self.account("assistant")

            self.flow("register and verify", setup, ["city"])
            self.flow("assistant", self.assistant, ["assistant_owner"])
            return self.summary()
        self.flow("register/verify/sign in: individuals", self.register_individual, ["city"])
        self.flow("register/verify/sign in: agency", self.register_agency, ["city"])
        self.flow("profile and pictures", self.profile, ["owner"])
        self.flow("draft, all sections, stale revision", self.draft_and_edit, ["owner"])
        self.flow("image uploads, order, cover, delete", self.images, ["listing"])
        self.flow("preview and estimates", self.preview_and_estimate, ["listing"])
        self.flow("publish around uploads", self.publish_around_uploads, ["image"])
        self.flow("browse/filter/detail/agency page", self.browse, ["published", "buyer"])
        self.flow("search by description", self.search, ["buyer"])
        self.flow("inquiry and reply", self.inquiry, ["published", "buyer"])
        self.flow("live delivery", self.live, ["conversation"])
        self.flow("favourites and ownership", self.favourites, ["published", "buyer"])
        self.flow(
            "edit/archive/republish/correct/sold", self.edit_and_lifecycle, ["published", "third"]
        )
        self.flow("notifications and allowance", self.notifications_and_quota, ["owner"])
        self.flow("password reset and sign out", self.password_reset, ["buyer"])
        self.flow("admin: quota, lookup, suspend", self.admin, ["owner"])
        self.flow("assistant", self.assistant, ["buyer", "marla_basis"])
        return self.summary()

    def summary(self):
        failed = [name for name, status, _ in self.results if status != "PASS"]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} flows passed")
        return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument("--provision-admin", action="store_true")
    parser.add_argument(
        "--origin", default="http://localhost:3000", help="a configured origin, for /ws"
    )
    parser.add_argument("--live-idle", type=float, default=0, help="hold the idle socket (s)")
    parser.add_argument("--manage-python", default="uv run python")
    parser.add_argument("--only-assistant", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="print assistant answers")
    args = parser.parse_args(argv)
    for url in (args.base_url, args.mailpit):
        if urllib.parse.urlsplit(url).hostname not in LOOPBACK:
            parser.error("only loopback URLs: this creates accounts and reads mail")
    return Smoke(args).main()


if __name__ == "__main__":
    sys.exit(main())
