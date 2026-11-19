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
