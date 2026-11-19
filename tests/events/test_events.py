import logging
import uuid
from collections import deque  # noqa: F401  (resolves string annotations)
from collections.abc import Mapping, MutableSequence, Sequence  # noqa: F401
from dataclasses import FrozenInstanceError, dataclass
from typing import Optional  # noqa: F401

import pytest
from django.db import transaction

from apps.kernel import events
from apps.kernel.events import DomainEvent
from tests.events.sample_events import OtherThingHappened, ThingHappened

pytestmark = pytest.mark.django_db
ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})


def test_dispatches_after_commit_in_registration_order(django_capture_on_commit_callbacks):
    seen = []
    events.subscribe(ThingHappened, lambda e: seen.append(("a", e.thing_id)))
    events.subscribe(ThingHappened, lambda e: seen.append(("b", e.thing_id)))
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        events.emit(ThingHappened(thing_id=ID))
        assert seen == []
    assert len(callbacks) == 2  # one callback per subscriber
    assert seen == [("a", ID), ("b", ID)]


def test_rolled_back_emit_calls_nobody():
    seen = []
    events.subscribe(ThingHappened, seen.append)
    with pytest.raises(RuntimeError), transaction.atomic():
        events.emit(ThingHappened(thing_id=ID))
        raise RuntimeError
    assert seen == []


