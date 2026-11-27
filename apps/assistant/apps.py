from django.apps import AppConfig


class AssistantConfig(AppConfig):
    name = "apps.assistant"
    label = "assistant"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.assistant import retention as history_retention
        from apps.assistant import subscribers
        from apps.kernel import events, retention
        from apps.listings.events import ListingDeleted

        if not events.is_subscribed(ListingDeleted, subscribers.on_listing_deleted):
            events.subscribe(ListingDeleted, subscribers.on_listing_deleted)
        retention.register("assistant.history", history_retention.purge_orphaned)
