"""Unit tests for TPI event emit keepalive / re-assert."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from helpers_endpoints import fake_endpoint_factory
from zencontrol.api.types import ZenEventMode
from zencontrol.interface.interface import ZenControl


def _controller(name: str = "ctrl") -> SimpleNamespace:
    return SimpleNamespace(name=name, filtering=False, mac="AA:BB:CC:DD:EE:01")


@pytest.mark.asyncio
async def test_assert_reconfigures_when_emit_disabled() -> None:
    zen = ZenControl()
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)
    zen.controllers = [ctrl]  # type: ignore[list-item]

    zen.commands.query_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.commands.query_tpi_event_emit_state = AsyncMock(return_value=False)
    zen.configure_controller_events = AsyncMock(return_value=True)

    assert await zen.assert_controller_events(ctrl) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_awaited_once_with(ctrl)


@pytest.mark.asyncio
async def test_assert_marks_unreachable_when_reassert_fails() -> None:
    zen = ZenControl()
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)
    zen.commands.query_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.commands.query_tpi_event_emit_state = AsyncMock(return_value=False)
    zen.configure_controller_events = AsyncMock(return_value=False)
    status_cb = AsyncMock()
    zen.controller_status_change = status_cb

    assert await zen.assert_controller_events(ctrl) is False  # type: ignore[arg-type]
    status_cb.assert_awaited_once_with(ctrl, "unreachable")


@pytest.mark.asyncio
async def test_assert_defers_while_controller_not_ready() -> None:
    zen = ZenControl()
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=False)
    zen.configure_controller_events = AsyncMock()
    zen.commands.query_tpi_event_unicast_address = AsyncMock()
    status_cb = AsyncMock()
    zen.controller_status_change = status_cb

    assert await zen.assert_controller_events(ctrl) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_not_awaited()
    zen.commands.query_tpi_event_unicast_address.assert_not_awaited()
    status_cb.assert_awaited_once_with(ctrl, "starting")


@pytest.mark.asyncio
async def test_assert_noop_when_emit_enabled() -> None:
    zen = ZenControl()
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)

    zen.commands.query_tpi_event_unicast_address = AsyncMock(
        return_value={
            "mode": ZenEventMode(enabled=True, unicast=False, multicast=True),
            "port": 0,
            "ip": "0.0.0.0",
        }
    )
    zen.configure_controller_events = AsyncMock()

    assert await zen.assert_controller_events(ctrl) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_assert_reconfigures_on_unicast_target_mismatch() -> None:
    from unittest.mock import MagicMock

    zen = ZenControl(unicast=True)
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)

    lease = SimpleNamespace(advertise=("192.168.1.10", 6970))
    wiring = MagicMock()
    wiring.get.return_value = SimpleNamespace(lease=lease)
    zen._wiring = wiring

    zen.commands.query_tpi_event_unicast_address = AsyncMock(
        return_value={
            "mode": ZenEventMode(enabled=True, unicast=True, multicast=False),
            "port": 6970,
            "ip": "192.168.1.99",  # stale target after HA IP change / reboot
        }
    )
    zen.configure_controller_events = AsyncMock(return_value=True)

    assert await zen.assert_controller_events(ctrl) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_awaited_once_with(ctrl)


@pytest.mark.asyncio
async def test_assert_compares_per_binding_advertise() -> None:
    """Multi-homed: each controller's expected unicast target is its own lease."""
    from unittest.mock import MagicMock

    zen = ZenControl(unicast=True)
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]

    ctrl_a = _controller("ctrl-a")
    ctrl_a.is_controller_ready = AsyncMock(return_value=True)
    ctrl_b = _controller("ctrl-b")
    ctrl_b.is_controller_ready = AsyncMock(return_value=True)

    lease_a = SimpleNamespace(advertise=("10.0.0.1", 6970))
    lease_b = SimpleNamespace(advertise=("192.168.1.10", 6970))
    binding_a = SimpleNamespace(lease=lease_a)
    binding_b = SimpleNamespace(lease=lease_b)
    wiring = MagicMock()
    wiring.get.side_effect = lambda c: {
        "ctrl-a": binding_a,
        "ctrl-b": binding_b,
    }.get(c if isinstance(c, str) else c.name)
    zen._wiring = wiring

    zen.configure_controller_events = AsyncMock(return_value=True)

    async def query(controller):
        if controller.name == "ctrl-a":
            return {
                "mode": ZenEventMode(enabled=True, unicast=True, multicast=False),
                "port": 6970,
                "ip": "10.0.0.1",
            }
        return {
            "mode": ZenEventMode(enabled=True, unicast=True, multicast=False),
            "port": 6970,
            "ip": "192.168.1.10",
        }

    zen.commands.query_tpi_event_unicast_address = AsyncMock(side_effect=query)

    assert await zen.assert_controller_events(ctrl_a) is True  # type: ignore[arg-type]
    assert await zen.assert_controller_events(ctrl_b) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_not_awaited()

    # ctrl-b programmed with ctrl-a's address must re-assert.
    zen.commands.query_tpi_event_unicast_address = AsyncMock(
        return_value={
            "mode": ZenEventMode(enabled=True, unicast=True, multicast=False),
            "port": 6970,
            "ip": "10.0.0.1",
        }
    )
    assert await zen.assert_controller_events(ctrl_b) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_awaited_once_with(ctrl_b)


@pytest.mark.asyncio
async def test_assert_returns_false_when_ping_fails() -> None:
    zen = ZenControl()
    zen.is_event_monitoring_active = lambda: True  # type: ignore[method-assign]
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=None)
    zen.configure_controller_events = AsyncMock()

    assert await zen.assert_controller_events(ctrl) is False  # type: ignore[arg-type]
    zen.configure_controller_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_loop_reasserts_and_stops_cleanly() -> None:
    zen = ZenControl()
    zen.event_keepalive_interval = 0.05
    zen.reconnect_min_delay = 0.01
    ctrl = _controller()
    zen.add_controller(
        id=1,
        name=ctrl.name,
        label="Controller",
        host="127.0.0.1",
        mac=ctrl.mac,
    )

    configure = AsyncMock()
    zen.configure_controller_events = configure
    zen.commands.query_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.commands.query_tpi_event_emit_state = AsyncMock(return_value=False)
    zen.commands.query_controller_startup_complete = AsyncMock(return_value=True)
    # start_event_monitoring configures controllers itself; stub the UDP path.
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    await zen.start()
    for _ in range(40):
        if configure.await_count >= 1:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("keepalive did not re-assert events")

    await zen.stop()
    assert zen._keepalive_task is None or zen._keepalive_task.done()
