"""Phase 4–5: ZenEventWiring attach/detach, re-arm, and MAC promotion persistence."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from helpers_endpoints import fake_endpoint_factory, require_event

from zencontrol.api.event_router import EventHealth, ZenEventReceiver
from zencontrol.api.types import Transport, ZenEventMode
from zencontrol.interface.interface import ZenControl
from zencontrol.interface.wiring import ZenEventWiring


def _controller(name: str = "ctrl-a", host: str = "127.0.0.1") -> MagicMock:
    ctrl = MagicMock()
    ctrl.name = name
    ctrl.ip = host
    ctrl.host = host
    ctrl.filtering = False
    ctrl.mac = None
    ctrl.mac_bytes = None
    return ctrl


@pytest.mark.asyncio
async def test_wiring_attach_subscribes_leases_and_programs_emit() -> None:
    receiver = ZenEventReceiver()
    receiver._endpoint_factory = fake_endpoint_factory()
    commands = MagicMock()
    commands.set_tpi_event_unicast_address = AsyncMock()
    commands.tpi_event_emit = AsyncMock(return_value=True)
    handler = AsyncMock()

    wiring = ZenEventWiring(receiver, commands, event_handler=handler)
    ctrl = _controller()
    mode = ZenEventMode(enabled=True, transport=Transport.MULTICAST)

    binding = await wiring.attach(ctrl, mode)

    assert binding.controller is ctrl
    assert wiring.get(ctrl) is binding
    assert receiver.lease_count(Transport.MULTICAST) == 1
    commands.tpi_event_emit.assert_awaited_once()
    emit_mode = commands.tpi_event_emit.await_args.args[1]
    assert emit_mode.enabled is True
    assert emit_mode.transport is Transport.MULTICAST

    await binding.detach()
    assert wiring.get(ctrl) is None
    assert receiver.lease_count(Transport.MULTICAST) == 0
    disable_mode = commands.tpi_event_emit.await_args.args[1]
    assert disable_mode.enabled is False


@pytest.mark.asyncio
async def test_program_unicast_without_advertise_raises() -> None:
    """UNICAST with no advertise must fail — not clear the address and emit blindly."""
    receiver = ZenEventReceiver(unicast_listen_ip="127.0.0.1", unicast_port=0)
    receiver._endpoint_factory = fake_endpoint_factory()
    commands = MagicMock()
    commands.set_tpi_event_unicast_address = AsyncMock()
    commands.tpi_event_emit = AsyncMock(return_value=True)
    wiring = ZenEventWiring(receiver, commands, event_handler=AsyncMock())

    lease = await receiver.acquire(Transport.UNICAST, toward="127.0.0.1")
    await receiver._close_endpoint(Transport.UNICAST)
    assert lease.advertise is None

    with pytest.raises(RuntimeError, match="no advertise address"):
        await wiring._program(
            _controller(),
            lease,
            ZenEventMode(enabled=True, transport=Transport.UNICAST),
        )

    commands.set_tpi_event_unicast_address.assert_not_awaited()
    commands.tpi_event_emit.assert_not_awaited()
    await lease.release()


@pytest.mark.asyncio
async def test_wiring_attach_rollback_on_emit_failure() -> None:
    receiver = ZenEventReceiver()
    receiver._endpoint_factory = fake_endpoint_factory()
    commands = MagicMock()
    commands.set_tpi_event_unicast_address = AsyncMock()
    commands.tpi_event_emit = AsyncMock(side_effect=RuntimeError("emit failed"))
    wiring = ZenEventWiring(receiver, commands, event_handler=AsyncMock())

    with pytest.raises(RuntimeError, match="emit failed"):
        await wiring.attach(_controller(), ZenEventMode(enabled=True))

    assert wiring.bindings == {}
    assert receiver.lease_count(Transport.MULTICAST) == 0


@pytest.mark.asyncio
async def test_attach_once_survives_forced_endpoint_death() -> None:
    """Bindings are not re-created; emit is re-armed after receiver recovery."""
    zen = ZenControl()
    zen.reconnect_min_delay = 0.01
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    zen.add_controller(id=1, name="ctrl-a", label="A", host="127.0.0.1", mac="02:00:00:00:00:01")
    on_resync = AsyncMock()
    await zen.start()
    assert zen.session.wiring is not None
    zen.session.wiring.on_resync = on_resync

    binding = zen.session.wiring.get("ctrl-a")
    assert binding is not None
    first_binding = binding
    emit_before = zen.commands.tpi_event_emit.await_count

    dead = zen.event_receiver.consumer_task
    assert dead is not None
    dead.cancel()
    try:
        await asyncio.wait_for(dead, timeout=1.0)
    except asyncio.CancelledError:
        pass

    # Wait for recovery without re-attaching
    for _ in range(100):
        live = zen.event_receiver.consumer_task
        if live is not None and not live.done() and live is not dead:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("receiver did not restore consumer after endpoint death")

    assert zen.session.wiring.get("ctrl-a") is first_binding
    assert zen.commands.tpi_event_emit.await_count > emit_before
    on_resync.assert_awaited()
    assert zen.is_event_monitoring_active()

    await zen.stop()


def _xor(buf: bytes) -> int:
    acc = 0
    for b in buf:
        acc ^= b
    return acc & 0xFF


def _frame(
    *,
    mac: bytes = b"\x02\x00\x00\x00\x00\x01",
    host: str = "127.0.0.1",
) -> tuple[bytes, tuple[str, int]]:
    payload = b"\x01"
    body = bytes([0x5A, 0x43]) + mac + (64).to_bytes(2, "big") + bytes([0x00, len(payload)]) + payload
    return body + bytes([_xor(body)]), (host, 1)


@pytest.mark.asyncio
async def test_host_only_binding_learns_mac_and_persists() -> None:
    """Attach without MAC; first packet promotes and fires persistence callback."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    ctrl = zen.add_controller(id=1, name="pending", label="Pending", host="127.0.0.1", mac=None)
    assert ctrl.mac is None
    assert ctrl.mac_bytes is None

    persisted: list[tuple[str, str]] = []

    async def on_identified(controller, mac: str) -> None:
        persisted.append((controller.name, mac))

    zen.callbacks.controller_identified = on_identified
    await zen.start()

    binding = zen.session.wiring.get("pending") if zen.session.wiring else None
    assert binding is not None
    assert binding.mac is None
    assert binding.event_health is EventHealth.IDENTIFYING

    data, addr = _frame(mac=b"\x02\x00\x00\x00\x00\xaa", host="127.0.0.1")
    zen.event_receiver.inject(require_event(data, addr))
    await asyncio.sleep(0.05)

    assert ctrl.mac == "02:00:00:00:00:AA"
    assert ctrl.mac_bytes == b"\x02\x00\x00\x00\x00\xaa"
    assert binding.mac == ctrl.mac_bytes
    assert binding.event_health is EventHealth.RECEIVING
    assert persisted == [("pending", "02:00:00:00:00:AA")]

    await zen.stop()


