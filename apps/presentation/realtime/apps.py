from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    """The WebSocket side of the presentation layer. It owns no tables."""

    name = "apps.presentation.realtime"
    label = "realtime"

    def ready(self):
        from apps.presentation.realtime import subscribers

        subscribers.register()
