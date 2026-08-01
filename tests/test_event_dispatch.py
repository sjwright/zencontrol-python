"""I8: entity/callback work must not run inline on the funnel consumer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from helpers_endpoints import fake_endpoint_factory, require_event

from zencontrol.api.event_decode import (
    GroupLevelChange,
    GroupOccupied,
    LevelChange,
    LevelChangeV2,
)
from zencontrol.api.types import ZenAddressType
from zencontrol.interface.interface import ZenControl


def _xor(buf: bytes) -> int:
    acc = 0
    for b in buf:
        acc ^= b
    return acc & 0xFF


def _level_frame(*, mac: bytes, level: int, host: str = "127.0.0.1") -> tuple[bytes, tuple[str, int]]:
    # LEVEL_CHANGE_V2: code 0x0B, payload [current, target]
    payload = bytes([level, level])
    body = (
        bytes([0x5A, 0x43])
        + mac
        + (0).to_bytes(2, "big")  # ECG 0
        + bytes([0x0B, len(payload)])
        + payload
    )
    return body + bytes([_xor(body)]), (host, 1)


@pytest.mark.asyncio
async def test_subscription_handler_returns_before_callback_runs() -> None:
    """Consumer path schedules dispatch; application callbacks run afterward."""
    zen = ZenControl()
    zen.event_receiver._endpoint_factory = fake_endpoint_factory()
    zen.commands.set_tpi_event_unicast_address = AsyncMock()
    zen.commands.tpi_event_emit = AsyncMock(return_value=True)

    ctrl = zen.add_controller(
        id=1,
        name="house",
        label="House",
        host="127.0.0.1",
        mac="02:00:00:00:00:01",
    )
    # Ensure a light singleton exists for address 0.
    from zencontrol import ZenAddress, ZenAddressType

    light = zen.ctx.light(
        ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=0),
    )
    light.features = {"brightness": True}

    release_callback = asyncio.Event()
    callback_started = asyncio.Event()
    order: list[str] = []

    async def on_light_change(**kwargs) -> None:
        order.append("callback")
        callback_started.set()
        await release_callback.wait()

    zen.callbacks.light_change = on_light_change
    await zen.start()

    data, addr = _level_frame(mac=b"\x02\x00\x00\x00\x00\x01", level=128)
    # Drive the consumer the same way a validated endpoint event would.
    zen.event_receiver.inject(require_event(data, addr))

    # Handler/dispatch was scheduled; callback may not have started yet, but
    # the funnel consumer must not be blocked waiting on it.
    for _ in range(50):
        if callback_started.is_set():
            break
        await asyncio.sleep(0.01)
    assert callback_started.is_set()
    assert "callback" in order

    # While the callback is still awaiting, the consumer task must still be live.
    consumer = zen.event_receiver.consumer_task
    assert consumer is not None and not consumer.done()

    release_callback.set()
    await asyncio.sleep(0.05)
    assert light.level == 128

    await zen.stop()


@pytest.mark.asyncio
async def test_dispatch_chain_ignores_predecessor_cancel_but_honours_own() -> None:
    """Awaiting a cancelled predecessor must not swallow this task's CancelledError."""
    zen = ZenControl()
    ctrl = zen.add_controller(
        id=1,
        name="house",
        label="House",
        host="127.0.0.1",
        mac="02:00:00:00:00:01",
    )
    ev = LevelChangeV2(target=0, current=1, level=2)
    dispatched: list[int] = []

    async def record_dispatch(_ctrl, _ev) -> None:
        dispatched.append(1)

    with patch.object(zen._dispatcher, "dispatch", side_effect=record_dispatch):
        # Predecessor stuck until cancelled.
        stuck = asyncio.get_running_loop().create_future()

        async def predecessor() -> None:
            await stuck

        prev = asyncio.create_task(predecessor())
        zen._dispatcher.tail[ctrl.name] = prev

        await zen._on_controller_event(ctrl, ev)
        chain = zen._dispatcher.tail[ctrl.name]
        assert chain is not prev

        # Cancel predecessor — chain should continue and dispatch.
        prev.cancel()
        await asyncio.wait_for(chain, timeout=1.0)
        assert dispatched == [1]

        # New link waiting on a live predecessor; cancel the link itself.
        dispatched.clear()
        stuck2 = asyncio.get_running_loop().create_future()

        async def predecessor2() -> None:
            await stuck2

        prev2 = asyncio.create_task(predecessor2())
        zen._dispatcher.tail[ctrl.name] = prev2
        await zen._on_controller_event(ctrl, ev)
        chain2 = zen._dispatcher.tail[ctrl.name]
        await asyncio.sleep(0)  # let chain2 reach await previous
        chain2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await chain2
        assert dispatched == []
        # Cancelling chain2 may have cancelled the Future it was awaiting.
        if not prev2.done():
            stuck2.set_result(None)
            await prev2


@pytest.mark.asyncio
async def test_dispatch_drops_unused_and_deprecated_event_kinds() -> None:
    """LEVEL_CHANGE / GROUP_LEVEL_CHANGE / GROUP_OCCUPIED must not touch entities."""
    from zencontrol import ZenAddress

    zen = ZenControl()
    ctrl = zen.add_controller(id=1, name="house", label="House", host="127.0.0.1", mac="02:00:00:00:00:01")
    light = zen.ctx.light(
        ZenAddress(controller=ctrl, type=ZenAddressType.ECG, number=5),
    )
    light.features = {"brightness": True}
    light.level = 10
    called = False

    async def on_any(**kwargs) -> None:
        nonlocal called
        called = True

    zen.callbacks.light_change = on_any
    zen.callbacks.group_change = on_any

    await zen._dispatcher.dispatch(ctrl, LevelChange(target=5, level=40))
    await zen._dispatcher.dispatch(ctrl, GroupLevelChange(target=64, level=80))
    await zen._dispatcher.dispatch(ctrl, GroupOccupied(target=64, occupied=True))
    assert light.level == 10
    assert called is False
