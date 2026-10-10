"""Request correlation and failure recovery through the actual receive paths."""

from __future__ import annotations

import asyncio
from functools import reduce
from operator import xor
from unittest.mock import AsyncMock, MagicMock

import pytest

from zencontrol.io.command import ZenClient
from zencontrol.io.command_tcp import ZenTcpClient
from zencontrol.io.const import ClientConst
from zencontrol.io.models import ZenRequest, ZenResponseType


def _reply(seq: int, data: bytes = b"", kind=ZenResponseType.ANSWER) -> bytes:
    body = bytes([kind, seq, len(data)]) + data
    return body + bytes([reduce(xor, body, 0)])


@pytest.fixture(params=[ZenClient, ZenTcpClient], ids=["udp", "tcp"])
async def wire_client(request):
    """Replace only the OS transport; keep request allocation and parsing real."""
    client = request.param(("127.0.0.1", 5108))
    sent = asyncio.Queue()
    transport = MagicMock()
    transport.is_closing.return_value = False
    if isinstance(client, ZenTcpClient):
        transport.write.side_effect = sent.put_nowait
        transport.drain = AsyncMock()
        transport.wait_closed = AsyncMock()
        client._writer = transport
        client._reader = asyncio.StreamReader()
        client._reader_task = asyncio.create_task(client._tcp_reader_loop())
    else:
        transport.sendto.side_effect = sent.put_nowait
        client._transport = transport
    try:
        yield client, sent
    finally:
        await client.close()


def _receive(client, packet):
    if isinstance(client, ZenTcpClient):
        client._reader.feed_data(packet)
    else:
        client._receive_response(packet, client.server)


async def _send(client, sent, command=0x24):
    req = ZenRequest(command=command, data=[0])
    task = asyncio.create_task(client.send_request(req, timeout=1, retries=0))
    packet = await asyncio.wait_for(sent.get(), 1)
    return task, req, packet[1]


async def test_reversed_replies_preserve_request_and_payload(wire_client):
    client, sent = wire_client
    first, req_a, seq_a = await _send(client, sent, 0x24)
    second, req_b, seq_b = await _send(client, sent, 0x25)
    _receive(client, _reply(seq_b, b"second"))
    reply_b = await asyncio.wait_for(second, 1)
    assert reply_b.request is req_b
    assert reply_b.data == b"second"
    assert not first.done()
    _receive(client, _reply(seq_a, b"first"))
    reply_a = await asyncio.wait_for(first, 1)
    assert reply_a.request is req_a
    assert reply_a.data == b"first"
    assert client._pending == {}


async def test_cancelled_request_and_late_reply_do_not_complete_another(wire_client):
    client, sent = wire_client
    cancelled, _, old_seq = await _send(client, sent)
    survivor, req, seq = await _send(client, sent)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert old_seq not in client._pending
    _receive(client, _reply(old_seq, b"stale"))
    _receive(client, _reply(seq, b"current"))
    result = await asyncio.wait_for(survivor, 1)
    assert result.request is req
    assert result.data == b"current"
    assert client._pending == {}


async def test_sequence_wrap_skips_live_requests(wire_client):
    client, sent = wire_client
    first, _, first_seq = await _send(client, sent)
    client._next_seq = 255
    second, _, second_seq = await _send(client, sent)
    third, _, third_seq = await _send(client, sent)
    assert (first_seq, second_seq, third_seq) == (0, 255, 1)
    for seq, value in [(third_seq, b"c"), (first_seq, b"a"), (second_seq, b"b")]:
        _receive(client, _reply(seq, value))
    responses = await asyncio.wait_for(asyncio.gather(first, second, third), 1)
    assert [r.data for r in responses] == [b"a", b"b", b"c"]


@pytest.mark.parametrize("fault", ["checksum", "length", "type"])
async def test_invalid_reply_fails_only_its_request(wire_client, fault):
    client, sent = wire_client
    # A malformed length in TCP consumes stream bytes, so test envelope validation
    # directly for that case; fragmentation/EOF have separate stream tests below.
    first, _, seq_a = await _send(client, sent)
    second, _, seq_b = await _send(client, sent)
    bad = bytearray(_reply(seq_a, b"bad"))
    if fault == "checksum":
        bad[-1] ^= 0xFF
    elif fault == "length":
        bad[2] += 1
    else:
        bad[0] = 0xFF
        bad[-1] = reduce(xor, bad[:-1], 0)
    client._receive_response(bytes(bad), client.server)
    assert (await asyncio.wait_for(first, 1)).response_type is ZenResponseType.INVALID
    assert not second.done()
    _receive(client, _reply(seq_b, b"good"))
    assert (await asyncio.wait_for(second, 1)).data == b"good"


