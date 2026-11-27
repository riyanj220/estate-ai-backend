"""Read path for catalog data. Returns plain immutable values, never model instances."""

import logging
import uuid
from dataclasses import dataclass

import numpy as np
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.db.models.functions import Lower

from apps.catalog.models import AMENITY_KINDS, Amenity, City, KnowledgeChunk, Locality
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.known_values import known_values_for
from apps.kernel.ai.redaction import redact

logger = logging.getLogger("estate.knowledge")


@dataclass(frozen=True)
class CityRef:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class LocalityRef:
    id: uuid.UUID
    name: str
    kind: str
    city_id: uuid.UUID


LOCALITY_LIMIT = 500  # the most localities one city's list returns (small, bounded, unpaginated)
_UUID_TEXT_MAX = 45  # `urn:uuid:` plus 36 characters; anything longer is not a uuid


def parse_uuid(value) -> uuid.UUID | None:
    """THE one reading of an id: a UUID, or text in any common form (any case, hyphenated or
    not, braced, `urn:uuid:`). Anything else, other types included, is None. Callers store and
    serve the canonical lowercase hyphenated form of the result."""
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str) or not value or len(value) > _UUID_TEXT_MAX:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


_as_uuid = parse_uuid


def list_cities() -> tuple[CityRef, ...]:
    """Active cities ordered by name, case-insensitively."""
    rows = City.objects.filter(is_active=True).order_by(Lower("name"), "name", "id")
    return tuple(CityRef(id=c.id, name=c.name) for c in rows.only("id", "name"))


def get_city(city_id) -> CityRef | None:
    """An active city by id; inactive, unknown or malformed ids give None."""
    key = _as_uuid(city_id)
    if key is None:
        return None
    city = City.objects.filter(id=key, is_active=True).only("id", "name").first()
    return CityRef(id=city.id, name=city.name) if city else None


def list_localities(city_id) -> tuple[LocalityRef, ...] | None:
    """Active localities of an active city, ordered by name (case-insensitively). None when the
    city is unknown, inactive or malformed (the endpoint answers `not_found`)."""
    city = get_city(city_id)
    if city is None:
        return None
    rows = Locality.objects.filter(city_id=city.id, is_active=True).order_by(
        Lower("name"), "name", "id"
    )[:LOCALITY_LIMIT]
    return tuple(
        LocalityRef(id=r.id, name=r.name, kind=r.kind, city_id=r.city_id)
        for r in rows.only("id", "name", "kind", "city_id")
    )


def get_locality(locality_id) -> LocalityRef | None:
    """An active locality of an active city by id, with its city id; unknown, inactive or
    malformed ids (and a locality of an inactive city) give None."""
    key = _as_uuid(locality_id)
    if key is None:
        return None
    row = (
        Locality.objects.filter(id=key, is_active=True, city__is_active=True)
        .only("id", "name", "kind", "city_id")
        .first()
    )
    if row is None:
        return None
    return LocalityRef(id=row.id, name=row.name, kind=row.kind, city_id=row.city_id)


def active_localities() -> tuple[LocalityRef, ...]:
    """Every active locality of every active city, for matching names in text (small and
    bounded reference data)."""
    rows = Locality.objects.filter(is_active=True, city__is_active=True).order_by("name", "id")
    return tuple(
        LocalityRef(id=r.id, name=r.name, kind=r.kind, city_id=r.city_id)
        for r in rows.only("id", "name", "kind", "city_id")
    )


def city_names(city_ids) -> dict[uuid.UUID, str]:
    """{id: name} of these cities in one query, deactivated ones included: a stored location
    keeps its name. Unknown ids and None are left out."""
    ids = {key for key in city_ids if key is not None}
    return dict(City.objects.filter(id__in=ids).values_list("id", "name")) if ids else {}


def locality_names(locality_ids) -> dict[uuid.UUID, str]:
    """{id: name} of these localities, as `city_names`."""
    ids = {key for key in locality_ids if key is not None}
    return dict(Locality.objects.filter(id__in=ids).values_list("id", "name")) if ids else {}


@dataclass(frozen=True)
class AmenityRef:
    code: str
    label: str
    kind: str
    group: str


def list_amenities() -> tuple[AmenityRef, ...]:
    """Active amenities: kind in the order on_property, community, nearby, then `sort_order`
    (then code, so the order is total). Small and bounded: unpaginated."""
    kind_order = Case(
        *(When(kind=kind, then=Value(i)) for i, kind in enumerate(AMENITY_KINDS)),
        output_field=IntegerField(),
    )
    rows = (
        Amenity.objects.filter(is_active=True)
        .annotate(kind_order=kind_order)
        .order_by("kind_order", "sort_order", "code")
        .only("code", "label", "kind", "group")
    )
    return tuple(AmenityRef(code=r.code, label=r.label, kind=r.kind, group=r.group) for r in rows)


