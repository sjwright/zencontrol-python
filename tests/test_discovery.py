"""Unit tests for multicast controller discovery (identity only)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from helpers_endpoints import fake_endpoint_factory
from zencontrol import ZenControl, ZenController
from zencontrol.interface import EntityContext
from zencontrol.api.commands import ZenCommandClient
from zencontrol.api.event_router import ZenEventReceiver
from zencontrol.api.models import mac_key
from zencontrol.api.models import DiscoveredController
from zencontrol.exceptions import ZenTimeoutError
from zencontrol.io.event import ZenEvent


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
    body = (
        bytes([0x5A, 0x43])
        + mac
        + target.to_bytes(2, "big")
        + bytes([code, len(payload)])
        + payload
    )
    return body + bytes([_xor(body)])


def _event(
    *,
    ip: str = "192.168.1.50",
    mac: bytes = b"\x02\x00\x00\x00\x00\x01",
    code: int = 1,
    received_at: float = 0.0,
) -> ZenEvent:
    return ZenEvent(
        mac=mac,
        target=0,
        code=code,
        payload=b"\x00",
        host=ip,
        received_at=received_at,
    )


def _receiver_with_discovered_callback(
    callback: AsyncMock | None = None,
) -> tuple[ZenCommandClient, ZenEventReceiver]:
    commands = ZenCommandClient()
    receiver = ZenEventReceiver()
    if callback is not None:
        receiver.identities.on_discovered = callback
    return commands, receiver


@pytest.mark.asyncio
async def test_unknown_multicast_records_identity_without_query() -> None:
    seen: list[DiscoveredController] = []

    async def on_discovered(discovered: DiscoveredController) -> None:
        seen.append(discovered)

    commands, receiver = _receiver_with_discovered_callback(on_discovered)

    with patch.object(
        commands, "query_controller_label", new_callable=AsyncMock, return_value="Kitchen"
    ) as query:
        await receiver.handle(_event())

    query.assert_not_awaited()
    assert len(receiver.identities.discovered) == 1
    discovered = receiver.identities.discovered[0]
    assert discovered.host == "192.168.1.50"
    assert discovered.mac == "02:00:00:00:00:01"
    assert discovered.label is None
    assert discovered.port == 5108
    assert seen == [discovered]


@pytest.mark.asyncio
async def test_second_packet_from_same_mac_refreshes_last_seen() -> None:
    commands, receiver = _receiver_with_discovered_callback()
    with patch.object(
        commands, "query_controller_label", new_callable=AsyncMock, return_value="Kitchen"
    ) as query:
        await receiver.handle(_event(received_at=1.0))
        first = receiver.identities.discovered[0]
        await receiver.handle(_event(ip="192.168.1.99", received_at=10.0))
        await receiver.handle(
            _event(ip="192.168.1.50", mac=b"\xaa\xbb\xcc\xdd\xee\xff", received_at=2.0)
        )

    query.assert_not_awaited()
    # One entry per MAC (same MAC from different IP still one; new MAC is second)
    assert len(receiver.identities.discovered) == 2
    refreshed = next(d for d in receiver.identities.discovered if d.mac == first.mac)
    assert refreshed.host == "192.168.1.99"
    assert refreshed.last_seen == 10.0
    assert refreshed.first_seen == first.first_seen
    assert receiver.identities.heard_since(10.0) == [refreshed]
    assert len(receiver.identities.heard_since(0.0)) == 2


@pytest.mark.asyncio
async def test_close_clears_discovered() -> None:
    _, receiver = _receiver_with_discovered_callback()
    await receiver.handle(_event())
    assert len(receiver.identities.discovered) == 1
    await receiver.close()
    assert receiver.identities.discovered == []


@pytest.mark.asyncio
async def test_heard_since_matches_discover_window_semantics() -> None:
    """Second window returns a controller only if it emits again (HA retry)."""
    _, receiver = _receiver_with_discovered_callback()
    await receiver.handle(_event(received_at=1.0))
    assert receiver.identities.heard_since(5.0) == []

    await receiver.handle(_event(ip="192.168.1.50", received_at=6.0))
    heard = receiver.identities.heard_since(5.0)
    assert len(heard) == 1
    assert heard[0].last_seen == 6.0
    assert heard[0].first_seen == 1.0


def test_mac_key_normalises_separators() -> None:
    assert mac_key("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert mac_key("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert mac_key(bytes.fromhex("aabbccddeeff")) == "AA:BB:CC:DD:EE:FF"


@pytest.mark.asyncio
async def test_discover_started_here_returns_results_after_teardown() -> None:
    """HA initial flow: discover starts+stops the stack; must snapshot before close()."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()

    async def inject_during_window() -> None:
        # Wait until discover has started the consumer, then emit.
        for _ in range(50):
            if zen.event_receiver.consumer_task is not None:
                break
            await asyncio.sleep(0.01)
        zen.event_receiver.inject(_frame(), ("192.168.1.50", 6969))
        for _ in range(20):
            if zen.event_receiver.identities.discovered:
                return
            await asyncio.sleep(0)

    with patch.object(
        zen.commands,
        "query_controller_label",
        new_callable=AsyncMock,
        return_value="Kitchen",
    ):
        with patch.object(zen.commands, "_invalidate_client", new_callable=AsyncMock):
            inject_task = asyncio.create_task(inject_during_window())
            found = await zen.discover(timeout=0.3)
            await inject_task

    assert len(found) == 1
    assert found[0].mac == "02:00:00:00:00:01"
    assert found[0].label == "Kitchen"
    # Teardown cleared the receiver cache — return value must not depend on it.
    assert zen.discovered_controllers == []


