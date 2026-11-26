from django.apps import AppConfig


class IdentityConfig(AppConfig):
    name = "apps.identity"
    label = "identity"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.identity import (
            email_change,
            password_reset,
            passwords,
            sessions,
            subscribers,
            verification,
        )
        from apps.identity.models import AccountType
        from apps.kernel import audit, notifications, retention
        from apps.kernel.privacy.reads import register_private_target

        # Metadata holds enumerated values only: never email, phone, hash or the suspension reason.
        audit.register_action("account.created", {"account_type": [t.value for t in AccountType]})
        for action in (
            "account.email_verified",
            "account.suspended",
            "account.reactivated",
            "account.deactivated",
            "account.sample_removed",
        ):
            audit.register_action(action, {})
        for action in ("account.password_reset", "account.password_changed"):
            audit.register_action(action, {})  # no metadata: never a password, token or email

        for action in (
            "account.profile_updated",
            "account.email_changed",
            "account.profile_image_changed",
        ):
            audit.register_action(action, {})  # no metadata: never a phone, name, email or file

        for action in (
            "admin.provisioned",
            "admin.enabled",
            "admin.password_reset",
            "admin.signed_in",
            "admin.sign_in_failed",
        ):
            audit.register_action(action, {})  # no metadata: never an email, password or hash

        for action in ("admin.disabled", "admin.removed"):
            audit.register_action(
                action, {"forced": ["yes", "no"]}
            )  # whether --force-last was needed

        # In-app counterparts of the old-address mail notices; empty payloads (no private data).
        for kind in ("account.password_changed", "account.email_changed"):
            notifications.register_kind(kind, {})
        # Never the reason: the account reads it from its own session summary.
        for kind in ("account.suspended", "account.reactivated"):
            notifications.register_kind(kind, {})

        register_private_target("account", "An account's email, name and state")

        subscribers.register()
        retention.register("identity.email_verification_tokens", verification.purge_expired)
        retention.register("identity.password_reset_tokens", password_reset.purge_expired)
        retention.register("identity.email_change_tokens", email_change.purge_expired)
        retention.register("identity.sessions", sessions.purge_stale)
        passwords.dummy_hash()
