"""Write path for catalog data. Only this module (and data migrations) writes catalog tables."""

import re
import time
import unicodedata

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from apps.catalog import knowledge
from apps.catalog.models import (
    AMENITY_KINDS,
    LOCALITY_KINDS,
    Amenity,
    City,
    KnowledgeChunk,
    KnowledgeDocument,
    Locality,
)
from apps.catalog.selectors import AmenityRef, CityRef, LocalityRef, get_city
from apps.kernel import audit
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.ai import gateway
from apps.kernel.ai.redaction import KnownValues, redact
from apps.kernel.errors import DomainError

NAME_MIN, NAME_MAX = 2, 80
_JOINERS = " '-"


def _clean_name(name, digits: bool = False) -> str:
    """NFC, single spaces; letters (and their combining marks), spaces, hyphens, apostrophes
    (and, for a locality, decimal digits: `DHA Phase 6`; never parentheses).

    The name must start with a letter and end with a letter, a digit (locality only) or a
    combining mark.
    """
    if not isinstance(name, str):
        raise DomainError("validation_error")
    name = " ".join(unicodedata.normalize("NFC", name).split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise DomainError("validation_error")
    for ch in name:
        cat = unicodedata.category(ch)
        if not (
            cat.startswith("L") or cat in ("Mn", "Mc") or ch in _JOINERS or (digits and cat == "Nd")
        ):
            raise DomainError("validation_error")
    if not unicodedata.category(name[0]).startswith("L") or name[-1] in _JOINERS:
        raise DomainError("validation_error")
    return name


def _name_taken(name: str) -> bool:
    return City.objects.annotate(lowered=Lower("name")).filter(lowered=name.lower()).exists()


def create_city(actor: Actor, name: str) -> CityRef:
    """Administrators add a city; the audit record is written in the same transaction."""
    require_admin(actor)
    name = _clean_name(name)
    with transaction.atomic():
        if _name_taken(name):
            raise DomainError("validation_error")
        try:
            with transaction.atomic():
                city = City.objects.create(name=name)
        except IntegrityError:  # lost a race to the unique constraint
            raise DomainError("validation_error") from None
        audit.record(actor, "city.created", "city", city.id)
    return CityRef(id=city.id, name=city.name)


def _locality_taken(city_id, name: str) -> bool:
    return (
        Locality.objects.filter(city_id=city_id)
        .annotate(lowered=Lower("name"))
        .filter(lowered=name.lower())
        .exists()
    )


def create_locality(actor: Actor, city_id, name: str, kind: str) -> LocalityRef:
    """Administrators add an area or society to an active city; audited as `locality.created`
    in the same transaction. An unknown or inactive city, a bad name or kind and a name the city
    already has (ignoring case) are `validation_error`."""
    require_admin(actor)
    name = _clean_name(name, digits=True)
    if not isinstance(kind, str) or kind not in LOCALITY_KINDS:
        raise DomainError("validation_error")
    city = get_city(city_id)
    if city is None:
        raise DomainError("validation_error")
    with transaction.atomic():
        if _locality_taken(city.id, name):
            raise DomainError("validation_error")
        try:
            with transaction.atomic():
                row = Locality.objects.create(city_id=city.id, name=name, kind=kind)
        except IntegrityError:  # lost a race to the unique constraint
            raise DomainError("validation_error") from None
        audit.record(actor, "locality.created", "locality", row.id)
    return LocalityRef(id=row.id, name=row.name, kind=row.kind, city_id=row.city_id)


_CODE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
LABEL_MIN, LABEL_MAX = (
    2,
    80,
)  # the database CHECK only enforces the length; control characters are the service rule


def create_amenity(
    actor: Actor, code: str, label: str, kind: str, group: str, sort_order: int = 0
) -> AmenityRef:
    """Administrators add an amenity to the catalogue; audited as `amenity.created` in the same
    transaction. A code or group that is not snake_case, a label outside 2 to 80 characters or with
    control characters, an unknown kind and a code already taken are `validation_error`."""
    require_admin(actor)
    if (
        not isinstance(code, str)
        or not isinstance(group, str)
        or not _CODE.fullmatch(code)
        or not _CODE.fullmatch(group)
        or not isinstance(kind, str)
        or kind not in AMENITY_KINDS
        or not isinstance(sort_order, int)
        or isinstance(sort_order, bool)
        or not -(2**31) <= sort_order < 2**31  # the column is a 32-bit integer
        or not isinstance(label, str)
    ):
        raise DomainError("validation_error")
    label = " ".join(unicodedata.normalize("NFC", label).split())
    if not LABEL_MIN <= len(label) <= LABEL_MAX or any(
        unicodedata.category(ch)[0] == "C" for ch in label
    ):
        raise DomainError("validation_error")
    with transaction.atomic():
        if Amenity.objects.filter(code=code).exists():
            raise DomainError("validation_error")
        try:
            with transaction.atomic():
                row = Amenity.objects.create(
                    code=code, label=label, kind=kind, group=group, sort_order=sort_order
                )
        except IntegrityError:  # lost a race to the unique constraint
            raise DomainError("validation_error") from None
        audit.record(actor, "amenity.created", "amenity", row.id)
    return AmenityRef(code=row.code, label=row.label, kind=row.kind, group=row.group)


KNOWLEDGE_BATCH = 50  # passages per embedding request


class KnowledgeIndexError(Exception):
    """The passages could not be embedded; the existing index is unchanged."""

    def __init__(self, outcome: str, status: int | None = None) -> None:
        super().__init__(f"embedding failed: {outcome}")
        self.outcome = outcome
        self.status = status  # the provider's HTTP status, when it gave one


def rebuild_knowledge_index(actor: Actor, *, pause: float = 0.0) -> int:
    """Embed every authored passage through the gateway (flag, budget, request log) and replace
    the index in one transaction; returns the number of passages. System or administrator only.
    `pause` seconds are waited between requests, for a provider's per-minute limit. Any failure
    raises `KnowledgeIndexError` and leaves the previous index in place."""
    require_signed_in(actor)
    if actor.kind not in (ActorKind.SYSTEM, ActorKind.ADMIN):
        raise DomainError("not_found")
    documents = knowledge.load_documents()
    passages = [(d, p) for d in documents for p in d.passages]
    model = gateway.embedding_model_key()
    vectors: list[list[float]] = []
    for start in range(0, len(passages), KNOWLEDGE_BATCH):
        if start and pause:
            time.sleep(pause)
        # Our own authored text: there are no account values to scrub.
        texts = [
            redact(f"{p.title}. {p.text}", KnownValues())
            for _, p in passages[start : start + KNOWLEDGE_BATCH]
        ]
        result = gateway.embed(actor, texts)
        if not result.ok:
            raise KnowledgeIndexError(result.outcome, result.status)
        if len(result.value) != len(texts):
            raise KnowledgeIndexError("error")
        vectors.extend(result.value)
    if len({len(v) for v in vectors}) != 1 or not vectors[0]:
        raise KnowledgeIndexError("error")
    with transaction.atomic():
        KnowledgeDocument.objects.all().delete()  # chunks cascade
        KnowledgeDocument.objects.bulk_create(
            KnowledgeDocument(
                slug=d.slug, title=d.title, version=d.version, provenance=d.provenance
            )
            for d in documents
        )
        KnowledgeChunk.objects.bulk_create(
            KnowledgeChunk(
                passage_id=p.id,
                document_id=d.slug,
                title=p.title,
                text=p.text,
                embedding=vector,
                embedding_model=model,
            )
            for (d, p), vector in zip(passages, vectors, strict=True)
        )
    return len(passages)