@pytest.mark.asyncio
async def test_registered_controller_is_not_discovered() -> None:
    commands, receiver = _receiver_with_discovered_callback()
    ctrl = ZenController(
        id=1,
        name="known",
        label="Known",
        host="192.168.1.50",
        port=5108,
        mac="02:00:00:00:00:01",
        ctx=EntityContext(commands=commands),
    )

    async def handler(_ev: object) -> None:
        pass

    receiver.subscribe(handler, host=ctrl.ip, mac=ctrl.mac_bytes)

    with patch.object(
        commands, "query_controller_label", new_callable=AsyncMock, return_value="Nope"
    ) as query:
        await receiver.handle(_event())

    query.assert_not_awaited()
    assert receiver.identities.discovered == []


@pytest.mark.asyncio
async def test_registering_controller_forgets_identified() -> None:
    commands, receiver = _receiver_with_discovered_callback()
    await receiver.handle(_event())
    assert len(receiver.identities.discovered) == 1

    ctrl = ZenController(
        id=1,
        name="kitchen",
        label="Kitchen",
        host="192.168.1.50",
        port=5108,
        mac="02:00:00:00:00:01",
        ctx=EntityContext(commands=commands),
    )
    receiver.identities.forget(host=ctrl.host, mac=ctrl.mac)
    assert receiver.identities.discovered == []


@pytest.mark.asyncio
async def test_discovery_never_awaits_label_query() -> None:
    commands, receiver = _receiver_with_discovered_callback()
    with patch.object(
        commands,
        "query_controller_label",
        new_callable=AsyncMock,
        side_effect=TimeoutError("offline"),
    ) as query:
        await receiver.handle(_event())

    query.assert_not_awaited()
    assert len(receiver.identities.discovered) == 1
    assert receiver.identities.discovered[0].label is None


@pytest.mark.asyncio
async def test_new_controller_discovered_while_one_is_registered() -> None:
    commands, receiver = _receiver_with_discovered_callback()
    known = ZenController(
        id=1,
        name="known",
        label="Known",
        host="192.168.1.10",
        port=5108,
        mac="11:22:33:44:55:66",
        ctx=EntityContext(commands=commands),
    )
    async def _ignore(_ev: object) -> None:
        pass

    receiver.subscribe(_ignore, host=known.ip, mac=known.mac_bytes)

    with patch.object(
        commands, "query_controller_label", new_callable=AsyncMock, return_value="Annex"
    ) as query:
        await receiver.handle(_event())

    query.assert_not_awaited()
    assert len(receiver.identities.discovered) == 1
    assert receiver.identities.discovered[0].label is None
    assert receiver.identities.discovered[0].mac == "02:00:00:00:00:01"


@pytest.mark.asyncio
async def test_enrich_discovered_queries_label_via_temp_name() -> None:
    zen = ZenControl()
    discovered = DiscoveredController(
        host="192.168.1.50",
        mac="02:00:00:00:00:01",
        label=None,
        port=5108,
        first_seen=1.0,
    )
    zen.identities._entries["02:00:00:00:00:01"] = discovered

    with patch.object(
        zen.commands,
        "query_controller_label",
        new_callable=AsyncMock,
        return_value="Kitchen",
    ) as query:
        with patch.object(
            zen.commands, "_invalidate_client", new_callable=AsyncMock
        ) as invalidate:
            enriched = await zen.enrich_discovered(discovered)

    query.assert_awaited_once()
    temp = query.await_args.args[0]
    assert temp.name == "_discover_020000000001"
    assert temp.host == "192.168.1.50"
    assert temp.mac == "02:00:00:00:00:01"
    invalidate.assert_awaited_once_with(temp)
    assert enriched.label == "Kitchen"
    assert zen.discovered_controllers[0].label == "Kitchen"
    assert zen.controllers == []


@pytest.mark.asyncio
async def test_enrich_discovered_keeps_identity_on_timeout() -> None:
    zen = ZenControl()
    discovered = DiscoveredController(
        host="192.168.1.50",
        mac="02:00:00:00:00:01",
        label=None,
    )
    zen.identities._entries["02:00:00:00:00:01"] = discovered

    with patch.object(
        zen.commands,
        "query_controller_label",
        new_callable=AsyncMock,
        side_effect=ZenTimeoutError("offline"),
    ):
        with patch.object(zen.commands, "_invalidate_client", new_callable=AsyncMock):
            enriched = await zen.enrich_discovered(discovered)

    assert enriched.label is None
    assert zen.discovered_controllers[0].label is None


@pytest.mark.asyncio
async def test_provisional_subscription_learns_mac() -> None:
    commands, receiver = _receiver_with_discovered_callback()
    ctrl = ZenController(
        id=1,
        name="pending",
        label="Pending",
        host="192.168.1.50",
        port=5108,
        mac=None,
        ctx=EntityContext(commands=commands),
    )
    assert ctrl.mac is None

    delivered: list[object] = []

    async def on_event(ev: object) -> None:
        delivered.append(ev)

    async def on_identified(mac: bytes) -> None:
        ctrl.mac = ":".join(f"{b:02X}" for b in mac)

    receiver.subscribe(on_event, host=ctrl.ip, on_identified=on_identified)
    await receiver.handle(_event(code=0x00, mac=b"\x02\x00\x00\x00\x00\x01"))

    assert ctrl.mac == "02:00:00:00:00:01"
    assert len(delivered) == 1
    assert receiver.identities.discovered == []
