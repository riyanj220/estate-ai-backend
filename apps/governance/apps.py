from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    name = "apps.governance"
    label = "governance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.governance.moderation import NOTIFICATION_KINDS
        from apps.kernel import audit, notifications
        from apps.kernel.privacy.reads import register_private_target

        # Metadata is empty: never the registration number, note or reason.
        for action in (
            "verification.requested",
            "verification.withdrawn",
            "verification.approved",
            "verification.rejected",
            "verification.revoked",
        ):
            audit.register_action(action, {})
        # Empty payloads: the Agency reads the outcome and any reason on its status page.
        for kind in ("verification.approved", "verification.rejected", "verification.revoked"):
            notifications.register_kind(kind, {})
        register_private_target("verification_request", "An Agency's registration number and note")

        # Reports (Stories 9.1, 9.2): never the description, notes or reporter.
        for action in (
            "report.submitted",
            "report.review_started",
            "report.resolved",
            "report.dismissed",
        ):
            audit.register_action(action, {})
        for kind in ("report.resolved", "report.dismissed"):
            notifications.register_kind(kind, {})
        # Moderation (Story 9.4): the owner is told which Listing and why.
        for kind in NOTIFICATION_KINDS:
            notifications.register_kind(
                kind,
                {"listing_id": notifications.UUID_TEXT, "reason": notifications.SHORT_TEXT},
            )
