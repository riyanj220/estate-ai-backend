"""Word error rate of a transcript against what was said (Story 6.3): reported, never gated."""

import re


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def errors(reference: str, heard: str) -> tuple[int, int]:
    """(substitutions + insertions + deletions, words in the reference)."""
    said, got = words(reference), words(heard)
    row = list(range(len(got) + 1))
    for i, word in enumerate(said, 1):
        diagonal, row[0] = row[0], i
        for j, other in enumerate(got, 1):
            diagonal, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, diagonal + (word != other))
    return row[-1], len(said)
