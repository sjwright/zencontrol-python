"""Unit tests for Cluster B lifecycle (aclose, task tracking, instance caches)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zencontrol.api.models import ZenAddress
from zencontrol.api.types import ZenAddressType
from zencontrol.interface.interface import (
    ZenControl,
    ZenLight,
)


@pytest.mark.asyncio
async def test_zencontrol_async_context_manager_calls_aclose() -> None:
    with patch.object(ZenControl, "aclose", new_callable=AsyncMock) as aclose:
        async with ZenControl() as zen:
            assert isinstance(zen, ZenControl)
        aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_controller_closes_client_and_purges_cache() -> None:
    zen = ZenControl()
    ctrl_a = zen.add_controller(
        id=1, name="ctrl-a", label="A", host="127.0.0.1", port=5108
    )
    ctrl_b = zen.add_controller(
        id=2, name="ctrl-b", label="B", host="127.0.0.2", port=5108
    )
    fake_client = MagicMock()
    fake_client.close = AsyncMock()
    ctrl_a.client = fake_client
    address = ZenAddress(controller=ctrl_a, type=ZenAddressType.ECG, number=1)
    ZenLight(protocol=zen.protocol, address=address)
    assert "ctrl-a 1" in zen.protocol.entity_registry.lights

    await zen.remove_controller(ctrl_a)

    fake_client.close.assert_awaited()
    assert ctrl_a.client is None
    assert zen.controllers == [ctrl_b]
    assert "ctrl-a" not in zen.protocol.entity_registry.controllers
    assert "ctrl-a 1" not in zen.protocol.entity_registry.lights
    assert "ctrl-b" in zen.protocol.entity_registry.controllers

    await zen.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_clients_and_clears_instances() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(
        id=1,
        name="ctrl-a",
        label="Ctrl A",
        host="127.0.0.1",
        port=5108,
    )
    fake_client = MagicMock()
    fake_client.is_connected.return_value = True
    fake_client.close = AsyncMock()
    ctrl.client = fake_client

    address = ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=1)
    light = ZenLight(protocol=zen.protocol, address=address)
    assert "ctrl-a 1" in zen.protocol.entity_registry.lights
    assert "ctrl-a" in zen.protocol.entity_registry.controllers

    await zen.aclose()

    fake_client.close.assert_awaited()
    assert ctrl.client is None
    assert zen.protocol.entity_registry.lights == {}
    assert zen.protocol.entity_registry.controllers == {}


@pytest.mark.asyncio
async def test_aclose_cancels_tracked_background_tasks() -> None:
    zen = ZenControl()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_running() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = zen.protocol.track_task(long_running())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert task in zen.protocol._bg_tasks

    await zen.aclose()

    assert task.cancelled() or task.done()
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert zen.protocol._bg_tasks == set()


@pytest.mark.asyncio
async def test_stop_does_not_clear_entity_caches() -> None:
    zen = ZenControl()
    ctrl = zen.add_controller(
        id=1,
        name="ctrl-b",
        label="Ctrl B",
        host="127.0.0.1",
        port=5108,
    )
    address = ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=2)
    ZenLight(protocol=zen.protocol, address=address)

    with patch(
        "zencontrol.api.protocol.ZenListener.create",
        new=AsyncMock(),
    ):
        # stop without ever starting should be a no-op for disconnect
        await zen.stop()

    assert "ctrl-b 2" in zen.protocol.entity_registry.lights
    assert "ctrl-b" in zen.protocol.entity_registry.controllers
    await zen.aclose()


def test_clear_entity_caches_clears_protocol_registry() -> None:
    zen = ZenControl()
    zen.protocol.entity_registry.controllers["x"] = MagicMock()
    zen.protocol.entity_registry.lights["x"] = MagicMock()
    zen.protocol.entity_registry.groups["x"] = MagicMock()

    zen.clear_entity_caches()

    assert zen.protocol.entity_registry.controllers == {}
    assert zen.protocol.entity_registry.lights == {}
    assert zen.protocol.entity_registry.groups == {}
