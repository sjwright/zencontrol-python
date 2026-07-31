"""Phase 3: leased endpoints, shared funnel, dual transport isolation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zencontrol.api.event_decode import ButtonPress
from zencontrol.api.event_router import ZenEventReceiver
from zencontrol.api.types import Transport, ZenEventMode
from zencontrol.io.event import EventConst, accept_datagram, parse_frame


def _xor(buf: bytes) -> int:
    acc = 0
    for b in buf:
        acc ^= b
    return acc & 0xFF


def _frame(
    *,
    mac: bytes = b"\x02\x00\x00\x00\x00\x01",
    target: int = 64,
    code: int = 0x00,
    payload: bytes = b"\x01",
) -> bytes:
    body = bytes([0x5A, 0x43]) + mac + target.to_bytes(2, "big") + bytes([code, len(payload)]) + payload
    return body + bytes([_xor(body)])


def _fake_endpoint(*, unicast: bool, listen_port: int = 0):
    async def factory(**kwargs):
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.unicast = kwargs.get("unicast", unicast)
        port = kwargs.get("listen_port") or listen_port or (0 if unicast else EventConst.MULTICAST_PORT)
        if unicast and port == 0:
            port = 41234
        ep.bound_port = port
        ep.listen_port = port
        ep.close = AsyncMock()
        # Stash sink so tests can push via accept_datagram (production handoff)
        ep.sink = kwargs["sink"]
        factory.last = ep  # type: ignore[attr-defined]
        return ep

    return factory


@pytest.mark.asyncio
async def test_lease_refcount_opens_once_closes_on_last_release() -> None:
    receiver = ZenEventReceiver()
    factory = _fake_endpoint(unicast=False)
    receiver._endpoint_factory = factory

    a = await receiver.acquire(Transport.MULTICAST)
    b = await receiver.acquire(Transport.MULTICAST)
    assert receiver.lease_count(Transport.MULTICAST) == 2
    assert receiver.is_transport_open(Transport.MULTICAST)
    assert factory.last.close.await_count == 0

    await a.release()
    assert receiver.lease_count(Transport.MULTICAST) == 1
    assert factory.last.close.await_count == 0

    await b.release()
    assert receiver.lease_count(Transport.MULTICAST) == 0
    factory.last.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_unicast_only_never_opens_multicast() -> None:
    receiver = ZenEventReceiver(unicast_listen_ip="127.0.0.1", unicast_port=0)
    opened: list[bool] = []

    async def factory(**kwargs):
        opened.append(bool(kwargs["unicast"]))
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.bound_port = 5555
        ep.close = AsyncMock()
        return ep

    receiver._endpoint_factory = factory
    lease = await receiver.acquire(Transport.UNICAST, toward="127.0.0.1")
    assert opened == [True]
    assert not receiver.is_transport_open(Transport.MULTICAST)
    assert lease.toward == "127.0.0.1"
    assert lease.advertise is not None
    assert lease.advertise[1] == 5555
    await lease.release()
    # Advertise is live — gone once the endpoint closes.
    assert lease.advertise is None


@pytest.mark.asyncio
async def test_advertise_memoises_route_lookup() -> None:
    """Repeated advertise reads must not open a UDP socket each time."""
    receiver = ZenEventReceiver(unicast_port=0)
    receiver._endpoint_factory = _fake_endpoint(unicast=True, listen_port=41234)
    lease = await receiver.acquire(Transport.UNICAST, toward="10.0.0.1")

    with patch(
        "zencontrol.api.event_router.local_ip_for_remote",
        return_value="10.0.0.50",
    ) as route:
        a = lease.advertise
        b = lease.advertise
        assert a == ("10.0.0.50", 41234)
        assert b == a
        route.assert_called_once_with("10.0.0.1")

    await lease.release()
    assert lease.advertise is None
    assert receiver._advertise_cache == {}


@pytest.mark.asyncio
async def test_multicast_only_never_opens_unicast() -> None:
    receiver = ZenEventReceiver()
    opened: list[bool] = []

    async def factory(**kwargs):
        opened.append(bool(kwargs["unicast"]))
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.bound_port = EventConst.MULTICAST_PORT
        ep.close = AsyncMock()
        return ep

    receiver._endpoint_factory = factory
    lease = await receiver.acquire(Transport.MULTICAST)
    assert opened == [False]
    assert not receiver.is_transport_open(Transport.UNICAST)
    await lease.release()


@pytest.mark.asyncio
async def test_unicast_bind_failure_leaves_multicast_healthy() -> None:
    receiver = ZenEventReceiver()

    async def factory(**kwargs):
        if kwargs.get("unicast"):
            raise OSError("address already in use")
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.bound_port = EventConst.MULTICAST_PORT
        ep.close = AsyncMock()
        return ep

    receiver._endpoint_factory = factory
    mlease = await receiver.acquire(Transport.MULTICAST)
    assert receiver.is_transport_open(Transport.MULTICAST)

    with pytest.raises(OSError, match="address already in use"):
        await receiver.acquire(Transport.UNICAST, toward="127.0.0.1")

    assert receiver.is_transport_open(Transport.MULTICAST)
    assert receiver.lease_count(Transport.UNICAST) == 0
    await mlease.release()


@pytest.mark.asyncio
async def test_both_transports_feed_one_funnel() -> None:
    receiver = ZenEventReceiver(unicast_listen_ip="127.0.0.1")
    sinks: list = []

    async def factory(**kwargs):
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.bound_port = 6000 if kwargs.get("unicast") else EventConst.MULTICAST_PORT
        ep.close = AsyncMock()
        sinks.append(kwargs["sink"])
        return ep

    receiver._endpoint_factory = factory
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    receiver.subscribe(handler, mac=b"\x02\x00\x00\x00\x00\x01")
    mlease = await receiver.acquire(Transport.MULTICAST)
    ulease = await receiver.acquire(Transport.UNICAST, toward="127.0.0.1")
    assert len(sinks) == 2

    # Push via each transport's sink — same path as ZenEventProtocol
    assert accept_datagram(_frame(), ("192.168.1.1", 1), sinks[0])
    assert accept_datagram(_frame(payload=b"\x02"), ("192.168.1.2", 1), sinks[1])

    for _ in range(50):
        if len(seen) >= 2:
            break
        await __import__("asyncio").sleep(0.01)
    else:
        pytest.fail(f"expected 2 events, got {seen!r}")

    assert seen == [
        ButtonPress(target=64, instance=1),
        ButtonPress(target=64, instance=2),
    ]
    await mlease.release()
    await ulease.release()


@pytest.mark.asyncio
async def test_inject_takes_validated_events_only() -> None:
    receiver = ZenEventReceiver()
    receiver._endpoint_factory = _fake_endpoint(unicast=False)
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    receiver.subscribe(handler, mac=b"\x02\x00\x00\x00\x00\x01")
    lease = await receiver.acquire(Transport.MULTICAST)
    event = parse_frame(_frame(), ("10.0.0.1", 6969))
    assert event is not None
    receiver.inject(event)
    # Framing rejection stays in io — malformed never becomes a ZenEvent
    assert parse_frame(b"\x00\x01\x02", ("10.0.0.1", 6969)) is None

    for _ in range(50):
        if seen:
            break
        await __import__("asyncio").sleep(0.01)
    assert seen == [ButtonPress(target=64, instance=1)]
    await lease.release()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ZenEventMode(enabled=True, transport=Transport.MULTICAST), 0x01),
        (ZenEventMode(enabled=True, transport=Transport.UNICAST), 0x01 | 0x40 | 0x80),
        (ZenEventMode(enabled=True, filtering=True, transport=Transport.MULTICAST), 0x01 | 0x02),
        (ZenEventMode(enabled=False, transport=Transport.UNICAST), 0x40 | 0x80),
    ],
)
def test_event_mode_bitmask_matrix(mode: ZenEventMode, expected: int) -> None:
    assert mode.bitmask() == expected


def test_event_mode_transport_properties() -> None:
    multi = ZenEventMode(enabled=True, transport=Transport.MULTICAST)
    assert multi.multicast is True and multi.unicast is False
    uni = ZenEventMode(enabled=True, transport=Transport.UNICAST)
    assert uni.unicast is True and uni.multicast is False


def test_event_mode_from_byte_roundtrip() -> None:
    for transport in (Transport.MULTICAST, Transport.UNICAST):
        mode = ZenEventMode(enabled=True, filtering=True, transport=transport)
        restored = ZenEventMode.from_byte(mode.bitmask())
        assert restored.enabled is True
        assert restored.filtering is True
        assert restored.transport is transport
