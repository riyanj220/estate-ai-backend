"""AI feature switches and their audited toggle (Story 10.6, AD-20).

A switch is a database-stored feature flag, so a change applies to the next request without a
restart. `ai_gateway` is the master switch; the others turn off one feature. A feature that is
off answers like any unavailable AI feature, so the manual and conventional paths keep working.
"""

from dataclasses import dataclass

from django.db import transaction

from apps.kernel import audit, flags
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.kernel.models import FeatureFlag

MASTER = "ai_gateway"
PRICE_ESTIMATE = "price_estimate"
AUDIT_ACTION = "ai.feature_flag_changed"
# (flag name, label). A gateway feature's flag is `ai_<feature name>`.
SWITCHES = (
    (MASTER, "All AI provider features"),
    ("ai_assistant_turn", "Assistant chat"),
    ("ai_listing_description", "Description drafting"),
    ("ai_search_query", "Plain-language search"),
    ("ai_voice_search", "Voice search"),
    (PRICE_ESTIMATE, "AI Price Estimate"),
)
NAMES = tuple(name for name, _ in SWITCHES)


@dataclass(frozen=True)
class SwitchState:
    name: str
    label: str
    enabled: bool


def register() -> None:
    """From `KernelConfig.ready`."""
    flags.register_flag(MASTER, True, "Master switch for every AI provider call.")
    for name, label in SWITCHES[1:]:
        flags.register_flag(name, True, f"Switch for: {label}.")
    audit.register_action(AUDIT_ACTION, {"flag": NAMES, "old_enabled": bool, "new_enabled": bool})


def states(actor: Actor) -> list[SwitchState]:
    require_admin(actor)
    return [SwitchState(name, label, flags.is_enabled(name)) for name, label in SWITCHES]


def set_switch(actor: Actor, name: str, enabled: bool) -> SwitchState:
    """Turn a switch on or off; audited with the old and new state. Setting the state it already
    has changes and records nothing."""
    require_admin(actor)
    labels = dict(SWITCHES)
    if name not in labels:
        raise DomainError("not_found")
    with transaction.atomic():
        list(FeatureFlag.objects.select_for_update().filter(name=name))
        old = flags.is_enabled(name)
        if old != enabled:
            flags.set_flag(actor, name, enabled)
            row = FeatureFlag.objects.get(name=name)
            audit.record(
                actor,
                AUDIT_ACTION,
                "feature_flag",
                row.id,
                {"flag": name, "old_enabled": old, "new_enabled": enabled},
            )
    return SwitchState(name, labels[name], enabled)
