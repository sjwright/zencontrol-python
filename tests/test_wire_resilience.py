"""Unit tests for Cluster A wire-level resilience."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from helpers_endpoints import fake_endpoint_factory

from zencontrol import ZenController
from zencontrol.api.commands import ZenCommandClient
from zencontrol.api.event_router import DEFAULT_MAX_QUEUE_SIZE, ZenEventReceiver
from zencontrol.api.types import Transport
from zencontrol.exceptions import ZenTimeoutError
from zencontrol.interface import EntityContext
from zencontrol.io.command import (
    ClientConst,
    ZenClient,
    ZenRequest,
    ZenResponse,
    ZenResponseType,
)
from zencontrol.io.event import EventConst, ZenEndpoint, ZenEvent
from zencontrol.utils import local_ip_for_remote


@pytest.mark.asyncio
async def test_event_funnel_drop_oldest_under_backpressure() -> None:
    receiver = ZenEventReceiver(max_queue_size=2)

    def evt(n: int) -> ZenEvent:
        return ZenEvent(
            mac=bytes([n]) * 6,
            target=0,
            code=0,
            payload=b"",
            host="127.0.0.1",
            received_at=0.0,
        )

    receiver._enqueue_event(evt(1))
    receiver._enqueue_event(evt(2))
    assert receiver.dropped_events == 0

    receiver._enqueue_event(evt(3))
    assert receiver.dropped_events == 1
    assert receiver._funnel.qsize() == 2

    first = receiver._funnel.get_nowait()
    second = receiver._funnel.get_nowait()
    assert first.mac == bytes([2]) * 6
    assert second.mac == bytes([3]) * 6


@pytest.mark.asyncio
async def test_receiver_acquire_release_owns_consumer_lifecycle() -> None:
    """Acquire starts the shared consumer; last release stops it intentionally."""
    receiver = ZenEventReceiver()
    receiver._endpoint_factory = fake_endpoint_factory()
    unexpected = AsyncMock()
    receiver.on_unexpected_exit = unexpected

    lease = await receiver.acquire(Transport.UNICAST)
    task = receiver.consumer_task
    assert task is not None and not task.done()

    await lease.release()
    assert receiver.consumer_task is None
    assert task.cancelled() or task.done()
    unexpected.assert_not_awaited()

    # Second release is idempotent
    await lease.release()


@pytest.mark.asyncio
async def test_receiver_unexpected_exit_invokes_hook() -> None:
    receiver = ZenEventReceiver()
    receiver._endpoint_factory = fake_endpoint_factory()
    unexpected = AsyncMock()
    receiver.on_unexpected_exit = unexpected

    lease = await receiver.acquire(Transport.UNICAST)
    task = receiver.consumer_task
    assert task is not None
    # Let _consume reach funnel.get(); cancel-before-start skips its finally.
    await asyncio.sleep(0)

    # Cancel without intentional stop — mimics consumer death
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass

    unexpected.assert_awaited_once()
    await receiver.close()
    await lease.release()


@pytest.mark.asyncio
async def test_multicast_endpoint_joins_before_bind_and_drops_on_close() -> None:
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
            endpoint = await ZenEndpoint.open(
                unicast=False,
                sink=lambda _event: None,
            )

    create_endpoint.assert_awaited_once()
    assert create_endpoint.await_args.kwargs["sock"] is fake_sock
    assert endpoint._mreq is not None

    # First setsockopt should be SO_REUSEADDR (before bind); ADD_MEMBERSHIP after bind
    first_opt = fake_sock.setsockopt.call_args_list[0].args[:2]
    assert first_opt == (socket.SOL_SOCKET, socket.SO_REUSEADDR)
    assert fake_sock.bind.call_args_list[0] == call(("0.0.0.0", EventConst.MULTICAST_PORT))
    add_call = next(c for c in fake_sock.setsockopt.call_args_list if c.args[:2] == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP))
    assert add_call.args[2] == endpoint._mreq
    bind_pos = next(i for i, (name, *_) in enumerate(fake_sock.method_calls) if name == "bind")
    add_pos = next(
        i
        for i, (name, args, _) in enumerate(fake_sock.method_calls)
        if name == "setsockopt" and args[:2] == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)
    )
    assert bind_pos < add_pos

    joined_mreq = add_call.args[2]
    await endpoint.close()

    drop_calls = [c for c in fake_sock.setsockopt.call_args_list if c.args[:2] == (socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP)]
    assert len(drop_calls) == 1
    assert drop_calls[0].args[2] == joined_mreq
    fake_transport.close.assert_called_once()
    assert endpoint._mreq is None


def test_local_ip_for_remote_returns_ipv4() -> None:
    ip = local_ip_for_remote("127.0.0.1")
    assert ip.count(".") == 3
    # Loopback route should advertise a local address (often 127.0.0.1)
    parts = [int(p) for p in ip.split(".")]
    assert all(0 <= p <= 255 for p in parts)


def test_resolve_host_sync_skips_dns_for_ipv4_literal() -> None:
    from zencontrol.utils import resolve_host_sync

    with patch("zencontrol.utils.socket.gethostbyname") as dns:
        assert resolve_host_sync("192.168.1.50") == "192.168.1.50"
        dns.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_host_runs_dns_in_executor() -> None:
    from zencontrol.utils import resolve_host

    with patch("zencontrol.utils.socket.gethostbyname", return_value="10.0.0.1") as dns:
        assert await resolve_host("controller.local") == "10.0.0.1"
        dns.assert_called_once_with("controller.local")


def test_subscribe_does_not_call_gethostbyname() -> None:
    from zencontrol.api.event_router import ZenEventReceiver

    receiver = ZenEventReceiver()

    async def handler(_ev: object) -> None:
        pass

    with patch("socket.gethostbyname") as dns:
        receiver.subscribe(handler, host="192.168.1.50")
        dns.assert_not_called()


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
        fut: asyncio.Future[ZenResponse] = loop.create_future()
        req = ZenRequest(command=0x10, data=[0x00, 0x00, 0x00, 0x00])
        req.seq = 1
        client._pending[1] = (fut, req)

        client._mark_disconnected(OSError("icmp unreachable"))

        assert client.is_connected() is False
        assert fut.done()
        assert fut.result().response_type == ZenResponseType.TIMEOUT
        transport.close.assert_called_once()
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_send_packet_timeout_invalidates_client_and_refreshes_ip() -> None:
    protocol = ZenCommandClient()
    controller = ZenController(
        id=1,
        name="ctrl",
        label="Ctrl",
        host="zen.local",
        port=5108,
        ctx=EntityContext(commands=protocol),
    )

    fake_client = MagicMock()
    fake_client.is_connected.return_value = True
    fake_client.send_request_with_retries = AsyncMock(return_value=ZenResponse(ZenResponseType.TIMEOUT))
    fake_client.close = AsyncMock()
    protocol.set_client(controller, fake_client)
    controller.set_resolved_ip("192.0.2.10")

    with patch.object(controller, "refresh_ip", wraps=controller.refresh_ip) as refresh:
        with pytest.raises(ZenTimeoutError):
            await protocol._send_packet(
                controller,
                ZenRequest(command=0x10, data=[0x00, 0x00, 0x00, 0x00]),
            )

    refresh.assert_called_once()
    fake_client.close.assert_awaited()
    assert protocol.client_for(controller) is None


@pytest.mark.asyncio
async def test_ensure_client_recreates_when_disconnected() -> None:
    protocol = ZenCommandClient()
    controller = ZenController(
        id=1,
        name="ctrl",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        ctx=EntityContext(commands=protocol),
    )
    stale = MagicMock()
    stale.is_connected.return_value = False
    stale.close = AsyncMock()
    protocol.set_client(controller, stale)

    new_client = MagicMock()
    new_client.is_connected.return_value = True
    with patch(
        "zencontrol.api.commands.ZenClient.create",
        new=AsyncMock(return_value=new_client),
    ) as create:
        await protocol._ensure_client(controller)

    stale.close.assert_awaited()
    create.assert_awaited_once()
    assert protocol.client_for(controller) is new_client


def test_default_retries_constant() -> None:
    assert ClientConst.DEFAULT_RETRIES >= 1
    assert DEFAULT_MAX_QUEUE_SIZE >= 1
    assert EventConst.MULTICAST_PORT == 6969


@pytest.mark.asyncio
async def test_send_request_timeout_with_retries_returns_timeout() -> None:
    """wait_for used to cancel the Future; retries must still yield TIMEOUT."""
    client = ZenClient(("127.0.0.1", 5108))
    client._closed = False
    transport = MagicMock()
    transport.is_closing.return_value = False
    client._transport = transport

    req = ZenRequest(command=0x10, data=[0x00, 0x00, 0x00, 0x00])
    resp = await client.send_request(req, timeout=0.05, retries=1)
    assert resp.response_type == ZenResponseType.TIMEOUT
    assert transport.sendto.call_count == 2


@pytest.mark.asyncio
async def test_send_request_allows_concurrent_awaits() -> None:
    """Lock must not cover RTT — two in-flight requests with different seqs."""
    client = ZenClient(("127.0.0.1", 5108))
    client._closed = False
    transport = MagicMock()
    transport.is_closing.return_value = False
    client._transport = transport

    async def one_request(cmd: int) -> ZenResponse:
        return await client.send_request(
            ZenRequest(command=cmd, data=[0x00, 0x00, 0x00, 0x00]),
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
        fut.set_result(ZenResponse(ZenResponseType.OK, seq=seq, request=req))
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.response_type == ZenResponseType.OK
    assert r2.response_type == ZenResponseType.OK


@pytest.mark.asyncio
async def test_invalid_checksum_completes_pending_as_invalid() -> None:
    client = ZenClient(("127.0.0.1", 5108))
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[ZenResponse] = loop.create_future()
    req = ZenRequest(command=0x10, data=[0x00, 0x00, 0x00, 0x00])
    req.seq = 7
    client._pending[7] = (fut, req)

    # OK type, seq 7, len 0, bad checksum
    await client._receive_response(bytes([0xA0, 7, 0, 0xFF]), ("127.0.0.1", 5108))
    assert fut.done()
    assert fut.result().response_type == ZenResponseType.INVALID


@pytest.mark.asyncio
async def test_send_packet_error_returns_without_raising() -> None:
    from zencontrol.api.types import ZenErrorCode

    protocol = ZenCommandClient()
    controller = ZenController(
        id=1,
        name="ctrl",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        ctx=EntityContext(commands=protocol),
    )
    fake_client = MagicMock()
    fake_client.is_connected.return_value = True
    fake_client.send_request_with_retries = AsyncMock(
        return_value=ZenResponse(
            ZenResponseType.ERROR,
            data=bytes([ZenErrorCode.PAID_FEATURE.value]),
        )
    )
    protocol.set_client(controller, fake_client)

    response = await protocol._send_packet(
        controller,
        ZenRequest(command=0x10, data=[0x00, 0x00, 0x00, 0x00]),
    )
    assert response.response_type == ZenResponseType.ERROR
    assert response.data == bytes([ZenErrorCode.PAID_FEATURE.value])


def test_mac_requires_six_bytes() -> None:
    protocol = ZenCommandClient()
    with pytest.raises(ValueError, match="6 bytes"):
        ZenController(
            id=1,
            name="ctrl",
            label="Ctrl",
            host="127.0.0.1",
            port=5108,
            mac="aa:bb",
            ctx=EntityContext(commands=protocol),
        )

    ctrl = ZenController(
        id=1,
        name="ctrl2",
        label="Ctrl",
        host="127.0.0.1",
        port=5108,
        mac="aa:bb:cc:dd:ee:ff",
        ctx=EntityContext(commands=protocol),
    )
    assert ctrl.mac_bytes == bytes.fromhex("aabbccddeeff")
