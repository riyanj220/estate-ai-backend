from django.apps import AppConfig
from django.conf import settings


class KernelConfig(AppConfig):
    name = "apps.kernel"
    label = "kernel"

    def ready(self):
        from apps.kernel import (
            ai_log,
            flags,
            idempotency,
            media,
            notifications,
            ratelimit,
            retention,
            subscribers,
        )
        from apps.kernel.ai import budget
        from apps.kernel.ai.adapters import get_adapter

        budget.validate_limits(settings.AI_DAILY_BUDGET, settings.AI_RESERVED_PERCENT)
        get_adapter()  # an unknown AI_PROVIDER fails at start-up

        flags.register_flag("ai_gateway", True, "Master switch for every AI provider call.")
        retention.register("kernel.idempotency", idempotency.purge_expired)
        retention.register("kernel.ratelimit", ratelimit.purge_expired)
        retention.register("kernel.ai_request_log", ai_log.purge_expired)
        retention.register("kernel.ai_budget", budget.purge_expired)
        retention.register("kernel.notifications", notifications.purge_expired)
        retention.register("kernel.media_orphans", media.purge_orphans)
        subscribers.ensure_subscribed()
