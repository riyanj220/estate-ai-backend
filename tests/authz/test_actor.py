import dataclasses
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError

ID = uuid.uuid4()


def test_constructors_set_kind_and_account():
    assert Actor.visitor("r").kind is ActorKind.VISITOR
    assert Actor.system("r").account_id is None
    assert Actor.user(ID, "r").account_id == ID
    assert Actor.admin(ID, "r").kind is ActorKind.ADMIN
    assert Actor.user(ID, "req-1").request_id == "req-1"


@pytest.mark.parametrize(
    "build",
    [
        lambda: Actor(ActorKind.USER),
        lambda: Actor(ActorKind.ADMIN),
        lambda: Actor(ActorKind.VISITOR, account_id=ID),
        lambda: Actor(ActorKind.SYSTEM, account_id=ID),
        lambda: Actor(ActorKind.USER, account_id=str(ID)),
        lambda: Actor("user", account_id=ID),
    ],
)
def test_invalid_construction_fails(build):
    with pytest.raises(ValueError, match=r"."):
        build()


def test_actor_is_frozen():
    actor = Actor.user(ID)
    with pytest.raises(dataclasses.FrozenInstanceError):
        actor.kind = ActorKind.ADMIN


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module", ["apps.kernel.actor", "apps.kernel.errors"])
def test_kernel_modules_import_no_web_framework(module):
    code = (
        f"import sys; import {module}; "
        "bad = [m for m in ('django', 'rest_framework', 'channels') if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT)}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=ROOT, env=env, check=False
    )
    assert result.returncode == 0


def test_require_signed_in():
    with pytest.raises(DomainError) as exc:
        require_signed_in(Actor.visitor())
    assert exc.value.code == "unauthenticated"
    for actor in (Actor.user(ID), Actor.admin(ID), Actor.system()):
        require_signed_in(actor)


def test_require_admin():
    with pytest.raises(DomainError) as exc:
        require_admin(Actor.visitor())
    assert exc.value.code == "unauthenticated"
    with pytest.raises(DomainError) as exc:
        require_admin(Actor.user(ID))
    assert exc.value.code == "not_found"
    with pytest.raises(DomainError) as exc:
        require_admin(Actor.system())
    assert exc.value.code == "not_found"
    require_admin(Actor.admin(ID))
