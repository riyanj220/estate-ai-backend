import uuid
from types import SimpleNamespace

import pytest

from apps.kernel.actor import ActorKind
from apps.presentation.api.actors import get_actor, resolve_actor

ID = uuid.uuid4()


def request(identity=None, request_id="req-1", **extra):
    ns = SimpleNamespace(request_id=request_id, **extra)
    if identity is not None:
        ns.authenticated_identity = identity
    return ns


def test_no_identity_is_visitor_with_request_id():
    actor = resolve_actor(request())
    assert actor.kind is ActorKind.VISITOR
    assert actor.request_id == "req-1"


@pytest.mark.parametrize("kind", ["user", "admin"])
def test_trusted_identity_resolves(kind):
    actor = resolve_actor(request(SimpleNamespace(kind=kind, account_id=ID)))
    assert actor.kind.value == kind
    assert actor.account_id == ID
    actor = resolve_actor(request(SimpleNamespace(kind=kind, account_id=str(ID))))
    assert actor.account_id == ID


@pytest.mark.parametrize(
    "identity",
    [
        SimpleNamespace(kind="root", account_id=ID),
        SimpleNamespace(kind="system", account_id=ID),
        SimpleNamespace(kind="visitor", account_id=ID),
        SimpleNamespace(kind="user"),
        SimpleNamespace(kind="user", account_id=None),
        SimpleNamespace(kind="admin", account_id="not-a-uuid"),
        SimpleNamespace(kind="user", account_id="{" + str(ID) + "}"),
        SimpleNamespace(kind="user", account_id="urn:uuid:" + str(ID)),
        SimpleNamespace(kind="user", account_id=ID.hex),
        SimpleNamespace(kind="user", account_id=str(ID).upper()),
        SimpleNamespace(kind="user", account_id=" " + str(ID)),
        SimpleNamespace(kind="user", account_id=uuid.UUID(int=0)),
        SimpleNamespace(kind="admin", account_id="00000000-0000-0000-0000-000000000000"),
        SimpleNamespace(kind="user", account_id=12345),
        SimpleNamespace(),
        "user",
        object(),
    ],
)
def test_unrecognised_identity_fails_closed(identity):
    actor = resolve_actor(request(identity))
    assert actor.kind is ActorKind.VISITOR
    assert actor.request_id == "req-1"


def test_client_supplied_values_never_set_an_actor():
    hostile = request(
        headers={"X-Actor": "admin", "X-Account-Id": str(ID)},
        META={"HTTP_X_ACTOR": "admin"},
        user=SimpleNamespace(kind="admin", account_id=ID),
        actor_kind="admin",
    )
    assert resolve_actor(hostile).kind is ActorKind.VISITOR


def test_missing_or_odd_request_id_becomes_empty():
    assert resolve_actor(SimpleNamespace()).request_id == ""
    assert resolve_actor(SimpleNamespace(request_id=5)).request_id == ""


def test_get_actor_attaches_and_caches():
    req = request()
    first = get_actor(req)
    assert req.actor is first
    req.authenticated_identity = SimpleNamespace(kind="user", account_id=ID)
    assert get_actor(req) is first
