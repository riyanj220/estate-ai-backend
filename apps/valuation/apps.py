from django.apps import AppConfig


class ValuationConfig(AppConfig):
    name = "apps.valuation"
    label = "valuation"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.kernel import events, retention
        from apps.listings.events import (
            ListingDeleted,
            ListingModelInputsChanged,
            ListingPublished,
        )
        from apps.valuation import retention as estimate_retention
        from apps.valuation import subscribers

        for event, handler in (
            (ListingDeleted, subscribers.on_listing_deleted),
            (ListingPublished, subscribers.on_listing_published),
            (ListingModelInputsChanged, subscribers.on_model_inputs_changed),
        ):
            if not events.is_subscribed(event, handler):
                events.subscribe(event, handler)
        retention.register("valuation.estimates", estimate_retention.purge_estimates)
