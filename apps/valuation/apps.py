from django.apps import AppConfig


class ValuationConfig(AppConfig):
    name = "apps.valuation"
    label = "valuation"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.kernel import events, retention
        from apps.listings.events import ListingDeleted
        from apps.valuation import retention as estimate_retention
        from apps.valuation import subscribers

        if not events.is_subscribed(ListingDeleted, subscribers.on_listing_deleted):
            events.subscribe(ListingDeleted, subscribers.on_listing_deleted)
        retention.register("valuation.estimates", estimate_retention.purge_estimates)
