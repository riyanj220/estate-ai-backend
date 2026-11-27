from django.apps import AppConfig


class ValuationConfig(AppConfig):
    name = "apps.valuation"
    label = "valuation"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.kernel import audit, events, retention
        from apps.kernel import erasure as erasers
        from apps.listings.events import (
            ListingDeleted,
            ListingModelInputsChanged,
            ListingPublished,
        )
        from apps.valuation import erasure, subscribers
        from apps.valuation import retention as estimate_retention

        for event, handler in (
            (ListingDeleted, subscribers.on_listing_deleted),
            (ListingPublished, subscribers.on_listing_published),
            (ListingModelInputsChanged, subscribers.on_model_inputs_changed),
        ):
            if not events.is_subscribed(event, handler):
                events.subscribe(event, handler)
        audit.register_action("valuation.account_erased", {})
        erasers.register("valuation", erasure.erase_account_data)
        retention.register("valuation.estimates", estimate_retention.purge_estimates)
