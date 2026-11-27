"""The knowledge base sources (FR-78): authored passages in `knowledge_data/documents/*.yaml`.

Pure reading and validation, plus the content hashes behind `knowledge.lock`: a changed document
must get a higher `version` (`scripts/knowledge_lock.py`). Nothing here touches the database or
an AI provider.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent / "knowledge_data"
DOCUMENTS = DATA / "documents"
LOCK = DATA / "knowledge.lock"
QUESTIONS = DATA / "questions.yaml"
_SLUG = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


@dataclass(frozen=True)
class SourcePassage:
    id: str
    title: str
    text: str


@dataclass(frozen=True)
class SourceDocument:
    slug: str
    title: str
    version: int
    provenance: str
    passages: tuple[SourcePassage, ...]

    def digest(self) -> str:
        """Over everything but the version."""
        content = [self.title, self.provenance, [[p.id, p.title, p.text] for p in self.passages]]
        return hashlib.sha256(json.dumps(content).encode()).hexdigest()


def _text(value, limit: int, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{where}: text of 1 to {limit} characters expected")
    return value.strip()


def load_documents(directory: Path = DOCUMENTS) -> tuple[SourceDocument, ...]:
    """Every source document, in file name order. Raises ValueError on invalid content."""
    documents, seen = [], set()
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        slug, version = path.stem, data.get("version")
        if not _SLUG.fullmatch(slug) or type(version) is not int or version < 1:
            raise ValueError(f"{path.name}: bad file name or version")
        passages = []
        for entry in data.get("passages") or []:
            key = entry.get("id", "")
            if not _SLUG.fullmatch(key) or key in seen:
                raise ValueError(f"{path.name}: bad or repeated passage id {key!r}")
            seen.add(key)
            passages.append(
                SourcePassage(
                    key, _text(entry.get("title"), 120, key), _text(entry.get("text"), 1000, key)
                )
            )
        if not passages:
            raise ValueError(f"{path.name}: no passages")
        documents.append(
            SourceDocument(
                slug,
                _text(data.get("title"), 120, path.name),
                version,
                _text(data.get("provenance"), 300, path.name),
                tuple(passages),
            )
        )
    return tuple(documents)


def read_lock(path: Path = LOCK) -> dict[str, tuple[int, str]]:
    if not path.is_file():
        return {}
    rows = (line.split() for line in path.read_text().splitlines() if line.strip())
    return {slug: (int(version), digest) for slug, version, digest in rows}


def lock_text(documents) -> str:
    return "".join(f"{d.slug} {d.version} {d.digest()}\n" for d in documents)


def unversioned_changes(documents, locked: dict[str, tuple[int, str]]) -> list[str]:
    """Documents whose content changed without a higher version."""
    return [
        d.slug
        for d in documents
        if d.slug in locked and d.digest() != locked[d.slug][1] and d.version <= locked[d.slug][0]
    ]
