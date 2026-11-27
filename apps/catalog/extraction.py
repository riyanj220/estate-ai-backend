"""The strict-mode structured-output schema for Listing facts, generated from the Field registry
(AD-4, AD-8): every property required and nullable (null = not stated), no additional properties,
and no length or range constraint. The registry's rules still judge every proposed value (AD-10).
"""

from apps.catalog import registry

# Bumped when the shape of the extraction schema changes; sessions record it.
SCHEMA_VERSION = 4
# The description is written from confirmed details (Story 5.13), never picked out of chat.
_NOT_EXTRACTED = frozenset({"description"})
_JSON_TYPES = {"integer": "integer", "boolean": "boolean"}  # everything else is text


def _property(spec: registry.FieldDef, purpose: str, subtype: str) -> dict:
    if spec.type == "enum":
        values = list(spec.values_for(purpose, subtype))
        schema = {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}
    else:
        schema = {"type": [_JSON_TYPES.get(spec.type, "string"), "null"]}
    if spec.description:
        schema["description"] = spec.description
    return schema


def extraction_schema(purpose: str, subtype: str) -> dict:
    """The `ai_extractable` fields that apply to a Listing of this purpose and Subtype."""
    category = next(c.id for c in registry.categories() if subtype in c.subtypes)
    properties = {
        spec.id: _property(spec, purpose, subtype)
        for spec in registry.fields()
        if spec.ai_extractable
        and spec.id not in _NOT_EXTRACTED
        and spec.applies_to(purpose, category, subtype)
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
