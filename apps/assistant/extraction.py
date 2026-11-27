"""From the model's structured answer to the Draft (Story 5.9).

The answer is untrusted: only the fields of the code-generated schema are read, text values must
survive redaction unchanged, and every write goes through `listings.services.apply_updates` with
origin `ai` (so it is judged by the registry rules and stored unconfirmed). A value that fails is
not saved; a value the Seller changed while the model was working is never overwritten.
"""

import json
from dataclasses import dataclass, field

from apps.assistant.planner import label
from apps.catalog import extraction as schemas
from apps.kernel.actor import Actor
from apps.kernel.ai.redaction import KnownValues, redact
from apps.listings import services as listings
from apps.listings.lifecycle import Origin
from apps.listings.selectors import ListingView, field_text

MAX_ANSWER = 20_000  # characters
CONTACT_NOTICE = (
    "I removed a phone number, email address or street address from your message. "
    "Please enter those in the form instead; I do not save them."
)
# Fields that only mean something together: if one cannot be saved, none of them is.
_TOGETHER = (frozenset({"area_value", "area_unit", "marla_basis"}),)


def turn_schema(purpose: str, subtype: str) -> dict:
    """Strict mode: one object for the reply and the facts, so a turn is one model call."""
    properties = {
        "reply": {"type": "string"},
        "clarify": {"type": ["string", "null"]},
        "fields": schemas.extraction_schema(purpose, subtype),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Parsed:
    reply: str = field(repr=False)
    clarify: str = field(default="", repr=False)
    fields: dict = field(default_factory=dict, repr=False)


def parse(text: str) -> Parsed | None:
    """None for a broken or oversized structured answer. Plain prose is a reply with no facts."""
    if len(text) > MAX_ANSWER:
        return None
    if not text.lstrip().startswith("{"):
        return Parsed(text)
    try:
        data = json.loads(text)
        reply, clarify, fields = data["reply"], data.get("clarify"), data.get("fields")
    except (ValueError, KeyError, TypeError, RecursionError):
        return None
    if not isinstance(reply, str):
        return None
    return Parsed(
        reply,
        clarify if isinstance(clarify, str) else "",
        fields if isinstance(fields, dict) else {},
    )


@dataclass
class Written:
    applied: dict = field(default_factory=dict)  # field id -> wire value
    rejected: dict = field(default_factory=dict)  # field id -> code
    conflicts: list = field(default_factory=list)

    def as_json(self, notices: list[str]) -> dict:
        if self.rejected:
            names = ", ".join(map(label, self.rejected))
            notices = [*notices, f"I could not save: {names}. Please check and tell me again."]
        if self.conflicts:
            names = ", ".join(map(label, self.conflicts))
            notices = [*notices, f"You changed these meanwhile, so I left them: {names}."]
        return {
            "applied": [{"field": k, "value": v} for k, v in self.applied.items()],
            "rejected": [{"field": k, "code": v} for k, v in self.rejected.items()],
            "conflicts": list(self.conflicts),
            "notices": notices,
        }


def _with_partners(names: set[str], pending: dict) -> set[str]:
    for group in _TOGETHER:
        if names & group:
            names = names | (group & pending.keys())
    return names


def _proposed(listing: ListingView, fields: dict, values: KnownValues, out: Written) -> dict:
    schema = schemas.extraction_schema(listing.fields["purpose"], listing.fields["subtype"])
    updates = {}
    for name in schema["properties"]:  # never keyed by what the model sent
        value = fields.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not isinstance(value, str | int):  # a boolean is an int
            out.rejected[name] = "invalid"
        elif isinstance(value, str) and redact(value, values).value != value:
            out.rejected[name] = "contains_contact_details"
        else:
            updates[name] = value
    for name in _with_partners(set(out.rejected), updates) & updates.keys():
        out.rejected[name] = "invalid"
        del updates[name]
    return updates


def apply(actor: Actor, listing: ListingView, fields: dict, values: KnownValues) -> Written:
    """Write what can be written against the revision of `listing` (read before the model call).

    A stale revision is reconciled once: fields the Seller has since changed are left alone and
    reported; the rest are applied to the current revision."""
    out = Written()
    pending = _proposed(listing, fields, values, out)
    revision, reconciled = listing.revision, False
    while pending:
        try:
            view = listings.apply_updates(actor, listing.id, revision, pending, Origin.AI)
        except listings.StaleRevision as stale:
            if reconciled:
                out.conflicts.extend(pending)
                break
            reconciled, revision = True, stale.current.revision
            changed = {
                name
                for name in pending
                if stale.current.fields.get(name) != listing.fields.get(name)
            }
            for name in sorted(_with_partners(changed, pending)):
                out.conflicts.append(name)
                del pending[name]
        except listings.FieldsInvalid as invalid:
            # errors that name no proposed field refuse the whole write
            bad = (set(invalid.fields) & pending.keys()) or set(pending)
            for name in sorted(_with_partners(bad, pending)):
                out.rejected[name] = invalid.fields.get(name, "invalid")
                del pending[name]
        else:
            for name in pending:
                if view.fields.get(name) != listing.fields.get(name):
                    out.applied[name] = field_text(name, view.fields.get(name))
            break
    return out
