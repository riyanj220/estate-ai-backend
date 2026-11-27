"""Stories 7.2 and 7.3: the receive-only socket, driven through the real ASGI application, next to
the REST routes it accompanies."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.management import call_command
from django.db import connections
from jsonschema import Draft202012Validator

from apps.identity import services as identity
from apps.identity.sessions import COOKIE_NAME
from apps.kernel.actor import Actor
from apps.messaging.models import Message
from apps.presentation.realtime import consumers, protocol
from config.asgi import application
from tests.listings.support import PASSWORD, random_email
from tests.messaging.test_conversations import Api, publish_for

ORIGIN = "http://localhost:3000"
ROOT = Path(__file__).resolve().parents[2]
SECRET = "unmistakable-message-body-4471"


@pytest.fixture(autouse=True)
def fresh_connections():
    consumers._open.clear()
    yield
    consumers._open.clear()


def socket(api=None, origin=ORIGIN, path="/ws", cookie=None):
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if cookie is None and api is not None:
        cookie = api.client.cookies[COOKIE_NAME].value
    if cookie:
        headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode()))
    return WebsocketCommunicator(application, path, headers=headers)


async def opened(api, **kwargs):
    ws = socket(api, **kwargs)
    connected, _ = await ws.connect()
    assert connected
    assert await ws.receive_json_from() == protocol.ready()
    return ws


async def closed_with(ws, code):
    answer = await ws.receive_output(2)
    assert answer["type"] == "websocket.close", answer
    assert answer["code"] == code
    await ws.disconnect()


async def refused(ws):
    connected, _ = await ws.connect()
    assert not connected
    await ws.disconnect()


def party(**kwargs):
    return sync_to_async(Api)(**kwargs)


def valid(event):
    Draft202012Validator(protocol.schema()).validate(event)


async def world():
    seller = await party(name="Hira Seller")
    buyer = await party()
    listing = await sync_to_async(publish_for)(seller.account)
    return seller, buyer, listing


def run(scenario):
    async def closing():
        try:
            await asyncio.wait_for(scenario(), 60)
        finally:
            await sync_to_async(connections.close_all)()  # or the test database stays in use

    asyncio.run(closing())


def test_the_committed_event_schema_is_fresh(tmp_path):
    target = tmp_path / "events.schema.json"
    call_command("events_schema", "--file", str(target))
    assert (ROOT / "events.schema.json").read_text() == target.read_text(), (
        "Regenerate with: uv run python manage.py events_schema --file events.schema.json"
    )
    Draft202012Validator.check_schema(protocol.schema())
    valid(protocol.ready())
    valid(protocol.message_created(uuid.UUID(int=1), uuid.UUID(int=2), 3))
    assert not Draft202012Validator(protocol.schema()).is_valid({"type": "ready", "body": "x"})


@pytest.mark.django_db(transaction=True)
def test_only_the_two_participants_hear_of_a_message(caplog):
    async def scenario():
        seller, buyer, listing = await world()
        intruder = await party(name="Nosy Neighbour")
        admin = await party(account_type="admin")
        sockets = {
            name: await opened(api)
            for name, api in (("seller", seller), ("buyer", buyer), ("intruder", intruder))
        }
        await refused(socket(admin))  # administrators hold no socket
        await refused(socket())  # nor does a Visitor
        await refused(socket(cookie="x" * 43))

        response = await sync_to_async(buyer.start)(listing, SECRET, key="k1")
        assert response.status_code == 201
        conversation = response.json()["conversation"]["id"]
        message = response.json()["message"]
        for name in ("seller", "buyer"):
            event = await sockets[name].receive_json_from(1)
            valid(event)
            assert event == protocol.message_created(
                uuid.UUID(conversation), uuid.UUID(message["id"]), 1
            )
            assert SECRET not in json.dumps(event)
        assert await sockets["intruder"].receive_nothing(0.3)

        # saved before it is announced, and a retry neither stores nor announces it again
        assert await Message.objects.acount() == 1
        assert (await sync_to_async(buyer.start)(listing, SECRET, key="k1")).status_code == 200
        for name in sockets:
            assert await sockets[name].receive_nothing(0.2)

        # the counterpart answers; REST and the socket agree on who may read
        assert (await sync_to_async(seller.send)(conversation, "Yes")).status_code == 201
        assert (await sockets["buyer"].receive_json_from(1))["seq"] == 2
        assert (await sockets["seller"].receive_json_from(1))["seq"] == 2
        assert await sockets["intruder"].receive_nothing(0.3)
        for api, expected in ((buyer, 200), (seller, 200), (intruder, 404), (admin, 404)):
            got = await sync_to_async(api.read)(conversation, "/messages?after=1")
            assert got.status_code == expected
        for ws in sockets.values():
            await ws.disconnect()
        assert not consumers._open

    with caplog.at_level(logging.DEBUG):
        run(scenario)
    assert SECRET not in "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)


@pytest.mark.django_db(transaction=True)
def test_origin_path_and_client_frames(settings):
    settings.CSRF_TRUSTED_ORIGINS = ["http://localhost:3000", "https://app.example.org"]

    async def scenario():
        user = await party()
        for origin in (
            None,
            "null",
            "http://localhost:3001",
            "https://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3000.evil.example",
            "http://localhost:3000/path",
            "https://app.example.org:444",
        ):
            await refused(socket(user, origin=origin))
        await (await opened(user, origin="https://app.example.org:443")).disconnect()
        await refused(socket(user, path="/ws/account.123"))  # no room can be named
        for frame in ('{"join": "account.x"}', "x"):
            ws = await opened(user)
            await ws.send_to(text_data=frame)
            await closed_with(ws, protocol.CLOSE_RECEIVE_ONLY)
        assert not consumers._open

    run(scenario)


@pytest.mark.django_db(transaction=True)
def test_connection_and_idle_caps(settings):
    settings.WS_MAX_CONNECTIONS_PER_ACCOUNT = 2

    async def scenario():
        user = await party()
        first, second = await opened(user), await opened(user)
        third = socket(user)
        assert (await third.connect())[0]
        await closed_with(third, protocol.CLOSE_TOO_MANY)
        await first.disconnect()
        await (await opened(user)).disconnect()  # the slot was freed
        await second.disconnect()
        settings.WS_IDLE_SECONDS = 0.2
        await closed_with(await opened(user), protocol.CLOSE_IDLE)
        assert not consumers._open

    run(scenario)


@pytest.mark.django_db(transaction=True)
def test_sign_out_closes_only_that_session():
    async def scenario():
        user = await party()
        email = await sync_to_async(identity_email)(user)
        other = await sync_to_async(second_session)(email)
        first, second = await opened(user), await opened(other)
        out = await sync_to_async(user.write)("delete", "/api/v1/session")
        assert out.status_code == 204
        await closed_with(first, protocol.CLOSE_SESSION_ENDED)
        assert await second.receive_nothing(0.3)
        await second.disconnect()

    run(scenario)


def identity_email(api):
    from apps.identity.models import Account

    return Account.objects.get(id=api.account.id).email


def second_session(email):
    from django.test import Client

    other = Api.__new__(Api)
    other.client = Client(enforce_csrf_checks=True)
    answer = other.write("post", "/api/v1/session", {"email": email, "password": PASSWORD})
    assert answer.status_code == 200
    return other


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("how", ["password_change", "deactivation", "suspension"])
def test_account_changes_close_the_socket(how):
    def change(api):
        admin_account = identity.provision_admin(
            Actor.system(), random_email(), "correct horse battery 9 staple"
        )
        admin = Actor.admin(admin_account.id)
        if how == "password_change":
            answer = api.write(
                "post",
                "/api/v1/me/password",
                {"current_password": PASSWORD, "new_password": "violet harbour lantern 82"},
            )
            assert answer.status_code in (200, 204), answer.content
        elif how == "deactivation":
            identity.deactivate(admin, api.account.id)
        else:
            identity.suspend_account(admin, api.account.id, "reported abuse")

    async def scenario():
        user = await party()
        other = await sync_to_async(second_session)(await sync_to_async(identity_email)(user))
        ws = await opened(other)
        await sync_to_async(change)(user)
        await closed_with(ws, protocol.CLOSE_SESSION_ENDED)
        await refused(socket(other))

    run(scenario)


@pytest.mark.django_db(transaction=True)
def test_a_session_ended_without_a_signal_gets_no_event_and_is_closed(settings):
    """A change made by another process (a command) sends no signal: the check before each
    delivery and the periodic one catch it."""

    def end_quietly(api):
        from apps.identity.models import Session

        Session.objects.filter(account_id=api.account.id).update(
            ended_at="2020-01-01T00:00:00Z", ended_reason="sign_out"
        )

    async def scenario():
        seller, buyer, listing = await world()
        ws = await opened(seller)
        await sync_to_async(end_quietly)(seller)
        assert (await sync_to_async(buyer.start)(listing)).status_code == 201
        await closed_with(ws, protocol.CLOSE_SESSION_ENDED)

        settings.WS_RECHECK_SECONDS = 0.2
        ws = await opened(buyer)
        await sync_to_async(end_quietly)(buyer)
        await closed_with(ws, protocol.CLOSE_SESSION_ENDED)

    run(scenario)
