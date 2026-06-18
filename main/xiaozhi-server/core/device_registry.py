import asyncio
from typing import Optional


class DeviceConnectionRegistry:
    """In-memory registry for currently connected device WebSocket handlers."""

    def __init__(self):
        self._connections = {}
        self._lock = asyncio.Lock()

    async def register(self, conn):
        identifiers = self._identifiers_for(conn)
        if not identifiers:
            return
        async with self._lock:
            for identifier in identifiers:
                self._connections[identifier] = conn

    async def unregister(self, conn):
        async with self._lock:
            stale_keys = [
                key for key, candidate in self._connections.items() if candidate is conn
            ]
            for key in stale_keys:
                self._connections.pop(key, None)

    def get(self, device_identifier: str) -> Optional[object]:
        if not device_identifier:
            return None
        return self._connections.get(device_identifier)

    def active_identifiers(self) -> list[str]:
        return sorted(self._connections.keys())

    def _identifiers_for(self, conn) -> set:
        identifiers = set()
        device_id = getattr(conn, "device_id", None)
        if device_id:
            identifiers.add(device_id)
        headers = getattr(conn, "headers", {}) or {}
        client_id = headers.get("client-id")
        if client_id:
            identifiers.add(client_id)
        return identifiers
