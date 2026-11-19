from django.apps import AppConfig


class KernelConfig(AppConfig):
    name = "apps.kernel"
    label = "kernel"

    def ready(self):
        from apps.kernel import ai_log, flags, idempotency, ratelimit, retention, subscribers

        flags.register_flag("ai_gateway", True, "Master switch for every AI provider call.")
        retention.register("kernel.idempotency", idempotency.purge_expired)
        retention.register("kernel.ratelimit", ratelimit.purge_expired)
        retention.register("kernel.ai_request_log", ai_log.purge_expired)
        subscribers.ensure_subscribed()
