"""`redact` is the only way to make `RedactedText`, the only text the AI ports accept (AD-8).

The actor's exact phone, email and address values are scrubbed first, then the shared patterns
(`kernel.privacy.patterns`, AD-9). Redaction is best effort for free-text addresses (FR-32), so
audio, which cannot be redacted, is covered by the user-facing notice instead.
"""

from dataclasses import dataclass, field

from apps.kernel.privacy.patterns import ContactKind, find_contact_details, find_known_values

PLACEHOLDERS = {
    ContactKind.PHONE: "[phone removed]",
    ContactKind.EMAIL: "[email removed]",
    ContactKind.ADDRESS: "[address removed]",
}
_SEAL = object()


@dataclass(frozen=True)
class KnownValues:
    """One account's own private values. Never shown in a repr."""

    phones: tuple[str, ...] = field(default=(), repr=False)
    emails: tuple[str, ...] = field(default=(), repr=False)
    addresses: tuple[str, ...] = field(default=(), repr=False)

    def __add__(self, other: "KnownValues") -> "KnownValues":
        return KnownValues(
            self.phones + other.phones,
            self.emails + other.emails,
            self.addresses + other.addresses,
        )


class RedactedText:
    """Text that has passed `redact`. Immutable; `value` is safe to send or to store.

    `source_length` is the length of the text before redaction, which the input caps count.
    """

    __slots__ = ("_value", "_source_length")

    def __init__(self, seal: object, value: str, source_length: int) -> None:
        if seal is not _SEAL:
            raise TypeError("RedactedText is made only by redact()")
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_source_length", source_length)

    def __init_subclass__(cls, **kwargs) -> None:
        raise TypeError("RedactedText cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("RedactedText is immutable")

    @property
    def value(self) -> str:
        return self._value

    @property
    def source_length(self) -> int:
        return self._source_length

    def __repr__(self) -> str:
        return f"RedactedText(<{len(self._value)} chars>)"


def _replace(text: str, findings) -> str:
    """Replace each finding with its placeholder; overlapping findings become one."""
    merged: list[list] = []
    for found in sorted(findings, key=lambda f: (f.start, -f.end)):
        if merged and found.start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], found.end)
        else:
            merged.append([found.start, found.end, found.kind])
    for start, end, kind in reversed(merged):
        text = text[:start] + PLACEHOLDERS[kind] + text[end:]
    return text


def redact(text: str, known_values: KnownValues) -> RedactedText:
    """Raises TypeError for a non-string and ValueError for text beyond the pattern limit."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(known_values, KnownValues):
        raise TypeError("known_values must be KnownValues")
    exact = [
        *find_known_values(text, ContactKind.EMAIL, known_values.emails),
        *find_known_values(text, ContactKind.PHONE, known_values.phones),
        *find_known_values(text, ContactKind.ADDRESS, known_values.addresses),
    ]
    clean = _replace(text, exact)
    clean = _replace(clean, find_contact_details(clean))
    return RedactedText(_SEAL, clean, len(text))
