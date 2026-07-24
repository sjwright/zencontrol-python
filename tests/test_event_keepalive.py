"""Unit tests for TPI event emit keepalive / re-assert."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from zencontrol.api.types import ZenEventMode
from zencontrol.interface.interface import ZenControl


class _BlockingListener:
    def __init__(self) -> None:
        self.listen_port = 6969

    async def events(self):
        try:
            while True:
                await asyncio.sleep(3600)
                yield  # pragma: no cover
        except asyncio.CancelledError:
            raise

    async def close(self):
        return None


def _controller(name: str = "ctrl") -> SimpleNamespace:
    return SimpleNamespace(name=name, filtering=False, mac="AA:BB:CC:DD:EE:01")


@pytest.mark.asyncio
async def test_assert_reconfigures_when_emit_disabled() -> None:
    zen = ZenControl()
    zen.protocol.event_listener = object()  # pretend listener is up
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)
    zen.controllers = [ctrl]  # type: ignore[list-item]

    zen.protocol.query_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.protocol.query_tpi_event_emit_state = AsyncMock(return_value=False)
    zen.configure_controller_events = AsyncMock(return_value=True)

    assert await zen.assert_controller_events(ctrl) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_awaited_once_with(ctrl)


@pytest.mark.asyncio
async def test_assert_marks_unreachable_when_reassert_fails() -> None:
    zen = ZenControl()
    zen.protocol.event_listener = object()
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)
    zen.protocol.query_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.protocol.query_tpi_event_emit_state = AsyncMock(return_value=False)
    zen.configure_controller_events = AsyncMock(return_value=False)
    status_cb = AsyncMock()
    zen.controller_status_change = status_cb

    assert await zen.assert_controller_events(ctrl) is False  # type: ignore[arg-type]
    status_cb.assert_awaited_once_with(ctrl, "unreachable")


@pytest.mark.asyncio
async def test_assert_defers_while_controller_not_ready() -> None:
    zen = ZenControl()
    zen.protocol.event_listener = object()
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=False)
    zen.configure_controller_events = AsyncMock()
    zen.protocol.query_tpi_event_unicast_address = AsyncMock()
    status_cb = AsyncMock()
    zen.controller_status_change = status_cb

    assert await zen.assert_controller_events(ctrl) is True  # type: ignore[arg-type]
    zen.configure_controller_events.assert_not_awaited()
    zen.protocol.query_tpi_event_unicast_address.assert_not_awaited()
    status_cb.assert_awaited_once_with(ctrl, "starting")


@pytest.mark.asyncio
async def test_assert_noop_when_emit_enabled() -> None:
    zen = ZenControl()
    zen.protocol.event_listener = object()
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)

    zen.protocol.query_tpi_event_unicast_address = AsyncMock(
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
    zen = ZenControl(unicast=True)
    zen.protocol.event_listener = object()
    zen.protocol.local_ip = "192.168.1.10"
    zen.protocol.listen_port = 6970
    ctrl = _controller()
    ctrl.is_controller_ready = AsyncMock(return_value=True)

    zen.protocol.query_tpi_event_unicast_address = AsyncMock(
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
async def test_assert_returns_false_when_ping_fails() -> None:
    zen = ZenControl()
    zen.protocol.event_listener = object()
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
    zen.protocol.query_tpi_event_unicast_address = AsyncMock(return_value=None)
    zen.protocol.query_tpi_event_emit_state = AsyncMock(return_value=False)
    zen.protocol.query_controller_startup_complete = AsyncMock(return_value=True)
    # start_event_monitoring configures controllers itself; stub the UDP path.
    zen.protocol.set_tpi_event_unicast_address = AsyncMock()
    zen.protocol.tpi_event_emit = AsyncMock(return_value=True)

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(return_value=_BlockingListener()),
    ):
        await zen.start()
        for _ in range(40):
            if configure.await_count >= 1:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("keepalive did not re-assert events")

        await zen.stop()
        assert zen._keepalive_task is None or zen._keepalive_task.done()
