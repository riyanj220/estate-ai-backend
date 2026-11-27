"""The receive-only live channel (AD-13).

A socket joins one group, that of its own authenticated account. Before every event it forwards,
and every `WS_RECHECK_SECONDS`, it checks the session and account again, so a revoked session or a
suspension stops delivery even when no signal reached this process. Nothing here is logged but
exception class names.
"""

import asyncio
import logging
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from apps.presentation.realtime import auth, protocol

logger = logging.getLogger("estate.realtime")

# Open sockets per account. In process, like the channel layer: one server process only (AD-14).
_open: dict[uuid.UUID, int] = {}


def group_of(account_id) -> str:
    return f"account.{account_id}"


class LiveConsumer(AsyncJsonWebsocketConsumer):
    account_id: uuid.UUID | None = None
    _token = ""
    _closing = False
    _joined = False
    _watcher: asyncio.Task | None = None

    async def connect(self):
        if not auth.origin_allowed(self.scope):
            await self.close()  # before accepting: the handshake is refused (403)
            return
        self._token = auth.session_token(self.scope)
        account_id = await database_sync_to_async(auth.live_account)(self._token)
        if account_id is None:
            await self.close()
            return
        if _open.get(account_id, 0) >= settings.WS_MAX_CONNECTIONS_PER_ACCOUNT:
            await self.accept()
            await self.close(code=protocol.CLOSE_TOO_MANY)
            return
        _open[account_id] = _open.get(account_id, 0) + 1
        self.account_id, self._joined = account_id, True
        await self.channel_layer.group_add(group_of(account_id), self.channel_name)
        await self.accept()
        self._last_event = asyncio.get_running_loop().time()
        self._watcher = asyncio.create_task(self._watch())
        await self.send_json(protocol.ready())

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        await self._end(protocol.CLOSE_RECEIVE_ONLY)  # never parsed

    async def disconnect(self, code):
        await self._leave()

    # -- events from the channel layer ----------------------------------------------------

    async def live_event(self, message):
        if self._closing or not await self._still_allowed():
            return
        self._last_event = asyncio.get_running_loop().time()
        await self.send_json(message["event"])

    async def session_check(self, message):
        if not self._closing:
            await self._still_allowed()

    # -- helpers ----------------------------------------------------------------------------

    async def _still_allowed(self) -> bool:
        try:
            allowed = await database_sync_to_async(auth.live_account)(self._token)
        except Exception as exc:  # unsure means no
            logger.warning(
                "live_check_failure",
                extra={"event": "live_check_failure", "exception_class": type(exc).__name__},
            )
            await self._end(1011)
            return False
        if allowed != self.account_id:
            await self._end(protocol.CLOSE_SESSION_ENDED)
            return False
        return not self._closing

    async def _watch(self):
        loop = asyncio.get_running_loop()
        checked = loop.time()
        while not self._closing:
            idle_at = self._last_event + settings.WS_IDLE_SECONDS
            check_at = checked + settings.WS_RECHECK_SECONDS
            await asyncio.sleep(max(0.01, min(idle_at, check_at) - loop.time()))
            if loop.time() >= self._last_event + settings.WS_IDLE_SECONDS:
                await self._end(protocol.CLOSE_IDLE)
            elif loop.time() >= checked + settings.WS_RECHECK_SECONDS:
                checked = loop.time()
                await self._still_allowed()

    async def _end(self, code: int):
        if self._closing:
            return
        await self._leave()
        await self.close(code=code)

    async def _leave(self):
        """Stop delivery and free the connection slot; safe to call more than once."""
        self._closing = True
        watcher, self._watcher = self._watcher, None
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
        if not self._joined:
            return
        self._joined = False
        remaining = _open.get(self.account_id, 1) - 1
        if remaining > 0:
            _open[self.account_id] = remaining
        else:
            _open.pop(self.account_id, None)
        await self.channel_layer.group_discard(group_of(self.account_id), self.channel_name)
