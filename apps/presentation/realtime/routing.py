"""The one WebSocket path. Anything else is refused without a log line naming it."""

from apps.presentation.realtime.consumers import LiveConsumer

PATH = "/ws"
_consumer = LiveConsumer.as_asgi()


async def websocket_application(scope, receive, send):
    if scope["path"] != PATH:
        await receive()  # websocket.connect
        await send({"type": "websocket.close"})
        return
    await _consumer(scope, receive, send)
