from django.apps import AppConfig


class MessagingConfig(AppConfig):
    name = "apps.messaging"
    label = "messaging"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.kernel import audit
        from apps.kernel import erasure as erasers
        from apps.messaging import erasure

        audit.register_action("messaging.account_erased", {})
        erasers.register("messaging", erasure.erase_account_data)
