"""WebSocket broadcaster for /events clients."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class Broadcaster:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message)
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for c in clients:
            try:
                await c.send_text(text)
            except Exception:
                dead.append(c)
        for c in dead:
            await self.unregister(c)

    @property
    def client_count(self) -> int:
        return len(self._clients)