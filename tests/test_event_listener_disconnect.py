"""Unit tests for event-listener disconnect, start rollback, and teardown."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from helpers_endpoints import fake_endpoint_factory
from zencontrol.api.types import Transport, ZenEventMode
from zencontrol.interface.interface import ZenControl


@pytest.mark.asyncio
async def test_consumer_crash_fires_on_disconnect_once() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0
    on_disconnect = AsyncMock()
    zen.on_disconnect = on_disconnect
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    task = zen.event_receiver.consumer_task
    assert task is not None

    # Crash the consumer with a real exception (not CancelledError)
    async def boom(_event) -> None:
        raise RuntimeError("socket died")

    zen.event_receiver.handle = boom  # type: ignore[method-assign]

    def _xor(buf: bytes) -> int:
        acc = 0
        for b in buf:
            acc ^= b
        return acc & 0xFF

    body = bytes([0x5A, 0x43]) + b"\x02\x00\x00\x00\x00\x01" + b"\x00\x40\x00\x01\x01"
    zen.event_receiver.inject(body + bytes([_xor(body)]), ("127.0.0.1", 1))
    await asyncio.wait_for(task, timeout=1.0)

    # Recoverable gap: no disconnect — session restores and re-arms (I10).
    on_disconnect.assert_not_awaited()
    for _ in range(40):
        live = zen.event_receiver.consumer_task
        if live is not None and not live.done() and live is not task:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("session was not restored after consumer crash")
    assert zen.is_event_monitoring_active()


@pytest.mark.asyncio
async def test_intentional_stop_fires_on_disconnect_once() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0
    on_disconnect = AsyncMock()
    zen.on_disconnect = on_disconnect
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    await zen.stop()

    on_disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_after_crash_does_not_double_notify() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0
    on_disconnect = AsyncMock()
    zen.on_disconnect = on_disconnect
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    task = zen.event_receiver.consumer_task
    assert task is not None
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.05)
    await zen.stop()

    on_disconnect.assert_awaited_once()


def _mock_controller(name: str) -> MagicMock:
    ctrl = MagicMock()
    ctrl.name = name
    ctrl.filtering = False
    ctrl.ip = "127.0.0.1"
    ctrl.mac_bytes = None
    ctrl.host = "127.0.0.1"
    ctrl.port = 5108
    ctrl.mac = None
    ctrl.id = "1"
    return ctrl


@pytest.mark.asyncio
async def test_partial_attach_leaves_no_binding_when_emit_fails() -> None:
    """If emit programming fails, wiring does not retain a binding for that controller."""
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock(return_value=None)

    async def emit(controller: MagicMock, mode: ZenEventMode | None = None) -> bool:
        assert mode is not None
        if controller.name == "ctrl-b" and mode.enabled:
            raise RuntimeError("config failed")
        return True

    zen.commands.tpi_event_emit = AsyncMock(side_effect=emit)
    ctrl_a = _mock_controller("ctrl-a")
    ctrl_b = _mock_controller("ctrl-b")
    ctrl_b.ip = "127.0.0.2"
    ctrl_b.host = "127.0.0.2"
    zen.controllers = [ctrl_a, ctrl_b]

    # Attach ctrl-a succeeds; ctrl-b fails and rolls back its subscription/lease.
    from zencontrol.interface.wiring import ZenEventWiring

    wiring = ZenEventWiring(
        zen.event_receiver,
        zen.commands,
        event_handler=AsyncMock(),
        logger=zen.logger,
    )
    mode = ZenEventMode(enabled=True, filtering=False, transport=Transport.MULTICAST)
    await wiring.attach(ctrl_a, mode)
    with pytest.raises(RuntimeError, match="config failed"):
        await wiring.attach(ctrl_b, mode)

    assert wiring.get("ctrl-a") is not None
    assert wiring.get("ctrl-b") is None
    await wiring.detach_all()
    assert zen.event_receiver.lease_count(Transport.MULTICAST) == 0


@pytest.mark.asyncio
async def test_stop_event_monitoring_is_idempotent() -> None:
    zen = ZenControl()
    zen.reconnect_min_delay = 10.0
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    await zen.start()
    assert zen.is_event_monitoring_active()

    await zen.stop()
    assert not zen.is_event_monitoring_active()

    await zen.stop()
    assert not zen.is_event_monitoring_active()
