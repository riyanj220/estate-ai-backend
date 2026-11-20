from django.apps import AppConfig


class ListingsConfig(AppConfig):
    name = "apps.listings"
    label = "listings"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.catalog import registry
        from apps.kernel import audit

        # Metadata holds the three enumerated registry values only: no free text, no address.
        audit.register_action(
            "listing.draft_created",
            {
                "purpose": registry.purposes(),
                "category": [c.id for c in registry.categories()],
                "subtype": registry.all_subtypes(),
            },
        )
