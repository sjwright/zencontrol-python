"""Unit tests for MAC routing, provisional promotion, and identity-only discovery."""

from __future__ import annotations

import time

import pytest

from zencontrol.api.event_decode import ButtonPress, LevelChangeV2
from zencontrol.api.event_router import EventHealth, ZenEventReceiver
from zencontrol.io.event import ZenEvent

MAC_A = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x0A])
MAC_B = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x0B])


def _event(
    *,
    mac: bytes = MAC_A,
    host: str = "192.168.1.50",
    code: int = 0x00,
    target: int = 64,
    payload: bytes = b"\x01",
    received_at: float | None = None,
) -> ZenEvent:
    return ZenEvent(
        mac=mac,
        target=target,
        code=code,
        payload=payload,
        host=host,
        received_at=time.time() if received_at is None else received_at,
    )


@pytest.mark.asyncio
async def test_mac_subscription_delivers_decoded_event() -> None:
    receiver = ZenEventReceiver()
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    receiver.subscribe(handler, mac=MAC_A)
    await receiver.handle(_event())
    assert seen == [ButtonPress(target=64, instance=1)]


@pytest.mark.asyncio
async def test_subscription_handler_exception_does_not_break_later_delivery() -> None:
    receiver = ZenEventReceiver()
    seen: list[object] = []
    attempts = 0

    async def handler(ev: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("application callback failed")
        seen.append(ev)

    receiver.subscribe(handler, mac=MAC_A)
    await receiver.handle(_event(payload=b"\x01"))
    await receiver.handle(_event(payload=b"\x02"))

    assert attempts == 2
    assert seen == [ButtonPress(target=64, instance=2)]


@pytest.mark.asyncio
async def test_provisional_promotes_on_first_packet() -> None:
    receiver = ZenEventReceiver()
    identified: list[bytes] = []
    seen: list[object] = []

    async def on_identified(mac: bytes) -> None:
        identified.append(mac)

    async def handler(ev: object) -> None:
        seen.append(ev)

    sub = receiver.subscribe(handler, host="192.168.1.50", on_identified=on_identified)
    assert sub.mac is None
    assert sub.event_health is EventHealth.IDENTIFYING

    await receiver.handle(_event(mac=MAC_A, host="192.168.1.50"))
    assert sub.mac == MAC_A
    assert sub.event_health is EventHealth.RECEIVING
    assert identified == [MAC_A]
    assert seen == [ButtonPress(target=64, instance=1)]

    # Subsequent packets route by MAC even if IP changes
    await receiver.handle(_event(mac=MAC_A, host="192.168.1.99", payload=b"\x02"))
    assert seen[-1] == ButtonPress(target=64, instance=2)


@pytest.mark.asyncio
async def test_known_mac_starts_silent_until_packet() -> None:
    """Configured-MAC subscriptions are silent before the first routed packet."""
    receiver = ZenEventReceiver(event_silent_after=60.0)
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    sub = receiver.subscribe(handler, mac=MAC_A)
    assert sub.event_health is EventHealth.SILENT
    assert sub.last_seen is None

    await receiver.handle(_event(mac=MAC_A))
    assert seen == [ButtonPress(target=64, instance=1)]
    assert sub.event_health is EventHealth.RECEIVING
    assert sub.last_seen is not None


@pytest.mark.asyncio
async def test_receiving_demotes_to_silent_when_last_seen_stale() -> None:
    receiver = ZenEventReceiver(event_silent_after=0.05)
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    sub = receiver.subscribe(handler, mac=MAC_A)
    await receiver.handle(_event(mac=MAC_A))
    assert sub.event_health is EventHealth.RECEIVING

    sub._last_seen = time.time() - 1.0
    assert sub.event_health is EventHealth.SILENT


@pytest.mark.asyncio
async def test_mac_wins_over_provisional_ip() -> None:
    receiver = ZenEventReceiver()
    mac_hits: list[str] = []
    prov_hits: list[str] = []

    async def mac_handler(ev: object) -> None:
        mac_hits.append("mac")

    async def prov_handler(ev: object) -> None:
        prov_hits.append("prov")

    receiver.subscribe(mac_handler, mac=MAC_A)
    receiver.subscribe(prov_handler, host="192.168.1.50")

    await receiver.handle(_event(mac=MAC_A, host="192.168.1.50"))
    assert mac_hits == ["mac"]
    assert prov_hits == []


@pytest.mark.asyncio
async def test_second_provisional_same_host_rejected() -> None:
    receiver = ZenEventReceiver()

    async def handler(ev: object) -> None:
        pass

    receiver.subscribe(handler, host="192.168.1.50")
    with pytest.raises(ValueError, match="provisional"):
        receiver.subscribe(handler, host="192.168.1.50")


def test_subscription_identity_fields_are_read_only() -> None:
    receiver = ZenEventReceiver()

    async def handler(_ev: object) -> None:
        pass

    sub = receiver.subscribe(handler, host="192.168.1.50")
    with pytest.raises(AttributeError):
        sub.mac = MAC_A  # type: ignore[misc]
    with pytest.raises(AttributeError):
        sub.host = "10.0.0.1"  # type: ignore[misc]
    assert sub.host == "192.168.1.50"
    assert MAC_A not in receiver._by_mac


@pytest.mark.asyncio
async def test_duplicate_mac_rejected() -> None:
    receiver = ZenEventReceiver()

    async def handler(ev: object) -> None:
        pass

    receiver.subscribe(handler, mac=MAC_A)
    with pytest.raises(ValueError, match="MAC already"):
        receiver.subscribe(handler, mac=MAC_A)


@pytest.mark.asyncio
async def test_promotion_conflict_fails_provisional() -> None:
    receiver = ZenEventReceiver()
    seen: list[str] = []
    lost: list[str] = []

    async def mac_handler(ev: object) -> None:
        seen.append("mac")

    async def prov_handler(ev: object) -> None:
        seen.append("prov")

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    receiver.subscribe(mac_handler, mac=MAC_A)
    # Different host so both can exist; promote will conflict if MAC_A arrives
    # on the provisional's host - but MAC lookup wins first, so use a race via
    # direct _promote after resolving to provisional with a colliding MAC.
    sub = receiver.subscribe(prov_handler, host="192.168.1.60", on_lost=on_lost)
    ok = await receiver._promote(sub, MAC_A)
    assert ok is False
    assert sub.event_health is EventHealth.DETACHED
    assert sub._closed
    assert lost == ["mac_conflict"]


@pytest.mark.asyncio
async def test_discovery_identity_only_no_label() -> None:
    receiver = ZenEventReceiver()
    discovered = []

    async def on_discovered(d) -> None:
        discovered.append(d)

    receiver.identities.on_discovered = on_discovered
    await receiver.handle(_event(mac=MAC_B, host="192.168.1.70", received_at=1.0))
    assert len(discovered) == 1
    assert discovered[0].host == "192.168.1.70"
    assert discovered[0].mac == "02:00:00:00:00:0B"
    assert discovered[0].label is None
    assert discovered[0].first_seen == 1.0
    assert receiver.identities.discovered == discovered

    # Second packet for same MAC is ignored
    await receiver.handle(_event(mac=MAC_B, host="192.168.1.70", payload=b"\x02", received_at=1.0))
    assert len(discovered) == 1


@pytest.mark.asyncio
async def test_subscribed_controller_is_not_discovered() -> None:
    receiver = ZenEventReceiver()
    discovered = []

    async def on_discovered(d) -> None:
        discovered.append(d)

    async def handler(ev: object) -> None:
        pass

    receiver.identities.on_discovered = on_discovered
    receiver.subscribe(handler, mac=MAC_A)
    await receiver.handle(_event(mac=MAC_A))
    assert discovered == []


@pytest.mark.asyncio
async def test_close_unregisters_subscription() -> None:
    receiver = ZenEventReceiver()
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    sub = receiver.subscribe(handler, mac=MAC_A)
    sub.close()
    await receiver.handle(_event())
    assert seen == []
    assert len(receiver.identities.discovered) == 1


@pytest.mark.asyncio
async def test_unknown_code_does_not_call_handler() -> None:
    receiver = ZenEventReceiver()
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    receiver.subscribe(handler, mac=MAC_A)
    await receiver.handle(_event(code=0xFF, payload=b"\x00"))
    assert seen == []


@pytest.mark.asyncio
async def test_level_change_v2_routed() -> None:
    receiver = ZenEventReceiver()
    seen: list[object] = []

    async def handler(ev: object) -> None:
        seen.append(ev)

    receiver.subscribe(handler, mac=MAC_A)
    await receiver.handle(_event(code=0x0B, target=3, payload=b"\xfe\x10"))
    assert seen == [LevelChangeV2(target=3, current=0xFE, level=0x10)]


def test_subscribe_requires_mac_or_host() -> None:
    receiver = ZenEventReceiver()

    async def handler(ev: object) -> None:
        pass

    with pytest.raises(ValueError, match="mac= or host="):
        receiver.subscribe(handler)


def test_subscribe_host_must_be_ipv4() -> None:
    receiver = ZenEventReceiver()

    async def handler(ev: object) -> None:
        pass

    with pytest.raises(ValueError, match="wire IPv4"):
        receiver.subscribe(handler, host="ctrl.local")
    with pytest.raises(ValueError, match="wire IPv4"):
        receiver.subscribe(handler, mac=MAC_A, host="not-an-ip")


def test_event_router_imports_no_command_plane() -> None:
    """I1: receiver never imports the command plane."""
    import zencontrol.api.event_router as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "api.commands" not in source
    assert "io.command" not in source
    assert "ZenCommandClient" not in source
    assert "query_" not in source
