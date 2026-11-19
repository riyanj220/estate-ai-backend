from django.apps import AppConfig


class IdentityConfig(AppConfig):
    name = "apps.identity"
    label = "identity"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.identity.models import AccountType
        from apps.kernel import audit

        # Metadata holds enumerated values only: never email, phone, hash or the suspension reason.
        audit.register_action("account.created", {"account_type": [t.value for t in AccountType]})
        for action in (
            "account.email_verified",
            "account.suspended",
            "account.reactivated",
            "account.deactivated",
        ):
            audit.register_action(action, {})
