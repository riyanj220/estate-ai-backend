"""Matching catalogue place names in free text (shared by the assistant and search). Whole names
only, ignoring case, spaces, hyphens and the connecting "e"; no model call.
"""

import re

from apps.catalog import selectors

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


def matches(text: str, index: dict, prose: bool) -> set:
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


def name_index(refs) -> dict[str, set]:
    """{normalised name: the refs that carry it}, for `matches`."""
    index: dict[str, set] = {}
    for ref in refs:
        for key in _keys(_tokens(ref.name)):
            index.setdefault(key, set()).add(ref)
    return index


def words(text: str) -> str:
    """The normalised words of `text` between single spaces, for containment tests."""
    return f" {' '.join(_tokens(text))} "


def localities_called(name: str, city_id=None) -> set[selectors.LocalityRef]:
    """The active localities a buyer means by `name`: those named exactly, else every one whose
    name starts with those words ("DHA" means each DHA phase)."""
    refs = [r for r in selectors.active_localities() if city_id is None or r.city_id == city_id]
    found = matches(name, name_index(refs), prose=False)
    if not found and (start := words(name).rstrip()):
        found = {r for r in refs if words(r.name).startswith(f"{start} ")}
    return found
