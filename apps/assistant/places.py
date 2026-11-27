"""Finding the catalogue locality a Seller named. No model call: the redacted message and the
model's location texts are matched against the active localities, whole names only.

City and locality are not `ai_extractable` (the model never sees or chooses catalogue ids); what is
found here is written as an unconfirmed `ai` value that the Seller must confirm.
"""

from apps.catalog import places as names_in
from apps.catalog import selectors as catalog

# The extracted text fields in which a model tends to put a locality name.
LOCATION_TEXTS = ("society", "phase", "sector", "block")
same_name = names_in.same_name


def find_locality(message: str, texts: list[str], city_id=None) -> catalog.LocalityRef | None:
    """The one locality named by the message or the model's location texts, else None: two
    different ones are not guessed between. `city_id` limits the search to one city."""
    index = names_in.name_index(
        ref for ref in catalog.active_localities() if city_id is None or ref.city_id == city_id
    )
    said = names_in.matches(message, index, prose=True)
    found = said.union(*(names_in.matches(text, index, prose=False) for text in texts))
    # the model may have copied only a part of the name the Seller wrote
    names = {ref: names_in.words(ref.name) for ref in found}
    found = {
        ref
        for ref in found
        if ref in said or not any(names[ref] != n and names[ref] in n for n in names.values())
    }
    return found.pop() if len(found) == 1 else None
