"""Unit tests for multicast controller discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zencontrol.api.models import DiscoveredController, ZenController
from zencontrol.api.protocol import ZenProtocol
from zencontrol.io.event import ZenEvent


def _event(
    *,
    ip: str = "192.168.1.50",
    mac: bytes = b"\x02\x00\x00\x00\x00\x01",
    code: int = 1,
) -> ZenEvent:
    return ZenEvent(
        raw_data=b"ZC" + mac + bytes([0, code, 0]),
        event_code=code,
        target=0,
        payload=b"\x00",
        mac_address=mac,
        ip_address=ip,
        ip_port=6969,
    )


@pytest.mark.asyncio
async def test_unknown_multicast_queries_label_and_remembers() -> None:
    protocol = ZenProtocol()
    seen: list[DiscoveredController] = []

    async def on_discovered(discovered: DiscoveredController) -> None:
        seen.append(discovered)

    protocol.callbacks.controller_discovered = on_discovered

    with patch.object(
        protocol, "query_controller_label", new_callable=AsyncMock, return_value="Kitchen"
    ) as query:
        await protocol._process_zen_event(_event())

    query.assert_awaited_once()
    assert len(protocol.identified_controllers) == 1
    discovered = protocol.identified_controllers[0]
    assert discovered.host == "192.168.1.50"
    assert discovered.mac == "02:00:00:00:00:01"
    assert discovered.label == "Kitchen"
    assert discovered.port == 5108
    assert seen == [discovered]


@pytest.mark.asyncio
async def test_second_packet_from_same_mac_or_ip_is_ignored() -> None:
    protocol = ZenProtocol()
    with patch.object(
        protocol, "query_controller_label", new_callable=AsyncMock, return_value="Kitchen"
    ) as query:
        await protocol._process_zen_event(_event())
        await protocol._process_zen_event(_event())
        await protocol._process_zen_event(
            _event(ip="192.168.1.99", mac=b"\x02\x00\x00\x00\x00\x01")
        )
        await protocol._process_zen_event(
            _event(ip="192.168.1.50", mac=b"\xaa\xbb\xcc\xdd\xee\xff")
        )

    assert query.await_count == 1
    assert len(protocol.identified_controllers) == 1


@pytest.mark.asyncio
async def test_registered_controller_is_not_discovered() -> None:
    protocol = ZenProtocol()
    ctrl = ZenController(
        id="1",
        name="known",
        label="Known",
        host="192.168.1.50",
        port=5108,
        mac="02:00:00:00:00:01",
        protocol=protocol,
    )
    protocol.set_controllers([ctrl])

    with patch.object(
        protocol, "query_controller_label", new_callable=AsyncMock, return_value="Nope"
    ) as query:
        await protocol._process_zen_event(_event())

    query.assert_not_awaited()
    assert protocol.identified_controllers == []


@pytest.mark.asyncio
async def test_registering_controller_forgets_identified() -> None:
    protocol = ZenProtocol()
    with patch.object(
        protocol, "query_controller_label", new_callable=AsyncMock, return_value="Kitchen"
    ):
        await protocol._process_zen_event(_event())
    assert len(protocol.identified_controllers) == 1

    ctrl = ZenController(
        id="1",
        name="kitchen",
        label="Kitchen",
        host="192.168.1.50",
        port=5108,
        mac="02:00:00:00:00:01",
        protocol=protocol,
    )
    protocol.set_controllers([ctrl])
    assert protocol.identified_controllers == []


@pytest.mark.asyncio
async def test_label_query_failure_still_remembers_controller() -> None:
    protocol = ZenProtocol()
    with patch.object(
        protocol,
        "query_controller_label",
        new_callable=AsyncMock,
        side_effect=TimeoutError("offline"),
    ):
        await protocol._process_zen_event(_event())

    assert len(protocol.identified_controllers) == 1
    assert protocol.identified_controllers[0].label is None


@pytest.mark.asyncio
async def test_new_controller_discovered_while_one_is_registered() -> None:
    protocol = ZenProtocol()
    known = ZenController(
        id="1",
        name="known",
        label="Known",
        host="192.168.1.10",
        port=5108,
        mac="11:22:33:44:55:66",
        protocol=protocol,
    )
    protocol.set_controllers([known])

    with patch.object(
        protocol, "query_controller_label", new_callable=AsyncMock, return_value="Annex"
    ) as query:
        await protocol._process_zen_event(_event())

    query.assert_awaited_once()
    assert len(protocol.identified_controllers) == 1
    assert protocol.identified_controllers[0].label == "Annex"
