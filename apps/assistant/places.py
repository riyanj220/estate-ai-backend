"""Finding the catalogue locality a Seller named. No model call: the redacted message and the
model's location texts are matched against the active localities, whole names only.

City and locality are not `ai_extractable` (the model never sees or chooses catalogue ids); what is
found here is written as an unconfirmed `ai` value that the Seller must confirm.
"""

import re

from apps.catalog import selectors as catalog

# The extracted text fields in which a model tends to put a locality name.
LOCATION_TEXTS = ("society", "phase", "sector", "block")
_BREAKS = re.compile(r"[,.;:!?()\[\]/|\n۔،؟]")
_CONNECTORS = frozenset({"e", "i"})  # "Gulshan-e-Iqbal", "Gulshan i Iqbal", "Gulshan Iqbal"
_SPELLINGS = {"johar": "jauhar", "jouhar": "jauhar"}
# Names that are also everyday words: never picked out of a sentence.
_EVERYDAY = frozenset({"garden", "site"})
# "near Saddar" or "Korangi Road" does not say the property is there.
_BEFORE = frozenset({"near", "nearby", "from", "opposite", "behind", "towards"})
_AFTER = (("road",), ("ke", "qareeb"), ("ke", "paas"), ("ke", "pass"))


def _tokens(text: str) -> list[str]:
    words = [_SPELLINGS.get(w, w) for w in re.findall(r"[^\W_]+", text.casefold())]
    return [
        "dha" if word == "defence" and words[i + 1 : i + 2] == ["phase"] else word
        for i, word in enumerate(words)
    ]


def _keys(tokens: list[str]) -> set[str]:
    return {"".join(tokens), "".join(t for t in tokens if t not in _CONNECTORS)}


def same_name(text: str, name: str) -> bool:
    """Equal ignoring case, spaces, hyphens and the connecting "e"."""
    return bool(_keys(_tokens(text)) & _keys(_tokens(name)))


def _spans(tokens: list[str], index: dict, prose: bool) -> list:
    found = []
    for start in range(len(tokens)):
        for end in range(start + 1, len(tokens) + 1):
            window = tokens[start:end]
            if window[0] in _CONNECTORS or window[-1] in _CONNECTORS:
                continue
            for locality in {ref for key in _keys(window) for ref in index.get(key, ())}:
                if prose and (
                    locality.name.casefold() in _EVERYDAY
                    or (start and tokens[start - 1] in _BEFORE)
                    or any(tuple(tokens[end : end + len(a)]) == a for a in _AFTER)
                ):
                    # kept as a span so that a shorter name inside it is not matched instead
                    found.append((start, end, None))
                else:
                    found.append((start, end, locality))
    return found


def _matches(text: str, index: dict, prose: bool) -> set:
    """The localities named in `text`; a name inside a longer one ("Nazimabad" in "North
    Nazimabad") does not count."""
    found = set()
    for segment in _BREAKS.split(text):
        spans = _spans(_tokens(segment), index, prose)
        for start, end, locality in spans:
            inside = any(s <= start and end <= e and e - s > end - start for s, e, _ in spans)
            if locality is not None and not inside:
                found.add(locality)
    return found


def find_locality(message: str, texts: list[str], city_id=None) -> catalog.LocalityRef | None:
    """The one locality named by the message or the model's location texts, else None: two
    different ones are not guessed between. `city_id` limits the search to one city."""
    index: dict[str, set] = {}
    for locality in catalog.active_localities():
        if city_id is None or locality.city_id == city_id:
            for key in _keys(_tokens(locality.name)):
                index.setdefault(key, set()).add(locality)
    said = _matches(message, index, prose=True)
    found = said.union(*(_matches(text, index, prose=False) for text in texts))
    # the model may have copied only a part of the name the Seller wrote
    names = {ref: f" {' '.join(_tokens(ref.name))} " for ref in found}
    found = {
        ref
        for ref in found
        if ref in said or not any(names[ref] != n and names[ref] in n for n in names.values())
    }
    return found.pop() if len(found) == 1 else None
