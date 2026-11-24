import uuid

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
