from django.apps import AppConfig
from django.conf import settings


class KernelConfig(AppConfig):
    name = "apps.kernel"
    label = "kernel"

    def ready(self):
        from apps.kernel import (
            ai_log,
            idempotency,
            media,
            notifications,
            ratelimit,
            retention,
            subscribers,
        )
        from apps.kernel.ai import budget, oversight
        from apps.kernel.ai.adapters import get_adapter

        budget.validate_limits(settings.AI_DAILY_BUDGET, settings.AI_RESERVED_PERCENT)
        get_adapter()  # an unknown AI_PROVIDER fails at start-up

        oversight.register()
        retention.register("kernel.idempotency", idempotency.purge_expired)
        retention.register("kernel.ratelimit", ratelimit.purge_expired)
        retention.register("kernel.ai_request_log", ai_log.purge_expired)
        retention.register("kernel.ai_budget", budget.purge_expired)
        retention.register("kernel.notifications", notifications.purge_expired)
        retention.register("kernel.media_orphans", media.purge_orphans)
        subscribers.ensure_subscribed()
