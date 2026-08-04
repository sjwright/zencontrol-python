"""
Wire-level command client for ZenControl TPI Advanced
=====================================================

This module takes care of framing, checksums, sequence numbers, matching replies
to waiters, and retries, but has no knowledge of TPI commands or DALI semantics.

The host creates one long-lived "ZenClient" per controller. It represents one connected
UDP socket talking to one host:port.

You send commands by constructing a "ZenRequest" (opcode + data + "ZenRequestType")
and calling send_request or send_request_with_retries.

You await a "ZenResponse" and interpret the result.

Lost packets are retried by re-sending.

Queue-full errors are retried with a backoff.

Bad packets and transport death surface as "TIMEOUT" / "INVALID" responses rather than
raising exceptions, so callers can use one recovery path.

-----------------------------------------------------
Basic example:

    client = await ZenClient.create(("192.0.2.10", 5108))
    async with client:
        req = ZenRequest(command=0x10, data=[0x01, 0xAA, 0x00, 0x00])
        resp = await client.send_request(req)
        if resp.response_type == ZenResponseType.ANSWER:
            print("Answer:", resp.data)

-----------------------------------------------------
"""
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Self, cast


# Constants
class ClientConst:
    """Constants for the ZenClient"""
    MAGIC = 0x04
    DEFAULT_TIMEOUT = 1.5
    MIN_TIMEOUT = 0.01
    MAX_TIMEOUT = 10.0
    # UDP datagram retries (lost packets / brief network blips); separate from QUEUE_FAILURE
    DEFAULT_RETRIES = 1
    # TPI ERROR payload: controller DALI command queue briefly full
    QUEUE_FAILURE = 0xB3
    QUEUE_FAILURE_RETRIES = 3
    QUEUE_FAILURE_BASE_DELAY = 0.05  # doubles each attempt: 50/100/200ms

class ZenRequestType(IntEnum):
    """Types of requests that can be sent"""
    BASIC = 0x01
    DYNAMIC = 0x02
    DALI_COLOUR = 0x03
    COMMAND = 0x04

@dataclass(slots=True)
class ZenRequest:
    """Represents a request to be sent to the controller"""
    command: int
    data: bytes | list[int]
    request_type: ZenRequestType = ZenRequestType.BASIC
    seq: int | None = None
    raw_sent: bytes | None = None
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.timestamp = time.time()
        # If data is a list, convert it to a bytes object
        if isinstance(self.data, list):
            self.data = bytes([d & 0xFF for d in self.data])
        # Length of data
        n = len(self.data)
        # Validate request type
        match self.request_type:
            case ZenRequestType.BASIC:
                # Pad data to 4 bytes if it's less than 4 bytes
                self.data = self.data + bytes([0x00] * (4 - n)) if n < 4 else self.data
                if len(self.data) != 4:
                    raise ValueError("ZenRequest.data must be exactly 4 bytes when request type is BASIC")
            case ZenRequestType.DALI_COLOUR:
                pass
            case ZenRequestType.DYNAMIC:
                # Prepend data length to data
                self.data = bytes([n]) + self.data
            case ZenRequestType.COMMAND:
                # No padding for command type
                pass

    def to_bytes(self, checksum: Callable[[bytes], int]) -> bytes:
        """Convert request to wire format"""
        if self.seq is None:
            raise ValueError("ZenRequest.seq must be set before calling to_bytes")
        data = self.data if isinstance(self.data, bytes) else bytes([d & 0xFF for d in self.data])
        req = bytes([ClientConst.MAGIC, self.seq & 0xFF, self.command & 0xFF]) + data
        cs = bytes([checksum(req) & 0xFF])
        self.raw_sent = req + cs
        return req + cs

class ZenResponseType(IntEnum):
    """Types of responses from the controller"""
    OK = 0xA0
    ANSWER = 0xA1
    NO_ANSWER = 0xA2
    ERROR = 0xA3
    TIMEOUT = 0xAE
    INVALID = 0xAF

@dataclass(slots=True)
class ZenResponse:
    response_type: ZenResponseType
    seq: int | None = None
    data: bytes | None = None # empty for TIMEOUT and INVALID
    raw_rcvd: bytes | None = None
    request: ZenRequest | None = None
    addr: tuple[str, int] | None = None
    timestamp: float = field(default_factory=time.time)

