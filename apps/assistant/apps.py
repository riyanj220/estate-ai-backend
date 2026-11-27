from django.apps import AppConfig


class AssistantConfig(AppConfig):
    name = "apps.assistant"
    label = "assistant"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.assistant import erasure, subscribers
        from apps.assistant import retention as history_retention
        from apps.kernel import audit, events, retention
        from apps.kernel import erasure as erasers
        from apps.listings.events import ListingDeleted

        if not events.is_subscribed(ListingDeleted, subscribers.on_listing_deleted):
            events.subscribe(ListingDeleted, subscribers.on_listing_deleted)
        audit.register_action("assistant.account_erased", {})
        erasers.register("assistant", erasure.erase_account_data)
        retention.register("assistant.history", history_retention.purge_orphaned)
        retention.register("assistant.published_history", history_retention.purge_published)
