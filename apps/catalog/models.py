import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.functions import Lower


class City(models.Model):
    """A city agencies and listings can choose. Written only by `catalog.services`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("name"), name="catalog_city_name_ci_uniq"),
            models.CheckConstraint(
                condition=models.Q(name__regex=r"^.{2,80}$"), name="catalog_city_name_length"
            ),
        ]


LOCALITY_KINDS = ("area", "society")


class Locality(models.Model):
    """An area or society inside a city. Written only by `catalog.services` (and data
    migrations). `city` is an in-app key: other apps hold the locality id as a plain UUID."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name="localities")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=16)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "city", Lower("name"), name="catalog_locality_city_name_ci_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(name__regex=r"^.{2,80}$"), name="catalog_locality_name_length"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=list(LOCALITY_KINDS)),
                name="catalog_locality_kind_valid",
            ),
        ]


AMENITY_KINDS = ("on_property", "community", "nearby")


class Amenity(models.Model):
    """An amenity or nearby facility a listing can declare (Story 2.8). `code` is the stable
    snake_case identifier clients and listings store; `label`, `group` and `sort_order` are display
    data. Written only by `catalog.services` (and data migrations). Other apps hold the code as
    plain text, never a key. Kind `nearby` entries are always the seller's own claim."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=48, unique=True)
    label = models.CharField(max_length=80)
    kind = models.CharField(max_length=16)
    group = models.CharField(max_length=48)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[a-z][a-z0-9_]{1,47}$"),
                name="catalog_amenity_code_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=list(AMENITY_KINDS)),
                name="catalog_amenity_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(label__regex=r"^.{2,80}$"), name="catalog_amenity_label_length"
            ),
            models.CheckConstraint(
                condition=models.Q(group__regex=r"^[a-z][a-z0-9_]{1,47}$"),
                name="catalog_amenity_group_shape",
            ),
        ]


class KnowledgeDocument(models.Model):
    """One authored knowledge source (Story 5.5). Written only by `catalog.services` from the
    files in `catalog/knowledge_data`."""

    slug = models.CharField(max_length=64, primary_key=True)
    title = models.CharField(max_length=120)
    version = models.PositiveIntegerField()
    provenance = models.CharField(max_length=300)


class KnowledgeChunk(models.Model):
    """One indexed passage and its vector. The vector is a plain float array compared in process
    (a knowledge base this small needs no vector index); `embedding_model` names the model that
    made it, so vectors of different models are never compared."""

    passage_id = models.CharField(max_length=64, primary_key=True)
    document = models.ForeignKey(KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks")
    title = models.CharField(max_length=120)
    text = models.TextField()
    embedding = ArrayField(models.FloatField())
    embedding_model = models.CharField(max_length=100)