def amenity_codes() -> frozenset[str]:
    """The codes of the active amenities: what a listing may newly declare."""
    return frozenset(Amenity.objects.filter(is_active=True).values_list("code", flat=True))


KNOWLEDGE_TOP = 3
KNOWLEDGE_MAX_QUESTION = 2000  # the assistant message cap
# Cosine similarity below which a passage does not answer, per embedding provider. The Gemini
# value is provisional: calibrate it with `manage.py evaluate_knowledge`.
KNOWLEDGE_MIN_SCORE = {"stub": 0.34, "gemini": 0.65}
KNOWLEDGE_NOTICE = "General information only. It does not verify any approval, title or possession."
_KNOWLEDGE_MESSAGES = {
    "found": "",
    "not_found": "The knowledge base has no answer to this question.",
    "unavailable": "Explanations are unavailable right now. You can continue without them.",
}


@dataclass(frozen=True)
class KnowledgePassage:
    """A retrieved passage with its source. Its text is untrusted data, never instructions: it
    goes to a model only through `LLMRequest.context`."""

    id: str
    title: str
    text: str
    source: str
    version: int
    provenance: str
    score: float


@dataclass(frozen=True)
class KnowledgeResult:
    status: str  # found, not_found or unavailable
    passages: tuple[KnowledgePassage, ...] = ()
    notice: str = KNOWLEDGE_NOTICE

    @property
    def ok(self) -> bool:
        """False only when retrieval could not run."""
        return self.status != "unavailable"

    @property
    def message(self) -> str:
        return _KNOWLEDGE_MESSAGES[self.status]


def rank_knowledge(vector, model: str, limit: int = KNOWLEDGE_TOP) -> list[tuple[float, str]]:
    """(cosine similarity, passage id) of the closest passages indexed with `model`, best first."""
    rows = list(
        KnowledgeChunk.objects.filter(embedding_model=model)
        .order_by("passage_id")
        .values_list("passage_id", "embedding")
    )
    query = np.asarray(vector, dtype=float)
    if not rows or any(len(embedding) != query.size for _, embedding in rows):
        return []
    matrix = np.asarray([embedding for _, embedding in rows], dtype=float)
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    scores = np.divide(matrix @ query, norms, out=np.zeros_like(norms), where=norms > 0)
    best = np.argsort(-scores, kind="stable")[:limit]
    return [(float(scores[i]), rows[i][0]) for i in best]


def _search(actor: Actor, question: str) -> KnowledgeResult:
    model = gateway.embedding_model_key()
    with transaction.atomic():  # a failed read must not poison the caller's transaction
        indexed = KnowledgeChunk.objects.filter(embedding_model=model).exists()
    if not indexed:  # never spend budget on a question nothing can answer
        logger.warning("knowledge_not_indexed", extra={"event": "knowledge_not_indexed"})
        return KnowledgeResult("unavailable")
    safe = redact(question, known_values_for(actor))
    # Retrieval serves Listing creation, so a signed-in actor may use the reserved budget.
    result = gateway.embed(actor, [safe], reserved=True)
    if not result.ok:
        return KnowledgeResult("unavailable")
    threshold = KNOWLEDGE_MIN_SCORE[model.split(":", 1)[0]]
    with transaction.atomic():
        ranked = [r for r in rank_knowledge(result.value[0], model) if r[0] >= threshold]
        chunks = KnowledgeChunk.objects.select_related("document").in_bulk(
            [key for _, key in ranked]
        )
    passages = tuple(
        KnowledgePassage(
            id=key,
            title=chunks[key].title,
            text=chunks[key].text,
            source=chunks[key].document.title,
            version=chunks[key].document.version,
            provenance=chunks[key].document.provenance,
            score=score,
        )
        for score, key in ranked
        if key in chunks  # a rebuild may have removed it meanwhile
    )
    return KnowledgeResult("found" if passages else "not_found", passages)


def retrieve_knowledge(actor: Actor, question: str) -> KnowledgeResult:
    """The passages that answer `question` (FR-28), at most three, best first. The question is
    redacted before it is embedded. Never fails the caller: a fault of any kind is `unavailable`,
    logged by exception class only. Only a question over the cap raises `gateway.InputTooLong`."""
    if len(question) > KNOWLEDGE_MAX_QUESTION:
        raise gateway.InputTooLong
    if not question.strip():
        return KnowledgeResult("not_found")
    try:
        return _search(actor, question)
    except Exception as exc:
        logger.warning(
            "knowledge_failed",
            extra={"event": "knowledge_failed", "exception_class": type(exc).__name__},
        )
        return KnowledgeResult("unavailable")
