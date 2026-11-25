from django.apps import AppConfig


class ListingsConfig(AppConfig):
    name = "apps.listings"
    label = "listings"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.catalog import registry
        from apps.kernel import audit, notifications, retention
        from apps.listings import lifecycle
        from apps.listings import retention as draft_retention

        # Metadata holds the three enumerated registry values only: no free text, no address.
        audit.register_action(
            "listing.draft_created",
            {
                "purpose": registry.purposes(),
                "category": [c.id for c in registry.categories()],
                "subtype": registry.all_subtypes(),
            },
        )
        # The id and the reason only; never a title, address or other content.
        audit.register_action("listing.published", {})  # the Listing id is the audit target
        # Status moves out of Published: the two statuses only (no content, no reason text).
        audit.register_action(
            "listing.status_changed",
            {"from": lifecycle.MOVE_SOURCES, "to": lifecycle.MOVE_TARGETS},
        )
        audit.register_action("listing.draft_deleted", {"reason": ["owner", "retention"]})
        audit.register_action("listing.sample_removed", {})  # sample-data reset: the id only
        # The Listing id and the days left; the frontend renders a fixed sentence from the kind.
        notifications.register_kind(
            "listing.draft_expiring",
            {"listing_id": notifications.UUID_TEXT, "days_left": notifications.int_range(0, 365)},
        )
        retention.register("listings.draft_notice", draft_retention.draft_notice)
        retention.register("listings.draft_expiry", draft_retention.draft_expiry)
