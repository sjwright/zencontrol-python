"""Unit tests for Cluster A wire-level resilience."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from zencontrol import ZenController
from zencontrol.api.protocol import ZenProtocol
from zencontrol.exceptions import ZenTimeoutError
from zencontrol.io.command import (
    ClientConst,
    Request,
    Response,
    ResponseType,
    ZenClient,
)
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


@pytest.mark.asyncio
async def test_listener_run_stop_owns_consumer_lifecycle() -> None:
    """ZenListener.run/stop own the consumer task and unexpected-exit hook."""
    listener = ZenListener()
    received: list[int] = []
    unexpected = AsyncMock()

    async def on_event(event: ZenEvent) -> None:
        received.append(event.event_code)

    # No socket: feed the queue directly and end the stream via stop_event
    listener._enqueue_event(_make_event(7))
    task = listener.run(on_event, on_unexpected_exit=unexpected)
    assert listener.is_running()

    for _ in range(20):
        if received == [7]:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("handler did not receive enqueued event")

    await listener.stop()
    assert not listener.is_running()
    assert task.cancelled() or task.done()
    unexpected.assert_not_awaited()

    # Second stop is idempotent
    await listener.stop()


@pytest.mark.asyncio
async def test_listener_unexpected_exit_invokes_hook() -> None:
    listener = ZenListener()
    unexpected = AsyncMock()

    async def on_event(event: ZenEvent) -> None:
        pass

    # End the events() loop immediately by setting stop before run pumps
    listener._stop_event.set()
    task = listener.run(on_event, on_unexpected_exit=unexpected)
    await asyncio.wait_for(task, timeout=1.0)
    unexpected.assert_awaited_once()


@pytest.mark.asyncio
async def test_multicast_listener_joins_before_endpoint_and_drops_on_stop() -> None:
    """Reuse/join must happen before asyncio owns the socket; DROP before close."""
    fake_sock = MagicMock()
    fake_transport = MagicMock()
    fake_transport.is_closing.return_value = False
    fake_transport.get_extra_info.return_value = fake_sock

    with patch("zencontrol.io.event.socket.socket", return_value=fake_sock):
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "create_datagram_endpoint",
            new_callable=AsyncMock,
            return_value=(fake_transport, MagicMock()),
        ) as create_endpoint:
            listener = await ZenListener.create(unicast=False)

    create_endpoint.assert_awaited_once()
    assert create_endpoint.await_args.kwargs["sock"] is fake_sock
    assert listener._mreq is not None

    # First setsockopt should be SO_REUSEADDR (before bind); ADD_MEMBERSHIP after bind
    first_opt = fake_sock.setsockopt.call_args_list[0].args[:2]
    assert first_opt == (socket.SOL_SOCKET, socket.SO_REUSEADDR)
    assert fake_sock.bind.call_args_list[0] == call(
        ("0.0.0.0", EventConst.MULTICAST_PORT)
    )
    add_call = next(
        c
        for c in fake_sock.setsockopt.call_args_list
        if c.args[:2] == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)
    )
    assert add_call.args[2] == listener._mreq
    bind_pos = next(
        i for i, (name, *_) in enumerate(fake_sock.method_calls) if name == "bind"
    )
    add_pos = next(
        i
        for i, (name, args, _) in enumerate(fake_sock.method_calls)
        if name == "setsockopt"
        and args[:2] == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)
    )
    assert bind_pos < add_pos

    joined_mreq = add_call.args[2]
    await listener.stop_listening()

    drop_calls = [
        c
        for c in fake_sock.setsockopt.call_args_list
        if c.args[:2] == (socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP)
    ]
    assert len(drop_calls) == 1
    assert drop_calls[0].args[2] == joined_mreq
    fake_transport.close.assert_called_once()
    assert listener._mreq is None


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
        id=1,
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
        id=1,
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
        id=1,
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


@pytest.mark.asyncio
async def test_send_request_timeout_with_retries_returns_timeout() -> None:
    """wait_for used to cancel the Future; retries must still yield TIMEOUT."""
    client = ZenClient(("127.0.0.1", 5108))
    client._closed = False
    transport = MagicMock()
    transport.is_closing.return_value = False
    client._transport = transport

    req = Request(command=0x10, data=[0x00, 0x00, 0x00, 0x00])
    resp = await client.send_request(req, timeout=0.05, retries=1)
    assert resp.response_type == ResponseType.TIMEOUT
    assert transport.sendto.call_count == 2


@pytest.mark.asyncio
async def test_send_request_allows_concurrent_awaits() -> None:
    """Lock must not cover RTT — two in-flight requests with different seqs."""
    client = ZenClient(("127.0.0.1", 5108))
    client._closed = False
    transport = MagicMock()
    transport.is_closing.return_value = False
    client._transport = transport

    async def one_request(cmd: int) -> Response:
        return await client.send_request(
            Request(command=cmd, data=[0x00, 0x00, 0x00, 0x00]),
            timeout=0.2,
            retries=0,
        )

    t1 = asyncio.create_task(one_request(0x10))
    t2 = asyncio.create_task(one_request(0x11))
    await asyncio.sleep(0)  # let both register pending
    assert len(client._pending) == 2
    seqs = list(client._pending.keys())
    for seq in seqs:
        fut, req = client._pending[seq]
        fut.set_result(Response(ResponseType.OK, seq=seq, request=req))
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.response_type == ResponseType.OK
    assert r2.response_type == ResponseType.OK


@pytest.mark.asyncio
async def test_invalid_checksum_completes_pending_as_invalid() -> None:
    client = ZenClient(("127.0.0.1", 5108))
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Response] = loop.create_future()
    req = Request(command=0x10, data=[0x00, 0x00, 0x00, 0x00])
    req.seq = 7
    client._pending[7] = (fut, req)

    # OK type, seq 7, len 0, bad checksum
    await client._receive_response(bytes([0xA0, 7, 0, 0xFF]), ("127.0.0.1", 5108))
    assert fut.done()
    assert fut.result().response_type == ResponseType.INVALID


@pytest.mark.asyncio
async def test_send_packet_error_returns_without_raising() -> None:
    from zencontrol.api.types import ZenErrorCode

    protocol = ZenProtocol()
    controller = ZenController(
        id=1,
        name="ctrl",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        protocol=protocol,
    )
    fake_client = MagicMock()
    fake_client.is_connected.return_value = True
    fake_client.send_request_with_retries = AsyncMock(
        return_value=Response(
            ResponseType.ERROR,
            data=bytes([ZenErrorCode.PAID_FEATURE.value]),
        )
    )
    controller.client = fake_client

    data, code = await protocol._send_packet(
        controller,
        Request(command=0x10, data=[0x00, 0x00, 0x00, 0x00]),
    )
    assert code == ResponseType.ERROR.value
    assert data == bytes([ZenErrorCode.PAID_FEATURE.value])


def test_mac_requires_six_bytes() -> None:
    protocol = ZenProtocol()
    with pytest.raises(ValueError, match="6 bytes"):
        ZenController(
            id=1,
            name="ctrl",
            label="Ctrl",
            host="127.0.0.1",
            port=5108,
            mac="aa:bb",
            protocol=protocol,
        )

    ctrl = ZenController(
        id=1,
        name="ctrl2",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        mac="aa:bb:cc:dd:ee:ff",
        protocol=protocol,
    )
    assert ctrl.mac_bytes == bytes.fromhex("aabbccddeeff")


def test_response_timeout_constant_matches_client() -> None:
    from zencontrol.api.types import Const

    assert Const.RESPONSE_TIMEOUT == ClientConst.DEFAULT_TIMEOUT
