"""Multicast discovery + command-plane label enrichment."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from ..api import ZenController as SuperZenController
from ..api.commands import ZenCommandClient
from ..api.event_router import Lease, ZenEventReceiver
from ..api.discovery import DiscoveryLog
from ..api.models import DiscoveredController, mac_key
from ..api.types import Transport
from ..exceptions import ZenTimeoutError


class DiscoveryHost(Protocol):
    """Minimal surface ControllerDiscovery needs from ZenControl."""

    logger: logging.Logger
    commands: ZenCommandClient
    identities: DiscoveryLog
    event_receiver: ZenEventReceiver
    _enrich_locks: dict[str, asyncio.Lock]

    def is_session_running(self) -> bool: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ControllerDiscovery:
    """Identity-log listen window plus optional label probe."""

    def __init__(self, host: DiscoveryHost) -> None:
        self._host = host

    async def enrich_discovered(self, discovered: DiscoveredController) -> DiscoveredController:
        """Probe QUERY_CONTROLLER_LABEL over the command plane and store the result.

        Discovery on the identity log is host/mac only. Call this (or
        discover(), which enriches its return value) when a distinct label
        is needed for UI listing. Uses a temporary controller name for the UDP
        client; does not register the controller.
        """
        h = self._host
        if discovered.label:
            return discovered

        key = mac_key(discovered.mac)
        lock = h._enrich_locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = h.identities.get(mac=discovered.mac) or discovered
            if current.label:
                return current

            probe_name = f"_discover_{key.replace(':', '')}"
            temp = SuperZenController(
                id=f"discover-{key}",
                name=probe_name,
                label="",
                host=discovered.host,
                port=discovered.port,
                mac=discovered.mac,
            )
            label: str | None = None
            try:
                label = await h.commands.query_controller_label(temp)
            except ZenTimeoutError:
                h.logger.info(
                    "Discovered controller %s (%s) but label query timed out",
                    discovered.host,
                    discovered.mac,
                )
            except Exception as err:
                h.logger.warning(
                    "Discovered controller %s (%s) but label query failed: %s",
                    discovered.host,
                    discovered.mac,
                    err,
                )
            finally:
                await h.commands._invalidate_client(temp)

            if not label:
                return current

            enriched = DiscoveredController(
                host=current.host,
                mac=current.mac,
                label=label,
                port=current.port,
                first_seen=current.first_seen,
                last_seen=current.last_seen,
            )
            h.identities.replace(enriched)
            h.logger.info(
                "Enriched discovered controller %s mac=%s label=%r",
                enriched.host,
                enriched.mac,
                enriched.label,
            )
            return enriched

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredController]:
        """Listen for multicast and return controllers heard within timeout seconds.

        Starts event monitoring if needed. Works with zero registered controllers
        and also reports unregistered controllers while already running. Returns
        identities with a packet in this window (last_seen), so a second call
        on a long-lived instance still surfaces controllers that emit again -
        required for HA "add another" / "try discovery again".

        Opens a temporary multicast lease when multicast is not already up
        (e.g. unicast-only runtime). Enriches each result with a command-plane
        label probe after the listen window.
        """
        h = self._host
        window_start = time.time()
        started_here = False
        if not h.is_session_running():
            await h.start()
            started_here = True

        temp_mcast: Lease | None = None
        if not h.event_receiver.is_transport_open(Transport.MULTICAST):
            temp_mcast = await h.event_receiver.acquire(Transport.MULTICAST)
        found: list[DiscoveredController] = []
        try:
            await asyncio.sleep(timeout)
            found = h.identities.heard_since(window_start)
        finally:
            if temp_mcast is not None:
                await temp_mcast.release()
            if started_here:
                await h.stop()

        return list(await asyncio.gather(*(self.enrich_discovered(d) for d in found)))
