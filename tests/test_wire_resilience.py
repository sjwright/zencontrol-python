"""Unit tests for Cluster A wire-level resilience."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zencontrol.api.models import ZenController
from zencontrol.api.protocol import ZenProtocol
from zencontrol.exceptions import ZenTimeoutError
from zencontrol.io.command import ClientConst, Request, Response, ResponseType, ZenClient
from zencontrol.io.event import EventConst, ZenEvent, ZenListener
from zencontrol.utils import local_ip_for_remote


def _make_event(code: int = 1) -> ZenEvent:
    return ZenEvent(
        raw_data=b"",
        event_code=code,
        target=0,
        payload=b"",
        mac_address=b"\x00" * 6,
        ip_address="127.0.0.1",
        ip_port=6969,
    )


@pytest.mark.asyncio
async def test_event_queue_drop_oldest_under_backpressure() -> None:
    listener = ZenListener(max_queue_size=2)
    listener._enqueue_event(_make_event(1))
    listener._enqueue_event(_make_event(2))
    assert listener.dropped_events == 0

    listener._enqueue_event(_make_event(3))
    assert listener.dropped_events == 1
    assert listener._event_queue.qsize() == 2

    first = listener._event_queue.get_nowait()
    second = listener._event_queue.get_nowait()
    assert first.event_code == 2
    assert second.event_code == 3


def test_local_ip_for_remote_returns_ipv4() -> None:
    ip = local_ip_for_remote("127.0.0.1")
    assert ip.count(".") == 3
    # Loopback route should advertise a local address (often 127.0.0.1)
    parts = [int(p) for p in ip.split(".")]
    assert all(0 <= p <= 255 for p in parts)


def test_is_connected_false_when_transport_closing() -> None:
    client = ZenClient(("127.0.0.1", 5108))
    transport = MagicMock()
    transport.is_closing.return_value = True
    client._transport = transport
    client._closed = False
    assert client.is_connected() is False


def test_mark_disconnected_unblocks_pending_with_timeout() -> None:
    client = ZenClient(("127.0.0.1", 5108))
    transport = MagicMock()
    transport.is_closing.return_value = False
    client._transport = transport

    loop = asyncio.new_event_loop()
    try:
        fut: asyncio.Future[Response] = loop.create_future()
        req = Request(command=0x10, data=[0x00, 0x00, 0x00, 0x00])
        req.seq = 1
        client._pending[1] = (fut, req)

        client._mark_disconnected(OSError("icmp unreachable"))

        assert client.is_connected() is False
        assert fut.done()
        assert fut.result().response_type == ResponseType.TIMEOUT
        transport.close.assert_called_once()
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_send_packet_timeout_invalidates_client_and_refreshes_ip() -> None:
    protocol = ZenProtocol()
    controller = ZenController(
        id="1",
        name="ctrl",
        label="Ctrl",
        host="zen.local",
        port=5108,
        protocol=protocol,
    )
    protocol.set_controllers([controller])

    fake_client = MagicMock()
    fake_client.is_connected.return_value = True
    fake_client.send_request_with_retries = AsyncMock(
        return_value=Response(ResponseType.TIMEOUT)
    )
    fake_client.close = AsyncMock()
    controller.client = fake_client
    controller._ip = "192.0.2.10"

    with patch.object(controller, "refresh_ip", wraps=controller.refresh_ip) as refresh:
        with pytest.raises(ZenTimeoutError):
            await protocol._send_packet(
                controller,
                Request(command=0x10, data=[0x00, 0x00, 0x00, 0x00]),
            )

    refresh.assert_called_once()
    fake_client.close.assert_awaited()
    assert controller.client is None


@pytest.mark.asyncio
async def test_ensure_client_recreates_when_disconnected() -> None:
    protocol = ZenProtocol()
    controller = ZenController(
        id="1",
        name="ctrl",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        protocol=protocol,
    )
    stale = MagicMock()
    stale.is_connected.return_value = False
    stale.close = AsyncMock()
    controller.client = stale

    new_client = MagicMock()
    new_client.is_connected.return_value = True
    with patch(
        "zencontrol.api.protocol.ZenClient.create",
        new=AsyncMock(return_value=new_client),
    ) as create:
        await protocol._ensure_client(controller)

    stale.close.assert_awaited()
    create.assert_awaited_once()
    assert controller.client is new_client


def test_resolve_unicast_advertise_ip_uses_explicit_listen_ip() -> None:
    protocol = ZenProtocol(unicast=True, listen_ip="10.0.0.5")
    assert protocol.local_ip == "10.0.0.5"
    assert protocol._resolve_unicast_advertise_ip() == "10.0.0.5"


def test_resolve_unicast_advertise_ip_uses_route_via_controller() -> None:
    protocol = ZenProtocol(unicast=True)
    assert protocol.local_ip is None
    controller = ZenController(
        id="1",
        name="ctrl",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        protocol=protocol,
    )
    protocol.set_controllers([controller])
    with patch(
        "zencontrol.api.protocol.local_ip_for_remote",
        return_value="192.168.1.50",
    ) as route:
        assert protocol._resolve_unicast_advertise_ip() == "192.168.1.50"
        route.assert_called_once_with(controller.ip)


def test_default_retries_constant() -> None:
    assert ClientConst.DEFAULT_RETRIES >= 1
    assert EventConst.DEFAULT_MAX_QUEUE_SIZE >= 1
