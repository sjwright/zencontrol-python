"""Identity log for controllers heard on the event plane but not yet subscribed.

Owns discovery bookkeeping only — no routing, leases, or transports. The event
receiver appends here on the no-subscription branch; the interface layer
enriches and reads through this object directly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from ..io.event import ZenEvent
from .models import (
    DEFAULT_CONTROLLER_PORT,
    DiscoveredController,
    mac_bytes_to_str,
    mac_key,
)

DiscoveredHandler = Callable[[DiscoveredController], Awaitable[None]]


class IdentityLog:
    """Sightings of unsubscribed controllers (host/mac/label/last_seen)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.on_discovered: DiscoveredHandler | None = None
        self._entries: dict[str, DiscoveredController] = {}

    @property
    def discovered(self) -> list[DiscoveredController]:
        return list(self._entries.values())

    def clear(self) -> None:
        self._entries.clear()

    def heard_since(self, since: float) -> list[DiscoveredController]:
        """Identities with a packet at or after ``since`` (discover window filter)."""
        return [d for d in self._entries.values() if d.last_seen >= since]

    def get(self, *, host: str | None = None, mac: bytes | str | None = None) -> DiscoveredController | None:
        if mac is not None:
            return self._entries.get(mac_key(mac))
        if host is not None:
            for d in self._entries.values():
                if d.host == host:
                    return d
        return None

    def forget(self, *, host: str | None = None, mac: bytes | str | None = None) -> None:
        key = mac_key(mac) if mac is not None else None
        self._entries = {
            k: d
            for k, d in self._entries.items()
            if not ((host is not None and d.host == host) or (key is not None and k == key))
        }

    def replace(self, discovered: DiscoveredController) -> None:
        """Replace a stored identity (e.g. after a command-plane label probe)."""
        key = mac_key(discovered.mac)
        if key not in self._entries:
            return
        self._entries[key] = discovered

    async def record(self, event: ZenEvent) -> None:
        """Append or refresh a sighting from an unrouted event."""
        mac_str = mac_bytes_to_str(event.mac)
        key = mac_key(mac_str)
        now = event.received_at or time.time()
        existing = self._entries.get(key)
        if existing is not None:
            # Refresh sighting time (and host) without re-firing on_discovered.
            self._entries[key] = DiscoveredController(
                host=event.host,
                mac=existing.mac,
                label=existing.label,
                port=existing.port,
                first_seen=existing.first_seen,
                last_seen=now,
            )
            return

        discovered = DiscoveredController(
            host=event.host,
            mac=mac_str,
            label=None,
            port=DEFAULT_CONTROLLER_PORT,
            first_seen=now,
            last_seen=now,
        )
        self._entries[key] = discovered
        self.logger.info("Identified controller %s mac=%s", event.host, mac_str)
        if callable(self.on_discovered):
            try:
                await self.on_discovered(discovered)
            except Exception as err:
                self.logger.error("on_discovered callback error: %s", err, exc_info=True)
