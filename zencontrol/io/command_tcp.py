"""
Wire-level TCP command client for ZenControl TPI Advanced
=========================================================

This module takes care of framing, checksums, sequence numbers, matching replies
to waiters, and retries, but has no knowledge of TPI commands or DALI semantics.

The host creates one long-lived "ZenTcpClient" per controller. It represents one
connected TCP socket talking to one host:port. Same request/response framing as
UDP "ZenClient", but responses are reassembled from the stream (header then body).
Requires firmware ≥ 2.2.32. Events remain UDP (multicast/unicast) either way.

You send commands by constructing a "ZenRequest" (opcode + data + "ZenRequestType")
and calling send_request or send_request_with_retries.

You await a "ZenResponse" and interpret the result.

TCP is reliable, so send_request defaults to retries=0 (no retransmit / duplicate
commands). Queue-full errors are still retried with a backoff via
send_request_with_retries.

Bad packets and transport death surface as "TIMEOUT" / "INVALID" responses rather than
raising exceptions, so callers can use one recovery path.

-----------------------------------------------------
Basic example:

    client = await ZenTcpClient.create(("192.0.2.10", 5108))
    async with client:
        req = ZenRequest(command=0x10, data=[0x01, 0xAA, 0x00, 0x00])
        resp = await client.send_request(req)
        if resp.response_type == ZenResponseType.ANSWER:
            print("Answer:", resp.data)

-----------------------------------------------------
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Self

from .const import ClientConst
from .models import ZenRequest, ZenResponse, ZenResponseType


class ZenTcpClient:
    """
    ZenRequest:  [0x04, seq, command, address, data(3|7), checksum]
    ZenResponse: [response_type, seq, data_len, data..., checksum]
      - checksum = XOR of all preceding bytes
      - seq is 1 byte (0..255), auto-incremented & reused for retries
      - On any non-catastrophic parse problem, deliver ZenResponseType.INVALID instead of raising.
      - Responses are reassembled from the TCP stream (header then body).
    """

    def __init__(
        self,
        server: tuple[str, int],
        logger: logging.Logger | None = None,
        *,
        print_traffic: bool = False,
    ):
        self.server = server
        self.logger = logger or logging.getLogger(__name__)
        self.print_traffic = print_traffic
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, tuple[asyncio.Future[ZenResponse], ZenRequest]] = {}
        self._next_seq: int = 0
        self._closed = False
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        server: tuple[str, int],
        logger: logging.Logger | None = None,
        *,
        print_traffic: bool = False,
    ) -> Self:
        self = cls(server, logger, print_traffic=print_traffic)
        self._reader, self._writer = await asyncio.open_connection(server[0], server[1])
        self._reader_task = asyncio.create_task(
            self._tcp_reader_loop(),
            name=f"zen-tcp-reader-{server[0]}:{server[1]}",
        )
        self.logger.info("Connected to Zen server at %s:%s via TCP", server[0], server[1])
        return self

    async def _tcp_reader_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closed:
                header = await self._reader.readexactly(3)
                body = await self._reader.readexactly(header[2] + 1)
                self._receive_response(header + body, self.server)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError, OSError) as exc:
            self._mark_disconnected(exc)

    def _mark_disconnected(self, exc: Exception | None = None) -> None:
        """Mark the client dead after transport loss (must not await or take _lock)."""
        if self._closed:
            return
        self._closed = True
        writer = self._writer
        self._writer = None
        self._reader = None
        reader_task = self._reader_task
        self._reader_task = None
        # Unblock waiters with TIMEOUT so callers use the normal recovery path
        for fut, req in list(self._pending.values()):
            if not fut.done():
                fut.set_result(ZenResponse(ZenResponseType.TIMEOUT, request=req))
        self._pending.clear()
        if writer is not None and not writer.is_closing():
            writer.close()
        if reader_task is not None and not reader_task.done():
            reader_task.cancel()
        if exc:
            self.logger.debug("ZenTcpClient marked disconnected: %s", exc)

    def _is_disconnected(self) -> bool:
        return self._closed or self._writer is None

    async def send_request(
        self,
        req: ZenRequest,
        *,
        timeout: float | None = None,
        retries: int = 0,
    ) -> ZenResponse:
        # TCP is reliable — default retries=0 (no retransmit / duplicate commands).
        if self._closed:
            raise RuntimeError("Client is closed")
        if self._writer is None:
            raise RuntimeError("Transport is none?!")

        if timeout is None:
            timeout = ClientConst.DEFAULT_TIMEOUT
        timeout = max(ClientConst.MIN_TIMEOUT, min(timeout, ClientConst.MAX_TIMEOUT))
        if retries < 0:
            retries = 0

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ZenResponse]

        # Hold the lock only for seq allocation + pending registration (not RTT)
        async with self._lock:
            if self._is_disconnected():
                return ZenResponse(ZenResponseType.TIMEOUT, request=req)
            fut = loop.create_future()
            req.seq = self._alloc_seq()
            if req.seq in self._pending:
                raise RuntimeError(
                    f"sequence {req.seq} already pending, which shouldn't be possible because we just allocated it"
                )
            self._pending[req.seq] = (fut, req)
            wire = req.to_bytes(checksum=self._checksum)

        try:
            for i in range(retries + 1):
                if self._is_disconnected():
                    return ZenResponse(ZenResponseType.TIMEOUT, request=req)
                try:
                    req.timestamp = time.time()
                    assert self._writer is not None
                    self._writer.write(wire)
                    await self._writer.drain()
                except Exception as e:
                    self.logger.debug(f"Send failed (attempt {i + 1}): {e}")
                    self._mark_disconnected(e)
                    return ZenResponse(ZenResponseType.TIMEOUT, request=req)
                # asyncio.wait does not cancel fut on timeout (unlike wait_for)
                done, _ = await asyncio.wait({fut}, timeout=timeout)
                if done:
                    resp = fut.result()
                    resp.request = req
                    return resp
            return ZenResponse(ZenResponseType.TIMEOUT, request=req)
        finally:
            self._pending.pop(req.seq, None)
            if not fut.done():
                fut.cancel()

    async def send_request_with_retries(
        self,
        req: ZenRequest,
        *,
        timeout: float | None = None,
        retries: int = 0,
        queue_retries: int = ClientConst.QUEUE_FAILURE_RETRIES,
    ) -> ZenResponse:
        """Like send_request, but retries on TPI QUEUE_FAILURE with backoff."""
        response: ZenResponse | None = None
        for attempt in range(queue_retries + 1):
            response = await self.send_request(req, timeout=timeout, retries=retries)
            if (
                response.response_type == ZenResponseType.ERROR
                and response.data
                and response.data[0] == ClientConst.QUEUE_FAILURE
                and attempt < queue_retries
            ):
                delay = ClientConst.QUEUE_FAILURE_BASE_DELAY * (2**attempt)
                self.logger.debug(
                    "QUEUE_FAILURE from %s:%s, retry %d/%d in %.0fms",
                    self.server[0],
                    self.server[1],
                    attempt + 1,
                    queue_retries,
                    delay * 1000,
                )
                await asyncio.sleep(delay)
                continue
            break
        assert response is not None
        self._maybe_print_traffic(response)
        return response

    def _maybe_print_traffic(self, response: ZenResponse) -> None:
        req = response.request
        if not self.print_traffic:
            return
        elif req is None or not req.raw_sent or not response.raw_rcvd:
            return
        elif response.response_type is ZenResponseType.TIMEOUT:
            wait_time_ms = (time.time() - req.timestamp) * 1000 if req else 0.0
            self.logger.info(
                f"REQUEST: [{' '.join(f'0x{b:02X}' for b in req.raw_sent)}]  "
                f"RESPONSE TIMEOUT after {wait_time_ms:.0f}ms"
            )
        else:
            rtt_ms = (response.timestamp - req.timestamp) * 1000
            self.logger.info(
                f"REQUEST: [{' '.join(f'0x{b:02X}' for b in req.raw_sent)}]  "
                f"RESPONSE: [{' '.join(f'0x{b:02X}' for b in response.raw_rcvd)}]  "
                f"RTT: {rtt_ms:.0f}ms",
            )

    def _receive_response(self, datagram: bytes, addr: tuple[str, int]) -> None:

        # Too short to be a valid packet
        if len(datagram) < 4:
            return

        # Extract values
        response_type_byte = datagram[0]
        sequence_byte = datagram[1]
        data_length_byte = datagram[2]
        data_bytes = datagram[3:-1]  # may be empty
        checksum_byte = datagram[-1]

        def _fail_pending(reason: str) -> None:
            pending = self._pending.get(sequence_byte)
            if not pending:
                self.logger.debug(
                    "Dropping invalid response (%s) seq=%s from %s",
                    reason,
                    sequence_byte,
                    addr,
                )
                return
            future, request = pending
            if not future.done():
                future.set_result(
                    ZenResponse(
                        ZenResponseType.INVALID,
                        seq=sequence_byte,
                        raw_rcvd=datagram,
                        request=request,
                        addr=addr,
                    )
                )

        # Packet length mismatch
        if len(datagram) != data_length_byte + 3 + 1:  # data_len + 3 header + 1 checksum
            _fail_pending("length")
            return

        # Checksum mismatch
        if checksum_byte != self._checksum(datagram[:-1]):
            _fail_pending("checksum")
            return

        # Unknown response type
        if response_type_byte not in (
            ZenResponseType.OK,
            ZenResponseType.ANSWER,
            ZenResponseType.NO_ANSWER,
            ZenResponseType.ERROR,
        ):
            _fail_pending("type")
            return

        # Valid response
        response = ZenResponse(
            ZenResponseType(response_type_byte),
            seq=sequence_byte,
            data=data_bytes,
            raw_rcvd=datagram,
            addr=addr,
        )

        # Find the pending request
        if response.seq is None:
            return
        pending = self._pending.get(response.seq)
        if not pending:
            return
        future, request = pending

        # The future has come
        if not future.done():
            response.request = request
            future.set_result(response)

    def _alloc_seq(self) -> int:
        """Allocate a unique sequence number"""
        # Retry up to 256 times to find a free sequence number
        for _ in range(256):
            # By default, try the next sequence number
            proposed_seq = self._next_seq
            # Increment the sequence number
            self._next_seq = (self._next_seq + 1) & 0xFF
            # If the sequence number is not in use, return it
            if proposed_seq not in self._pending:
                return proposed_seq
        raise RuntimeError("All 256 sequence numbers are in use, which is highly improbable")

    def _checksum(self, buf: bytes) -> int:
        acc = 0x00
        for byte in buf:
            acc ^= byte
        return acc

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()

    def is_connected(self) -> bool:
        """Check if client has a usable TCP transport."""
        return (
            not self._closed
            and self._writer is not None
            and not self._writer.is_closing()
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def close(self) -> None:
        """Close the client"""
        async with self._lock:
            if self._closed and self._writer is None:
                return
            self._closed = True
            for future, _request in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("ZenTcpClient closed"))
            self._pending.clear()
            writer = self._writer
            self._writer = None
            self._reader = None
            reader_task = self._reader_task
            self._reader_task = None
        if reader_task is not None and not reader_task.done():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
