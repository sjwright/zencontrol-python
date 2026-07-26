"""
Controller-agnostic event receiver: subscriptions, leases, funnel, routing.

Never imports the command plane. Never awaits a device query on the consumer path
(I8). Subscription handlers are awaited inline by the funnel consumer — they must
not await command-plane / device I/O. Do that work on a task (see ZenControl's
per-controller event dispatch); a slow handler stalls every other controller and
lets the shared funnel drop packets.

Discovery identity bookkeeping lives on ``IdentityLog`` (``receiver.identities``);
the receiver only appends on the no-subscription branch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from ..io.event import EventConst, ZenEndpoint, ZenEvent, parse_frame
from ..utils import is_ipv4_address, local_ip_for_remote
from .event_decode import ZenDecodedEvent, decode
from .identity import IdentityLog
from .models import mac_bytes_to_str
from .types import Const, Transport

# Handlers run on the funnel consumer. Contract: do not await device/command I/O.
SubscriptionHandler = Callable[[ZenDecodedEvent], Awaitable[None]]
IdentifiedHandler = Callable[[bytes], Awaitable[None]]
LostHandler = Callable[[str], Awaitable[None]]
UnexpectedExitHandler = Callable[[], Awaitable[None]]
# Sync: must not await (may run while a transport lock is held).
LeasesIdleHandler = Callable[[], None]

# Why the receiver dropped a subscription (passed to LostHandler).
LOST_MAC_CONFLICT = "mac_conflict"


class EventHealth(Enum):
    IDENTIFYING = "identifying"
    RECEIVING = "receiving"
    SILENT = "silent"
    DETACHED = "detached"


@dataclass
class Subscription:
    """Route handle for one controller's events.

    Lifecycle: identifying → receiving → silent → detached.
    Known-MAC subscriptions start silent (attached, no packet yet). Provisional
    ones stay identifying until promotion. ``event_health`` is computed from the
    stored state plus ``last_seen`` so RECEIVING demotes to SILENT when stale.

    Identity fields are read-only to callers. Only the receiver mutates ``_mac``
    (via ``_promote``) together with ``_by_mac`` — assigning ``mac`` from outside
    would desynchronise the routing table.
    """

    _receiver: ZenEventReceiver
    _handler: SubscriptionHandler
    _mac: bytes | None = None
    _host: str | None = None
    _last_seen: float | None = None
    _on_identified: IdentifiedHandler | None = None
    _on_lost: LostHandler | None = None
    _closed: bool = field(default=False, repr=False)
    _health: EventHealth = field(default=EventHealth.IDENTIFYING, repr=False)

    @property
    def mac(self) -> bytes | None:
        return self._mac

    @property
    def host(self) -> str | None:
        return self._host

    @property
    def last_seen(self) -> float | None:
        return self._last_seen

    @property
    def event_health(self) -> EventHealth:
        health = self._health
        if health is EventHealth.DETACHED or health is EventHealth.IDENTIFYING:
            return health
        if self._last_seen is None:
            return EventHealth.SILENT
        silent_after = self._receiver.event_silent_after
        if silent_after > 0 and (time.time() - self._last_seen) > silent_after:
            return EventHealth.SILENT
        return EventHealth.RECEIVING

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._health = EventHealth.DETACHED
        self._receiver._forget(self)


@dataclass
class Lease:
    """Reference-counted hold on a transport endpoint.

    ``toward`` is the remote used to pick a local source IP on multi-homed
    hosts. ``advertise`` is derived live from the receiver's open unicast
    endpoint plus that toward — never a stale snapshot.

    ``transport`` / ``toward`` are read-only; release is the only mutator.
    """

    _receiver: ZenEventReceiver
    _transport: Transport
    _toward: str | None = None
    _released: bool = field(default=False, repr=False)

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def toward(self) -> str | None:
        return self._toward

    @property
    def advertise(self) -> tuple[str, int] | None:
        """Local (ip, port) for unicast programming.

        Derives from the open endpoint (live port) plus a route lookup toward
        ``toward``. The IP lookup opens a UDP socket; results are memoised on
        the receiver per ``(toward, bound_port)`` — still assign to a local if
        you need the value more than once in one function.
        """
        if self._transport is not Transport.UNICAST:
            return None
        return self._receiver.unicast_advertise(toward=self._toward)

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._receiver._release(self._transport)


class ZenEventReceiver:
    """MAC router + leased endpoints feeding one shared funnel."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        max_queue_size: int = EventConst.DEFAULT_MAX_QUEUE_SIZE,
        unicast_listen_ip: str = "0.0.0.0",
        unicast_port: int = 0,
        event_silent_after: float = Const.EVENT_SILENT_AFTER,
        identities: IdentityLog | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.identities = identities or IdentityLog(logger=self.logger)
        self.on_unexpected_exit: UnexpectedExitHandler | None = None
        self.on_session_restored: UnexpectedExitHandler | None = None
        self.on_leases_idle: LeasesIdleHandler | None = None
        self.max_queue_size = max(1, max_queue_size)
        self.event_silent_after = max(0.0, event_silent_after)
        self.unicast_listen_ip = unicast_listen_ip
        self.unicast_port = unicast_port

        self._by_mac: dict[bytes, Subscription] = {}
        self._by_host: dict[str, Subscription] = {}

        # Funnel: raw datagrams from every open endpoint
        self._funnel: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue(maxsize=self.max_queue_size)
        self.dropped_datagrams = 0
        self._last_drop_log = 0.0
        self._consumer_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._recover_task: asyncio.Task[None] | None = None

        # Leased endpoints
        self._refcounts: dict[Transport, int] = {
            Transport.MULTICAST: 0,
            Transport.UNICAST: 0,
        }
        self._endpoints: dict[Transport, ZenEndpoint] = {}
        self._locks: dict[Transport, asyncio.Lock] = {
            Transport.MULTICAST: asyncio.Lock(),
            Transport.UNICAST: asyncio.Lock(),
        }
        # Optional factory override for tests (in-memory endpoints)
        self._endpoint_factory: Callable[..., Awaitable[ZenEndpoint]] | None = None
        # (toward, bound_port) → (ip, port); avoids a UDP connect per advertise read
        self._advertise_cache: dict[tuple[str | None, int], tuple[str, int]] = {}

    @property
    def consumer_task(self) -> asyncio.Task[None] | None:
        return self._consumer_task

    def _stop_requested(self) -> bool:
        """Read stop flag without mypy narrowing across concurrent awaits."""
        return self._stopping

    def is_transport_open(self, transport: Transport) -> bool:
        ep = self._endpoints.get(transport)
        return ep is not None and ep.is_open()

    def unicast_advertise(self, *, toward: str | None = None) -> tuple[str, int] | None:
        """Local (ip, port) to program on a controller for unicast event delivery.

        ``toward`` selects which local address to advertise on a multi-homed host
        (route toward that remote). Port always comes from the open unicast
        endpoint so re-open never leaves a stale snapshot on the lease.
        Route lookups are memoised per ``(toward, bound_port)``.
        """
        ep = self._endpoints.get(Transport.UNICAST)
        if ep is None or not ep.is_open():
            return None
        port = ep.bound_port
        key = (toward, port)
        cached = self._advertise_cache.get(key)
        if cached is not None:
            return cached
        if toward:
            ip = local_ip_for_remote(toward)
        elif self.unicast_listen_ip and self.unicast_listen_ip != "0.0.0.0":
            ip = self.unicast_listen_ip
        else:
            ip = local_ip_for_remote("8.8.8.8")
        result = (ip, port)
        self._advertise_cache[key] = result
        return result

    def leased_transports_open(self) -> bool:
        """True when every transport with a positive refcount has an open endpoint."""
        any_lease = False
        for transport, count in self._refcounts.items():
            if count <= 0:
                continue
            any_lease = True
            if not self.is_transport_open(transport):
                return False
        return any_lease

    def lease_count(self, transport: Transport) -> int:
        return self._refcounts[transport]

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(
        self,
        handler: SubscriptionHandler,
        *,
        mac: bytes | None = None,
        host: str | None = None,
        on_identified: IdentifiedHandler | None = None,
        on_lost: LostHandler | None = None,
    ) -> Subscription:
        """Register a route. ``host`` must be the wire peer IPv4 (no DNS here).

        Provisional subscriptions key on ``event.host`` from UDP, which is
        always an IP. Resolve hostnames with ``await resolve_host()`` before
        calling — never ``socket.gethostbyname`` on the event loop (HA).
        """
        if mac is None and host is None:
            raise ValueError("subscribe() requires mac= or host=")
        if mac is not None and len(mac) != 6:
            raise ValueError(f"mac must be 6 bytes, got {len(mac)}")
        if host is not None and not is_ipv4_address(host):
            raise ValueError(f"subscribe(host=) must be a wire IPv4 address, got {host!r}; await resolve_host() first")

        if mac is not None and mac in self._by_mac:
            raise ValueError(f"MAC already subscribed: {mac_bytes_to_str(mac)}")
        if mac is None and host is not None and host in self._by_host:
            raise ValueError(f"Host already has a provisional subscription: {host}")

        sub = Subscription(
            _receiver=self,
            _handler=handler,
            _mac=mac,
            _host=host,
            # Known MAC: attached but unheard. Provisional: wait for identity.
            _health=(EventHealth.SILENT if mac is not None else EventHealth.IDENTIFYING),
            _on_identified=on_identified,
            _on_lost=on_lost,
        )
        if mac is not None:
            self._by_mac[mac] = sub
        elif host is not None:
            self._by_host[host] = sub
        return sub

    def _forget(self, sub: Subscription) -> None:
        if sub.mac is not None and self._by_mac.get(sub.mac) is sub:
            del self._by_mac[sub.mac]
        if sub.host is not None and self._by_host.get(sub.host) is sub:
            del self._by_host[sub.host]

    # ------------------------------------------------------------------
    # Leases
    # ------------------------------------------------------------------

    async def acquire(self, transport: Transport, *, toward: str | None = None) -> Lease:
        """Hold a transport open. First acquire binds; last release closes.

        For unicast, ``toward`` is stored on the lease so ``advertise`` can
        resolve a per-controller local IP on multi-homed hosts.
        """
        async with self._locks[transport]:
            count = self._refcounts[transport]
            if count == 0:
                # Open before incrementing — failure leaves refcount at 0.
                await self._open_endpoint(transport)
            self._refcounts[transport] = count + 1
            self._ensure_consumer()
            return Lease(
                _receiver=self,
                _transport=transport,
                _toward=toward if transport is Transport.UNICAST else None,
            )

    async def _release(self, transport: Transport) -> None:
        became_idle = False
        async with self._locks[transport]:
            count = self._refcounts[transport]
            if count <= 0:
                return
            count -= 1
            self._refcounts[transport] = count
            if count == 0:
                await self._close_endpoint(transport)
            if self._refcounts[Transport.MULTICAST] == 0 and self._refcounts[Transport.UNICAST] == 0:
                await self._stop_consumer(intentional=True)
                became_idle = True
        # Outside the lock: wake waiters (Event.set is sync / non-reentrant-safe).
        if became_idle and callable(self.on_leases_idle):
            self.on_leases_idle()

    async def _open_endpoint(self, transport: Transport) -> None:
        unicast = transport is Transport.UNICAST
        listen_ip = self.unicast_listen_ip if unicast else "0.0.0.0"
        listen_port = self.unicast_port if unicast else EventConst.MULTICAST_PORT

        factory = self._endpoint_factory or ZenEndpoint.open
        endpoint = await factory(
            unicast=unicast,
            listen_ip=listen_ip,
            listen_port=listen_port,
            sink=self._enqueue_datagram,
            logger=self.logger,
        )
        self._endpoints[transport] = endpoint
        if unicast and self.unicast_port == 0:
            # Remember the assigned port for stability across re-open
            self.unicast_port = endpoint.bound_port

    async def _close_endpoint(self, transport: Transport) -> None:
        ep = self._endpoints.pop(transport, None)
        if ep is not None:
            await ep.close()
        if transport is Transport.UNICAST:
            self._advertise_cache.clear()

    # ------------------------------------------------------------------
    # Funnel
    # ------------------------------------------------------------------

    def _enqueue_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self._funnel.put_nowait((data, addr))
            return
        except asyncio.QueueFull:
            pass
        try:
            self._funnel.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self._funnel.put_nowait((data, addr))
        except asyncio.QueueFull:
            pass
        self.dropped_datagrams += 1
        now = time.time()
        if now - self._last_drop_log >= EventConst.DROP_LOG_INTERVAL:
            self._last_drop_log = now
            self.logger.warning(
                "Event funnel full (max=%d); dropped %d datagram(s) total",
                self.max_queue_size,
                self.dropped_datagrams,
            )

    def inject(self, data: bytes, addr: tuple[str, int]) -> None:
        """Test helper: push a datagram into the funnel without a socket."""
        self._enqueue_datagram(data, addr)

    def _ensure_consumer(self) -> None:
        if self._consumer_task is not None and not self._consumer_task.done():
            return
        self._stopping = False
        self._consumer_task = asyncio.create_task(self._consume())

    async def _stop_consumer(self, *, intentional: bool) -> None:
        task = self._consumer_task
        self._consumer_task = None
        if task is None:
            return
        self._stopping = intentional
        # Don't await ourselves when called from the consumer's finally block
        if task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _consume(self) -> None:
        unexpected = True
        try:
            while True:
                data, addr = await self._funnel.get()
                try:
                    event = parse_frame(data, addr)
                    if event is None:
                        self.logger.debug(
                            "Invalid event packet from %s: %s",
                            addr,
                            ", ".join(f"0x{b:02x}" for b in data),
                        )
                        continue
                    await self.handle(event)
                finally:
                    self._funnel.task_done()
        except asyncio.CancelledError:
            # Intentional stop sets _stopping before cancelling us.
            unexpected = not self._stopping
            if not unexpected:
                raise
        except Exception as err:
            self.logger.error(f"Event funnel consumer error: {err}", exc_info=True)
        finally:
            if unexpected:
                if callable(self.on_unexpected_exit):
                    try:
                        await self.on_unexpected_exit()
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:
                        self.logger.error(f"on_unexpected_exit error: {err}", exc_info=True)
                # Leases and subscriptions survive; re-open transports (I10).
                if any(self._refcounts.values()):
                    self._schedule_recover()

    def _schedule_recover(self) -> None:
        task = self._recover_task
        if task is not None and not task.done():
            return
        self._recover_task = asyncio.create_task(self._recover_session())

    async def _recover_session(self) -> None:
        """Re-open leased transports and restart the consumer. Subscriptions untouched.

        Only starts a consumer when every refcounted transport is open. A partial
        bind failure (NIC flap, interface change) must not leave a zombie
        consumer on an unfed queue — retry with backoff instead.

        Endpoints are closed once before the retry loop. Retries only open
        transports that are still down (``is_transport_open``), so a working
        multicast socket is not flapped every backoff while unicast stays dead.
        """
        await asyncio.sleep(0)  # let the dying consumer finish
        if not any(self._refcounts.values()):
            return

        for transport in list(self._endpoints):
            async with self._locks[transport]:
                await self._close_endpoint(transport)
        # Re-read via method: close() may set _stopping during the awaits above.
        if self._stop_requested():
            return

        delay = 0.5
        max_delay = 30.0
        while True:
            if self._stop_requested():
                return
            if not any(self._refcounts.values()):
                return

            for transport, count in list(self._refcounts.items()):
                if count <= 0:
                    continue
                try:
                    async with self._locks[transport]:
                        if self.is_transport_open(transport):
                            continue
                        await self._open_endpoint(transport)
                except Exception as err:
                    self.logger.error(
                        "Failed to restore %s endpoint: %s",
                        transport.value,
                        err,
                        exc_info=True,
                    )

            if self._stop_requested():
                return
            if not any(self._refcounts.values()):
                return

            if self.leased_transports_open():
                self._ensure_consumer()
                if not callable(self.on_session_restored):
                    return
                try:
                    await self.on_session_restored()
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self.logger.error(f"on_session_restored error: {err}", exc_info=True)
                return

            self.logger.warning(
                "Session restore incomplete (leased transports not open); retrying in %.1fs",
                delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, max_delay)

    async def _teardown_endpoints(self) -> None:
        """Zero refcounts and close sockets without touching the consumer task."""
        for transport in (Transport.MULTICAST, Transport.UNICAST):
            async with self._locks[transport]:
                self._refcounts[transport] = 0
                await self._close_endpoint(transport)

    async def close(self) -> None:
        """Release all leases and close every endpoint. Idempotent."""
        self._stopping = True
        recover = self._recover_task
        self._recover_task = None
        if recover is not None and not recover.done():
            recover.cancel()
            try:
                await recover
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self._teardown_endpoints()
        await self._stop_consumer(intentional=True)
        self.identities.clear()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _resolve(self, event: ZenEvent) -> Subscription | None:
        sub = self._by_mac.get(event.mac)
        if sub is not None:
            return sub
        return self._by_host.get(event.host)

    async def handle(self, event: ZenEvent) -> None:
        sub = self._resolve(event)
        if sub is None:
            if event.mac not in self._by_mac:
                await self.identities.record(event)
            return

        if sub._mac is None:
            promoted = await self._promote(sub, event.mac)
            if not promoted:
                if event.mac not in self._by_mac:
                    await self.identities.record(event)
                return

        sub._last_seen = event.received_at
        if sub._health is not EventHealth.DETACHED:
            sub._health = EventHealth.RECEIVING

        decoded = decode(event)
        if decoded is None:
            self.logger.warning(
                "Unrecognised or malformed event code %s from %s",
                event.code,
                event.host,
            )
            return

        # Awaited on the funnel consumer — handler must not await the wire (I8).
        try:
            await sub._handler(decoded)
        except Exception as err:
            self.logger.error(
                "Subscription handler failed for %s: %s",
                mac_bytes_to_str(sub._mac) if sub._mac else sub._host,
                err,
                exc_info=True,
            )

    async def _promote(self, sub: Subscription, mac: bytes) -> bool:
        if sub._closed or sub._mac is not None:
            return sub._mac is not None

        existing = self._by_mac.get(mac)
        if existing is not None and existing is not sub:
            self.logger.warning(
                "Provisional subscription for %s failed: MAC %s already routed",
                sub._host,
                mac_bytes_to_str(mac),
            )
            await self._drop_subscription(sub, LOST_MAC_CONFLICT)
            return False

        if sub._host is not None and self._by_host.get(sub._host) is sub:
            del self._by_host[sub._host]

        sub._mac = mac
        self._by_mac[mac] = sub
        sub._health = EventHealth.RECEIVING
        self.identities.forget(mac=mac)

        if callable(sub._on_identified):
            try:
                await sub._on_identified(mac)
            except Exception as err:
                self.logger.error(
                    "on_identified error for %s: %s",
                    mac_bytes_to_str(mac),
                    err,
                    exc_info=True,
                )
        return True

    async def _drop_subscription(self, sub: Subscription, reason: str) -> None:
        """Close a subscription the receiver can no longer route and notify the owner."""
        sub.close()
        if not callable(sub._on_lost):
            return
        try:
            await sub._on_lost(reason)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.logger.error(
                "on_lost error for %s (%s): %s",
                mac_bytes_to_str(sub._mac) if sub._mac else sub._host,
                reason,
                err,
                exc_info=True,
            )
