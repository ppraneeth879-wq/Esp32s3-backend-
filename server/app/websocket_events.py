"""WebSocket /events handler for Android/Web clients."""

from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from .broadcaster import Broadcaster

logger = logging.getLogger("lifelog.ws.events")


class EventsWebSocket:
    def __init__(self, broadcaster: Broadcaster):
        self.broadcaster = broadcaster

    async def handle(self, ws: WebSocket) -> None:
        await self.broadcaster.register(ws)
        try:
            while True:
                # keep connection alive; ignore incoming
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await self.broadcaster.unregister(ws)