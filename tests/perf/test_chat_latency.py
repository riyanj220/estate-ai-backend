"""Story 7.4 (SM-7, NFR-1): chat delivery stays under 1 s at p95 while five AI or image requests
are in flight. Not part of the default run:

    uv run pytest -m perf -s tests/perf/test_chat_latency.py

The real ASGI application on one event loop, as under Daphne (one process), without the network.
Delivery is timed from just before the sender's POST until the online recipient has had the socket
event AND fetched the Message over REST, which is when it can be shown. The load is three image
uploads (real re-encoding on the runtime executor) and two searches by description (AI gateway,
the stub provider made to take 2 s), each repeated for as long as Messages are being sent.
"""

import asyncio
import io
import json
import math
import time
import uuid

import pytest
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.testing import HttpCommunicator, WebsocketCommunicator
from django.db import connections
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from PIL import Image

from apps.identity.sessions import COOKIE_NAME
from apps.kernel import runtime
from apps.kernel.ai.stub import stub
from apps.kernel.models import RateLimitPolicy
from apps.presentation.realtime import consumers
from config.asgi import application
from tests.listings.support import seed_cap, seed_media_policies, user
from tests.messaging.test_conversations import Api, publish_for

pytestmark = pytest.mark.perf

BUDGET_MS = 1000
MESSAGES = 50
AI_SECONDS = 2.0
ORIGIN = b"http://localhost:3000"


def photo() -> bytes:
    """A 12-megapixel JPEG with detail, so the re-encode costs what a phone photo costs."""
    noise = Image.effect_noise((1000, 750), 40).convert("RGB").resize((4000, 3000))
    out = io.BytesIO()
    noise.save(out, "JPEG", quality=85)
    return out.getvalue()


def credentials(api) -> dict:
    token = api.client.get("/api/v1/csrf").json()["csrf_token"]
    cookie = "; ".join(f"{k}={v.value}" for k, v in api.client.cookies.items())
    return {"cookie": cookie, "x-csrftoken": token}


async def request(method, path, who, body=b"", content_type="application/json", extra=()):
    headers = [(b"host", b"testserver")] + [(k.encode(), v.encode()) for k, v in who.items()]
    if body:
        headers += [
            (b"content-type", content_type.encode()),
            (b"content-length", str(len(body)).encode()),
        ]
    call = HttpCommunicator(application, method, path, body=body, headers=[*headers, *extra])
    answer = await call.get_response(timeout=60)
    await call.wait(timeout=60)
    return answer["status"], answer["body"]


def percentile(samples, share):
    return sorted(samples)[math.ceil(share * len(samples)) - 1]


def prepare():
    from django.test import Client

    from apps.listings import services as listings

    seed_cap(200)
    seed_media_policies()
    for scope in ("message_send", "conversation_new", "image_upload", "ai_search_visitor"):
        RateLimitPolicy.objects.update_or_create(
            scope=scope,
            defaults={
                "limit": 100_000,
                "window_seconds": 3600,
                "key_kind": "client" if scope.endswith("visitor") else "account",
                "note": "t",
            },
        )
    seller, buyer, uploader = Api(name="Hira Seller"), Api(), Api(name="Uploader")
    listing = publish_for(seller.account)
    drafts = [
        str(listings.create_draft(user(uploader.account), "sale", "house", "house").id)
        for _ in range(3)
    ]
    started = buyer.start(listing)
    assert started.status_code == 201, started.content
    visitor = Client()
    token = visitor.get("/api/v1/csrf").json()["csrf_token"]
    anonymous = {
        "cookie": "; ".join(f"{k}={v.value}" for k, v in visitor.cookies.items()),
        "x-csrftoken": token,
    }
    return (
        credentials(seller),
        seller.client.cookies[COOKIE_NAME].value,
        credentials(buyer),
        credentials(uploader),
        anonymous,
        drafts,
        started.json()["conversation"]["id"],
    )