@pytest.mark.parametrize("recover", [False, True], ids=["exhausted", "recovers"])
async def test_queue_full_retries_are_bounded_and_preserve_command(wire_client, monkeypatch, recover):
    client, sent = wire_client
    monkeypatch.setattr(ClientConst, "QUEUE_FAILURE_BASE_DELAY", 0)
    req = ZenRequest(command=0x24, data=[7, 0, 0, 0])
    task = asyncio.create_task(client.send_request_with_retries(req, timeout=1, retries=0, queue_retries=2))
    packets = []
    for attempt in range(3):
        packet = await asyncio.wait_for(sent.get(), 1)
        packets.append(packet)
        if recover and attempt == 2:
            _receive(client, _reply(packet[1], b"recovered"))
        else:
            _receive(client, _reply(packet[1], bytes([ClientConst.QUEUE_FAILURE]), ZenResponseType.ERROR))
    result = await asyncio.wait_for(task, 1)
    assert result.response_type is (ZenResponseType.ANSWER if recover else ZenResponseType.ERROR)
    assert result.data == (b"recovered" if recover else bytes([ClientConst.QUEUE_FAILURE]))
    assert all(packet[2:-1] == packets[0][2:-1] for packet in packets)
    assert sent.empty()
    assert client._pending == {}


async def test_non_queue_error_is_never_retried(wire_client):
    client, sent = wire_client
    req = ZenRequest(command=0x24, data=[0])
    task = asyncio.create_task(client.send_request_with_retries(req, timeout=1))
    packet = await asyncio.wait_for(sent.get(), 1)
    _receive(client, _reply(packet[1], b"\xb1", ZenResponseType.ERROR))
    assert (await asyncio.wait_for(task, 1)).data == b"\xb1"
    assert sent.empty()


@pytest.mark.parametrize("wire_client", [ZenTcpClient], indirect=True, ids=["tcp"])
async def test_tcp_fragmented_and_coalesced_replies(wire_client):
    client, sent = wire_client
    first, _, seq_a = await _send(client, sent)
    second, _, seq_b = await _send(client, sent)
    packet = _reply(seq_b, b"second")
    for byte in packet[:4]:
        client._reader.feed_data(bytes([byte]))
        await asyncio.sleep(0)
    assert not first.done() and not second.done()
    client._reader.feed_data(packet[4:] + _reply(seq_a, b"first"))
    responses = await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert [r.data for r in responses] == [b"first", b"second"]


@pytest.mark.parametrize("wire_client", [ZenTcpClient], indirect=True, ids=["tcp"])
@pytest.mark.parametrize("partial", [b"", b"\xa1", b"\xa1\x00\x05ab"])
async def test_tcp_eof_unblocks_all_pending_requests(wire_client, partial):
    client, sent = wire_client
    first, _, _ = await _send(client, sent)
    second, _, _ = await _send(client, sent)
    client._reader.feed_data(partial)
    client._reader.feed_eof()
    replies = await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert all(r.response_type is ZenResponseType.TIMEOUT for r in replies)
    assert not client.is_connected()
    assert client._pending == {}


async def test_timeout_late_reply_and_next_request_are_isolated(wire_client):
    client, sent = wire_client
    req = ZenRequest(command=0x24, data=[0])
    expired = asyncio.create_task(client.send_request(req, timeout=0.01, retries=0))
    old_packet = await asyncio.wait_for(sent.get(), 1)
    assert (await asyncio.wait_for(expired, 1)).response_type is ZenResponseType.TIMEOUT
    assert client._pending == {}
    next_task, _, seq = await _send(client, sent)
    _receive(client, _reply(old_packet[1], b"expired"))
    _receive(client, _reply(seq, b"fresh"))
    assert (await asyncio.wait_for(next_task, 1)).data == b"fresh"


async def test_sequence_exhaustion_does_not_overwrite_pending_requests(wire_client):
    client, sent = wire_client
    tasks = []
    try:
        for _ in range(256):
            task, _, seq = await _send(client, sent)
            tasks.append((task, seq))
        with pytest.raises(RuntimeError, match="256 sequence numbers"):
            await client.send_request(ZenRequest(command=0x24, data=[0]), timeout=1, retries=0)
        assert len(client._pending) == 256
        assert sent.empty()
        # Free exactly one slot, then prove it is reusable without displacing others.
        released, seq = tasks.pop(37)
        _receive(client, _reply(seq, b"released"))
        assert (await asyncio.wait_for(released, 1)).data == b"released"
        replacement, _, new_seq = await _send(client, sent)
        tasks.append((replacement, new_seq))
        assert new_seq == seq
        for _, pending_seq in tasks:
            _receive(client, _reply(pending_seq, bytes([pending_seq])))
        results = await asyncio.wait_for(asyncio.gather(*(task for task, _ in tasks)), 1)
        assert [r.data for r in results] == [bytes([s]) for _, s in tasks]
        assert client._pending == {}
    finally:
        for task, _ in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*(task for task, _ in tasks), return_exceptions=True)


async def test_close_unblocks_all_waiters_and_releases_pending_requests(wire_client):
    client, sent = wire_client
    first, _, _ = await _send(client, sent)
    second, _, _ = await _send(client, sent)
    await client.close()
    results = await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert [r.response_type for r in results] == [ZenResponseType.TIMEOUT] * 2
    assert client._pending == {}
    await client.close()


async def test_send_on_closed_client_returns_timeout(wire_client):
    client, _ = wire_client
    await client.close()
    response = await client.send_request(ZenRequest(command=0x24, data=[0]), timeout=1)
    assert response.response_type is ZenResponseType.TIMEOUT
