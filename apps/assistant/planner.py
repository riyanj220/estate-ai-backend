"""What to ask next (Story 5.10). No model call: the Draft and the Field registry decide, so a
value already there (typed in the chat or in the form) is never asked for again.

Missing required fields come first, one question at a time, those the chat can fill before those
only the form can. When nothing required is missing, review is offered with optional fields.
"""

from dataclasses import dataclass

from apps.catalog import registry
from apps.listings.selectors import ListingView

_FIXED = frozenset({"purpose", "category", "subtype"})  # chosen when the Draft starts
OPTIONAL_LIMIT = 3
# Asked together, in this order; a required field not listed follows in registry order.
_GROUPS = (
    (("area_value", "area_unit"), "What is the area of the property, with its unit?"),
    (("asking_price",), "What price are you asking, in rupees?"),
    (("bedrooms", "bathrooms"), "How many bedrooms and bathrooms does it have?"),
    (("title",), "What title would you like for the listing (at least 10 characters)?"),
)


@dataclass(frozen=True)
class NextStep:
    kind: str  # "required" or "review"
    question: str
    fields_asked: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    in_form: bool = False


def label(field_id: str) -> str:
    """A field id in plain words."""
    return field_id.removesuffix("_ft").removesuffix("_sqft").replace("_", " ")


def _missing(listing: ListingView, requirement: str) -> list[registry.FieldDef]:
    purpose, category, subtype = (listing.fields[n] for n in ("purpose", "category", "subtype"))
    return [
        spec
        for spec in registry.fields()
        if spec.requirement == requirement
        and not spec.derived
        and spec.id not in _FIXED
        and spec.applies_to(purpose, category, subtype)
        and listing.fields.get(spec.id) in (None, "")
    ]


def next_step(listing: ListingView) -> NextStep | None:
    """None for a Listing that is no longer a Draft."""
    if listing.status != "draft":
        return None
    missing = {spec.id: spec for spec in _missing(listing, "required_for_publication")}
    for names, question in _GROUPS:
        asked = tuple(name for name in names if name in missing)
        if asked:
            if len(asked) < len(names):
                question = f"What is the {label(asked[0])}?"
            return NextStep("required", question, asked)
    # what is left has no question of its own: chat-fillable first
    for spec in sorted(missing.values(), key=lambda s: not s.ai_extractable):
        if spec.ai_extractable:
            return NextStep("required", f"What is the {label(spec.id)}?", (spec.id,))
        question = f"Please choose the {label(spec.id)} in the form."
        return NextStep("required", question, (spec.id,), in_form=True)
    optional = tuple(
        spec.id
        for spec in _missing(listing, "recommended")
        if spec.ai_extractable and spec.id != "description"
    )[:OPTIONAL_LIMIT]
    question = "All required details are filled in. You can review your listing now."
    if not listing.media:
        question += " Add at least one photo before publishing."
    if optional:
        question += f" If you like, you can also tell me: {', '.join(map(label, optional))}."
    return NextStep("review", question, optional=optional)