@pytest.mark.asyncio
async def test_promotion_conflict_detaches_zombie_binding() -> None:
    """MAC conflict must drop the binding/lease, not leave a silent zombie."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    status_changes: list[tuple[str, str]] = []

    async def on_status(controller, status: str) -> None:
        status_changes.append((controller.name, status))

    zen.callbacks.controller_status_change = on_status
    known = zen.add_controller(
        id=1,
        name="known",
        label="Known",
        host="127.0.0.1",
        mac="02:00:00:00:00:0A",
    )
    pending = zen.add_controller(
        id=2,
        name="pending",
        label="Pending",
        host="127.0.0.2",
        mac=None,
    )
    await zen.start()

    binding = zen.session.wiring.get("pending") if zen.session.wiring else None
    assert binding is not None
    assert binding.event_health is EventHealth.IDENTIFYING
    assert zen.event_receiver.lease_count(Transport.MULTICAST) >= 1

    ok = await zen.event_receiver._promote(binding.subscription, known.mac_bytes)
    assert ok is False
    await asyncio.sleep(0.05)

    assert zen.session.wiring.get("pending") is None
    assert zen.event_health_for("pending") is None
    assert ("pending", "unreachable") in status_changes
    # Keepalive must not keep confirming emit for a controller with no route.
    assert await zen.assert_controller_events(pending) is False

    await zen.stop()


@pytest.mark.asyncio
async def test_known_mac_skips_identified_callback() -> None:
    """When MAC is already configured, promotion callback is not fired."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    zen.add_controller(
        id=1,
        name="known",
        label="Known",
        host="127.0.0.1",
        mac="02:00:00:00:00:01",
    )
    on_identified = AsyncMock()
    zen.callbacks.controller_identified = on_identified
    await zen.start()

    assert zen.event_health_for("known") is EventHealth.SILENT

    data, addr = _frame(mac=b"\x02\x00\x00\x00\x00\x01", host="127.0.0.1")
    zen.event_receiver.inject(require_event(data, addr))
    await asyncio.sleep(0.05)

    on_identified.assert_not_awaited()
    assert zen.event_health_for("known") is EventHealth.RECEIVING
    await zen.stop()