# Protocol classes
class ZenRequestProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        response_handler: Callable[[bytes, tuple[str, int]], None],
        logger: logging.Logger | None = None,
        on_transport_lost: Callable[[Exception | None], None] | None = None,
    ) -> None:
        self.response_handler = response_handler
        self.logger = logger or logging.getLogger(__name__)
        self.on_transport_lost = on_transport_lost
        self.transport: asyncio.transports.DatagramTransport | None = None
        
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.DatagramTransport, transport)
        
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self.response_handler(data, addr)
        except Exception as exc:
            self.logger.error(f"Response handler failed: {exc}", exc_info=exc)
        
    def error_received(self, exc: Exception) -> None:
        self.logger.error(f"Request protocol error: {exc}")
        if self.on_transport_lost:
            self.on_transport_lost(exc)
        
    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            self.logger.error(f"Request connection lost: {exc}")
        else:
            self.logger.info("Request connection closed")
        if self.on_transport_lost:
            self.on_transport_lost(exc)

class ZenClient:
    """
    ZenRequest:  [0x04, seq, command, address, data(3|7), checksum]
    ZenResponse: [response_type, seq, data_len, data..., checksum]
      - checksum = XOR of all preceding bytes
      - seq is 1 byte (0..255), auto-incremented & reused for retries
      - On any non-catastrophic parse problem, deliver ZenResponseType.INVALID instead of raising.
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
        self._transport: asyncio.transports.DatagramTransport | None = None
        self._protocol: ZenRequestProtocol | None = None
        self._pending: dict[int, tuple[asyncio.Future[ZenResponse], ZenRequest]] = {}
        self._next_seq: int = 0
        self._closed = False
        self._stop_event = asyncio.Event()
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
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: ZenRequestProtocol(
                self._receive_response,
                self.logger,
                on_transport_lost=self._mark_disconnected,
            ),
            remote_addr=server,  # Use connected UDP to maintain connection
        )
        self._transport = transport
        self._protocol = protocol
        self.logger.info(f"Connected to Zen server at {server[0]}:{server[1]}")
        return self

    def _mark_disconnected(self, exc: Exception | None = None) -> None:
        """Mark the client dead after transport loss (must not await or take _lock)."""
        if self._closed:
            return
        self._closed = True
        transport = self._transport
        self._transport = None
        # Unblock waiters with TIMEOUT so callers use the normal recovery path
        for fut, req in list(self._pending.values()):
            if not fut.done():
                fut.set_result(ZenResponse(ZenResponseType.TIMEOUT, request=req))
        self._pending.clear()
        if transport is not None and not transport.is_closing():
            transport.close()
        if exc:
            self.logger.debug("ZenClient marked disconnected: %s", exc)

    def _is_disconnected(self) -> bool:
        return self._closed or self._transport is None

    async def send_request(
        self,
        req: ZenRequest,
        *,
        timeout: float | None = None,
        retries: int = ClientConst.DEFAULT_RETRIES,
    ) -> ZenResponse:
        if self._closed:
            raise RuntimeError("Client is closed")
        if self._transport is None:
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
                    self._transport.sendto(wire)
                except Exception as e:
                    self.logger.debug(f"Send failed (attempt {i + 1}): {e}")
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
        retries: int = ClientConst.DEFAULT_RETRIES,
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
        data_bytes = datagram[3:-1] # may be empty
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
        if len(datagram) != data_length_byte + 3 + 1: # data_len + 3 header + 1 checksum
            _fail_pending("length")
            return
        
        # Checksum mismatch
        if checksum_byte != self._checksum(datagram[:-1]):
            _fail_pending("checksum")
            return

        # Unknown response type
        if response_type_byte not in (ZenResponseType.OK, ZenResponseType.ANSWER, ZenResponseType.NO_ANSWER, ZenResponseType.ERROR):
            _fail_pending("type")
            return

        # Valid response
        response = ZenResponse(ZenResponseType(response_type_byte), seq=sequence_byte, data=data_bytes, raw_rcvd=datagram, addr=addr)
        
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
        """Check if client has a usable datagram transport."""
        return not self._closed and self._transport is not None and not self._transport.is_closing()

    async def close(self) -> None:
        """Close the client"""
        async with self._lock:
            if self._closed and self._transport is None:
                return
            self._closed = True
            for future, _request in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("ZenClient closed"))
            self._pending.clear()
            transport = self._transport
            self._transport = None
        if transport is not None and not transport.is_closing():
            transport.close()