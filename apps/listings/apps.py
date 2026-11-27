from django.apps import AppConfig


class ListingsConfig(AppConfig):
    name = "apps.listings"
    label = "listings"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.catalog import registry
        from apps.kernel import audit, notifications, retention
        from apps.kernel import erasure as erasers
        from apps.kernel.ai import known_values
        from apps.kernel.privacy.reads import register_private_target
        from apps.listings import erasure, lifecycle, publication
        from apps.listings import retention as draft_retention
        from apps.listings.models import QUOTA_ACCOUNT_TYPES
        from apps.listings.selectors import known_private_addresses

        known_values.register("listings", known_private_addresses)
        register_private_target("listing_draft", "A Draft Listing's content")
        register_private_target("listing_address", "A Listing's Private address")
        # Moderation (Story 9.4): the Listing id is the target; the reason stays in `governance`.
        for action in (
            "listing.restricted",
            "listing.restriction_lifted",
            "listing.removed",
            "listing.restored",
            "listing.removal_archived",
        ):
            audit.register_action(action, {})

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
        # A republication: the Listing id is the target; the metadata is the event kind only.
        audit.register_action(
            "listing.republished", {"kind": ["republish_counted", "republish_free"]}
        )
        # A one-time correction: the Listing id is the target; which fields changed, never values.
        audit.register_action(
            "listing.corrected",
            {"locality": bool, "area_value": bool, "area_unit": bool, "marla_basis": bool},
        )
        # Status moves out of Published: the two statuses only (no content, no reason text).
        audit.register_action(
            "listing.status_changed",
            {"from": lifecycle.MOVE_SOURCES, "to": lifecycle.MOVE_TARGETS},
        )
        audit.register_action("listing.draft_deleted", {"reason": ["owner", "retention"]})
        audit.register_action("listing.sample_removed", {})  # sample-data reset: the id only
        audit.register_action("listings.account_erased", {})  # the account id is the target
        erasers.register("listings", erasure.erase_account_data)
        # An administrator's limit change; the target is the new policy row.
        audit.register_action(
            "quota.policy_changed",
            {"account_type": QUOTA_ACCOUNT_TYPES, "old_limit": int, "new_limit": int},
        )
        # The Listing id and the days left; the frontend renders a fixed sentence from the kind.
        notifications.register_kind(
            "listing.draft_expiring",
            {"listing_id": notifications.UUID_TEXT, "days_left": notifications.int_range(0, 365)},
        )
        retention.register("listings.draft_notice", draft_retention.draft_notice)
        retention.register("listings.draft_expiry", draft_retention.draft_expiry)
        retention.register("listings.view_buckets", draft_retention.view_buckets)
        retention.register("listings.publication_events", publication.purge_old_events)
