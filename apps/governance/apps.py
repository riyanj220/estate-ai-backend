from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    name = "apps.governance"
    label = "governance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.governance import reports, subscribers
        from apps.governance.moderation import NOTIFICATION_KINDS
        from apps.identity.events import AccountSessionEnded
        from apps.kernel import audit, events, notifications, retention
        from apps.kernel.privacy.reads import register_private_target
        from apps.listings.events import ListingDeleted, ListingPublished

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
            "report.snapshot_anonymised",
        ):
            audit.register_action(action, {})
        # Story 9.5: reported Messages are read only with a recorded reason.
        register_private_target("report_snapshot", "The Messages of a Conversation Report")
        retention.register("governance.report_snapshots", reports.purge_deactivated_snapshots)
        # Stories 9.5 and 9.6.
        for event, handler in (
            (AccountSessionEnded, subscribers.on_session_ended),
            (ListingPublished, subscribers.on_listing_published),
            (ListingDeleted, subscribers.on_listing_deleted),
        ):
            if not events.is_subscribed(event, handler):
                events.subscribe(event, handler)
        for kind in ("report.resolved", "report.dismissed"):
            notifications.register_kind(kind, {})
        # Moderation (Story 9.4): the owner is told which Listing and why.
        for kind in NOTIFICATION_KINDS:
            notifications.register_kind(
                kind,
                {"listing_id": notifications.UUID_TEXT, "reason": notifications.SHORT_TEXT},
            )