def test_no_subscribers_schedules_nothing(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks() as callbacks:
        events.emit(ThingHappened(thing_id=ID))
    assert callbacks == []


def test_only_subscribers_of_that_event_run(django_capture_on_commit_callbacks):
    seen = []
    events.subscribe(OtherThingHappened, seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        events.emit(ThingHappened(thing_id=ID))
    assert seen == []


@pytest.mark.django_db(transaction=True)
def test_autocommit_runs_immediately():
    seen = []
    events.subscribe(ThingHappened, seen.append)
    events.emit(ThingHappened(thing_id=ID))
    assert len(seen) == 1


def test_failing_subscriber_is_logged_by_name_only_and_others_run(
    django_capture_on_commit_callbacks, caplog
):
    seen = []

    def broken(event):
        raise RuntimeError(f"secret payload {event.thing_id}")

    events.subscribe(ThingHappened, broken)
    events.subscribe(ThingHappened, seen.append)
    with caplog.at_level(logging.ERROR, logger="estate.events"):
        with django_capture_on_commit_callbacks(execute=True):
            events.emit(ThingHappened(thing_id=ID))
    assert len(seen) == 1
    record = caplog.records[-1]
    assert record.event == "thing.happened"
    assert record.handler.endswith("broken")
    assert record.exc_info is None
    assert "secret" not in caplog.text and str(ID) not in caplog.text


@pytest.mark.parametrize(
    "name", ["", "listing", "Listing.published", "a.b.c", "listing.Published", ".x", "x."]
)
def test_bad_names_fail_at_class_creation(name):
    with pytest.raises(TypeError):
        type("Bad", (DomainEvent,), {"name": name})


def test_missing_name_fails():
    with pytest.raises(TypeError):
        type("NoName", (DomainEvent,), {})


def test_non_frozen_definition_fails():
    with pytest.raises(TypeError):

        @dataclass
        class Loose(DomainEvent):
            name = "thing.loosened"


def test_mutable_payload_types_fail():
    with pytest.raises(TypeError):

        @dataclass(frozen=True)
        class Lists(DomainEvent):
            name = "thing.listed"
            ids: list[int]


def test_events_are_immutable():
    event = ThingHappened(thing_id=ID)
    with pytest.raises(FrozenInstanceError):
        event.thing_id = uuid.uuid4()


def test_duplicate_subscription_is_an_error():
    events.subscribe(ThingHappened, print)
    with pytest.raises(ValueError):
        events.subscribe(ThingHappened, print)


def test_subscribe_rejects_non_events():
    with pytest.raises(TypeError):
        events.subscribe(int, print)
    with pytest.raises(TypeError):
        events.emit("thing.happened")


def test_clear_forgets_subscriptions(django_capture_on_commit_callbacks):
    events.subscribe(ThingHappened, print)
    events.clear()
    with django_capture_on_commit_callbacks() as callbacks:
        events.emit(ThingHappened(thing_id=ID))
    assert callbacks == []


def test_failing_subscriber_log_is_clean_through_the_json_formatter(
    django_capture_on_commit_callbacks, caplog
):
    import json

    from apps.kernel.request_logging import JsonFormatter

    def broken(event):
        raise ValueError(f"leak {event.thing_id}")

    events.subscribe(ThingHappened, broken)
    with caplog.at_level(logging.ERROR, logger="estate.events"):
        with django_capture_on_commit_callbacks(execute=True):
            events.emit(ThingHappened(thing_id=ID))
    line = JsonFormatter().format(caplog.records[-1])
    data = json.loads(line)
    assert data["event"] == "thing.happened"
    assert data["handler"].endswith("broken")
    assert data["exception_class"] == "ValueError"
    assert "leak" not in line and str(ID) not in line


def test_base_event_subscriber_does_not_receive_subclass_events(
    django_capture_on_commit_callbacks,
):
    @dataclass(frozen=True)
    class Derived(ThingHappened):
        name = "thing.derived_happened"

    seen = []
    events.subscribe(ThingHappened, seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        events.emit(Derived(thing_id=ID))
    assert seen == []  # matching is on the exact event type


def test_handler_registered_after_emit_is_not_called(django_capture_on_commit_callbacks):
    seen = []
    events.subscribe(ThingHappened, lambda e: seen.append("early"))
    with django_capture_on_commit_callbacks(execute=True):
        events.emit(ThingHappened(thing_id=ID))
        events.subscribe(ThingHappened, lambda e: seen.append("late"))
    assert seen == ["early"]


def test_non_callable_handler_is_rejected_at_subscribe():
    with pytest.raises(TypeError):
        events.subscribe(ThingHappened, "not callable")


def test_undecorated_subclass_is_rejected_at_subscribe_and_emit():
    class Bare(ThingHappened):
        name = "thing.bare_happened"

    with pytest.raises(TypeError):
        events.subscribe(Bare, print)
    with pytest.raises(TypeError):
        events.emit(Bare(thing_id=ID))


def test_duplicate_name_from_another_class_is_rejected_but_redefinition_allowed():
    with pytest.raises(TypeError):

        @dataclass(frozen=True)
        class Impostor(DomainEvent):
            name = "thing.happened"

    def define():
        @dataclass(frozen=True)
        class Redefined(DomainEvent):
            name = "thing.redefined"

        return Redefined

    define()
    define()  # same module and qualname: allowed (reload)


@pytest.mark.parametrize(
    "form",
    [
        "list[int]",
        "dict[str, int]",
        "set[int]",
        "Optional[list[int]]",
        "list[int] | None",
        "Sequence[int]",
        "Mapping[str, int]",
        "MutableSequence[int]",
        "deque[int]",
        "tuple[list[int], ...]",
        "bytearray",
    ],
)
def test_string_and_nested_mutable_annotations_are_rejected(form):
    slug = "".join(c for c in form if c.isalpha())[:20].lower()
    with pytest.raises(TypeError):
        type("M", (DomainEvent,), {"name": f"thing.{slug}_seen", "__annotations__": {"f": form}})


def test_unresolvable_string_annotation_is_rejected():
    with pytest.raises(TypeError):
        type(
            "U",
            (DomainEvent,),
            {"name": "thing.unresolved_seen", "__annotations__": {"f": "Nope"}},
        )


def test_immutable_annotations_are_accepted():
    @dataclass(frozen=True)
    class Fine(DomainEvent):
        name = "thing.fine_seen"
        a: int
        b: tuple[int, ...] | None
        c: frozenset[str]
        d: uuid.UUID
