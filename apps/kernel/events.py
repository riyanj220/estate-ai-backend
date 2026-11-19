"""Domain event bus (AD-18).

Events are frozen dataclasses defined in the emitting app's `events` module. `emit`
schedules one `transaction.on_commit(robust=True)` callback per subscriber, so work
rolled back never reaches a subscriber and no subscriber means nothing is scheduled.
Subscribers register in `AppConfig.ready` (see `docs/events.md`); a convention test
limits `subscribe(` calls to `subscribers.py` and `apps.py` modules.

Events are for derived, repairable consequences only. Consequences that must not be
lost belong in the originating transaction (AD-23).
"""

import collections.abc as abc
import logging
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import ClassVar, get_args, get_origin, get_type_hints

from django.db import transaction

logger = logging.getLogger("estate.events")

_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_MUTABLE = (list, dict, set, bytearray, deque)
_MUTABLE_ABC = (abc.MutableSequence, abc.MutableMapping, abc.MutableSet)
_ABSTRACT = (abc.Sequence, abc.Mapping, abc.Set, abc.Collection, abc.Iterable)
_names: dict[str, tuple[str, str]] = {}


def _is_mutable(annotation) -> bool:
    """True if the annotation, or any type nested in it, is a mutable or abstract container."""
    origin = get_origin(annotation) or annotation
    if origin is ClassVar:
        return False
    if isinstance(origin, type) and (
        origin in _MUTABLE or origin in _ABSTRACT or issubclass(origin, _MUTABLE_ABC)
    ):
        return True
    return any(_is_mutable(arg) for arg in get_args(annotation))


@dataclass(frozen=True)
class DomainEvent:
    """Base class. Subclasses are frozen dataclasses with a `name` such as `listing.published`.

    A subclass declared with a non-frozen `@dataclass` fails at class creation because a
    dataclass cannot inherit from a frozen one. Payloads hold ids and plain values only.
    """

    name: ClassVar[str]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = cls.__dict__.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise TypeError(f"{cls.__name__}: name must look like 'domain.past_tense'")
        own = cls.__dict__.get("__annotations__", {})
        try:
            hints = get_type_hints(cls, include_extras=True) if own else {}
        except Exception as exc:
            raise TypeError(f"{cls.__name__}: payload annotations cannot be resolved") from exc
        for field in own:
            if _is_mutable(hints[field]):
                raise TypeError(f"{cls.__name__}.{field}: mutable payload type")
        identity = (cls.__module__, cls.__qualname__)
        if _names.setdefault(name, identity) != identity:
            raise TypeError(f"{cls.__name__}: event name {name!r} is already defined")


Handler = Callable[[DomainEvent], None]

_registry: dict[type[DomainEvent], list[Handler]] = {}


def _handler_name(handler: Handler) -> str:
    return f"{getattr(handler, '__module__', '?')}.{getattr(handler, '__qualname__', '?')}"


def _check_event_class(event_type) -> None:
    if not (isinstance(event_type, type) and issubclass(event_type, DomainEvent)):
        raise TypeError("event_type must be a DomainEvent subclass")
    params = event_type.__dict__.get("__dataclass_params__")
    if params is None or not params.frozen:
        raise TypeError(f"{event_type.__name__} must be decorated with @dataclass(frozen=True)")


def subscribe(event_type: type[DomainEvent], handler: Handler) -> None:
    _check_event_class(event_type)
    if not callable(handler):
        raise TypeError("handler must be callable")
    handlers = _registry.setdefault(event_type, [])
    if handler in handlers:
        raise ValueError(f"{_handler_name(handler)} is already subscribed to {event_type.name}")
    handlers.append(handler)


def emit(event: DomainEvent) -> None:
    if not isinstance(event, DomainEvent):
        raise TypeError("emit takes a DomainEvent")
    _check_event_class(type(event))
    for handler in tuple(_registry.get(type(event), ())):
        transaction.on_commit(partial(_dispatch, handler, event), robust=True)


def _dispatch(handler: Handler, event: DomainEvent) -> None:
    try:
        handler(event)
    except Exception as exc:
        # Only names are logged: the message, payload and traceback may carry private values.
        logger.error(
            "subscriber failed",
            extra={
                "event": event.name,
                "handler": _handler_name(handler),
                "exception_class": type(exc).__name__,
            },
        )


def clear() -> None:
    """Forget every subscription. For tests."""
    _registry.clear()