@pytest.mark.django_db(transaction=True)
def test_chat_stays_fast_under_ai_and_image_load(settings):
    settings.AI_DAILY_BUDGET = 100_000
    consumers._open.clear()
    runtime.shutdown()
    stub.reset()
    stub.delay = AI_SECONDS
    image = photo()
    done = {"image": 0, "image_busy": 0, "ai": 0}
    in_flight = {"now": 0, "seen": []}

    async def scenario():
        (
            seller,
            token,
            buyer,
            uploader,
            anonymous,
            drafts,
            conversation,
        ) = await database_sync_to_async(prepare)()
        stop = asyncio.Event()

        async def counted(awaitable):
            in_flight["now"] += 1
            try:
                return await awaitable
            finally:
                in_flight["now"] -= 1

        async def upload(draft):
            body = encode_multipart(BOUNDARY, {"file": named(image)})
            while not stop.is_set():
                status, answer = await counted(
                    request(
                        "POST",
                        f"/api/v1/listings/{draft}/images",
                        uploader,
                        body,
                        MULTIPART_CONTENT,
                    )
                )
                if status == 201:
                    done["image"] += 1
                    made = json.loads(answer)
                    image_id = (made.get("image") or made)["id"]
                    await request("DELETE", f"/api/v1/listings/{draft}/images/{image_id}", uploader)
                else:
                    assert status in (409, 429, 503), (status, answer)
                    done["image_busy"] += 1
                    await asyncio.sleep(0.05)  # the executor was full: try again

        async def search():
            body = json.dumps({"query": "a house for sale near a park"}).encode()
            while not stop.is_set():
                status, answer = await counted(
                    request("POST", "/api/v1/public/search", anonymous, body)
                )
                assert status == 200, (status, answer)
                done["ai"] += 1

        socket = WebsocketCommunicator(
            application,
            "/ws",
            headers=[(b"origin", ORIGIN), (b"cookie", f"{COOKIE_NAME}={token}".encode())],
        )
        assert (await socket.connect())[0]
        assert await socket.receive_json_from() == {"type": "ready"}
        load = [asyncio.create_task(upload(d)) for d in drafts]
        load += [asyncio.create_task(search()) for _ in range(2)]
        await asyncio.sleep(1)

        announced, shown = [], []
        for number in range(MESSAGES):
            body = json.dumps({"body": f"Message {number}"}).encode()
            flying = in_flight["now"]
            started = time.perf_counter()
            sending = asyncio.create_task(
                request(
                    "POST",
                    f"/api/v1/conversations/{conversation}/messages",
                    buyer,
                    body,
                    extra=[(b"idempotency-key", str(uuid.uuid4()).encode())],
                )
            )
            event = await socket.receive_json_from(timeout=30)
            announced.append((time.perf_counter() - started) * 1000)
            status, answer = await request(
                "GET",
                f"/api/v1/conversations/{conversation}/messages?after={event['seq'] - 1}",
                seller,
            )
            shown.append((time.perf_counter() - started) * 1000)
            assert status == 200 and json.loads(answer)["results"][0]["seq"] == event["seq"]
            assert (await sending)[0] == 201
            in_flight["seen"].append(flying)
            await asyncio.sleep(0.1)

        stop.set()
        await asyncio.gather(*load)
        await socket.disconnect()
        await sync_to_async(connections.close_all)()  # or the test database stays in use
        return announced, shown

    try:
        announced, shown = asyncio.run(scenario())
    finally:
        stub.reset()
        runtime.shutdown()

    for label, samples in (("event received", announced), ("event + fetch", shown)):
        print(
            f"{label:<16} p50 {percentile(samples, 0.5):6.1f} ms   "
            f"p95 {percentile(samples, 0.95):6.1f} ms   max {max(samples):6.1f} ms"
        )
    seen = in_flight["seen"]
    typical = percentile(seen, 0.5)
    print(f"load completed: {done}")
    print(f"AI/image requests in flight at send: {min(seen)} to {max(seen)}, median {typical}")
    assert done["image"] > 0 and done["ai"] > 0, done
    assert typical >= 4 and max(seen) == 5, "the load was not sustained"
    assert percentile(shown, 0.95) < BUDGET_MS


def named(data: bytes):
    file = io.BytesIO(data)
    file.name = "photo.jpg"
    return file
