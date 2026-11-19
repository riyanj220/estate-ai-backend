import inspect
import uuid

import pytest

from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from tests.authz import sample_service
from tests.authz.sample_service import Widget, rename_widget

OWNER = uuid.uuid4()


@pytest.fixture
def widget():
    w = Widget(uuid.uuid4(), OWNER, "before")
    sample_service.STORE[w.id] = w
    yield w
    sample_service.STORE.pop(w.id, None)


def test_actor_is_the_first_parameter():
    assert next(iter(inspect.signature(rename_widget).parameters)) == "actor"


def test_owner_can_rename(widget):
    assert rename_widget(Actor.user(OWNER), widget.id, "after").name == "after"


@pytest.mark.parametrize(
    ("actor", "code"),
    [
        (Actor.visitor(), "unauthenticated"),
        (Actor.user(uuid.uuid4()), "not_found"),
        (Actor.admin(uuid.uuid4()), "not_found"),
        (Actor.system(), "not_found"),
    ],
)
def test_others_are_refused_and_nothing_changes(widget, actor, code):
    with pytest.raises(DomainError) as exc:
        rename_widget(actor, widget.id, "hijacked")
    assert exc.value.code == code
    assert widget.name == "before"
