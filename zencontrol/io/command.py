"""
ZenControl wire-level command client.

This module implements the command/request side of ZenControl TPI Advanced using asyncio.
It contains the ZenClient class for sending requests and receiving responses.

Terms:
- Request = A UDP packet sent by the Client to the controller
- Response = A response to a Request  
- Client = A class which sends Requests and receives Responses

Example usage:
async def main():
    client = await ZenClient.create(("192.0.2.10", 9000))
    async with client:
        req = Request(command=0x10, data=[0x01, 0xAA, 0x00, 0x00])
        resp = await client.send_request(req)
        if resp.resp_type == ResponseType.ANSWER:
            print("Answer:", resp.data)
        elif resp.resp_type == ResponseType.TIMEOUT:
            print("Timed out after 3 attempts")
        else:
            print("Resp:", resp.resp_type.name, resp.data)

asyncio.run(main())
"""

import asyncio
import socket
import struct
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Optional, Self, Tuple

# Constants
class ClientConst:
    """Constants for the ZenClient"""
    MAGIC = 0x04
    DEFAULT_TIMEOUT = 1.5
    MIN_TIMEOUT = 0.01
    MAX_TIMEOUT = 10.0

class RequestType(IntEnum):
    """Types of requests that can be sent"""
    BASIC = 0x01
    DYNAMIC = 0x02
    DALI_COLOUR = 0x03
    COMMAND = 0x04

@dataclass
class Request:
    """Represents a request to be sent to the controller"""
    command: int
    data: bytes | list[int]
    request_type: RequestType = RequestType.BASIC
    seq: Optional[int] = None
    raw_sent: Optional[bytes] = None
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        self.timestamp = time.time()
        # If data is a list, convert it to a bytes object
        if isinstance(self.data, list):
            self.data = bytes([d & 0xFF for d in self.data])
        # Length of data
        n = len(self.data)
        # Validate request type
        match self.request_type:
            case RequestType.BASIC:
                # Pad data to 4 bytes if it's less than 4 bytes
                self.data = self.data + bytes([0x00] * (4 - n)) if n < 4 else self.data
                if len(self.data) != 4:
                    raise ValueError("Request.data must be exactly 4 bytes when request type is BASIC")
            case RequestType.DALI_COLOUR:
                # Pad data to 8 bytes if it's less than 8 bytes
                self.data = self.data + bytes([0x00] * (8 - n)) if n < 8 else self.data
                if len(self.data) != 8:
                    raise ValueError("Request.data must be exactly 8 bytes when request type is DALI_COLOUR")
            case RequestType.DYNAMIC:
                # Prepend data length to data
                self.data = bytes([n]) + self.data
            case RequestType.COMMAND:
                # No padding for command type
                pass

    def to_bytes(self, checksum: callable) -> bytes:
        """Convert request to wire format"""
        req = bytes([ClientConst.MAGIC, self.seq & 0xFF, self.command & 0xFF]) + self.data
        cs = bytes([checksum(req) & 0xFF])
        self.raw_sent = req + cs
        return req + cs

class ResponseType(IntEnum):
    """Types of responses from the controller"""
    OK = 0xA0
    ANSWER = 0xA1
    NO_ANSWER = 0xA2
    ERROR = 0xA3
    TIMEOUT = 0xAE
    INVALID = 0xAF

@dataclass()
class Response:
    response_type: ResponseType
    seq: Optional[int] = None
    data: Optional[bytes] = None # empty for TIMEOUT and INVALID
    raw_rcvd: Optional[bytes] = None
    request: Optional[Request] = None
    addr: Optional[Tuple[str, int]] = None
    timestamp: float = field(default_factory=time.time)

# Protocol classes
class ZenRequestProtocol(asyncio.DatagramProtocol):
    def __init__(self, response_handler, logger: Optional[logging.Logger] = None):
        self.response_handler = response_handler
        self.logger = logger or logging.getLogger(__name__)
        self.transport: Optional[asyncio.transports.DatagramTransport] = None
        
    def connection_made(self, transport):
        self.transport = transport
        
    def datagram_received(self, data, addr):
        asyncio.create_task(self.response_handler(data, addr))
        
    def error_received(self, exc):
        self.logger.error(f"Request protocol error: {exc}")
        
    def connection_lost(self, exc):
        if exc:
            self.logger.error(f"Request connection lost: {exc}")
        else:
            self.logger.info("Request connection closed")

class ZenClient:
    """
    Request:  [0x04, seq, command, address, data(3|7), checksum]
    Response: [response_type, seq, data_len, data..., checksum]
      - checksum = XOR of all preceding bytes
      - seq is 1 byte (0..255), auto-incremented & reused for retries
      - On any non-catastrophic parse problem, deliver ResponseType.INVALID instead of raising.
    """

    def __init__(self, server: Tuple[str, int], logger: Optional[logging.Logger] = None):
        self.server = server
        self.logger = logger or logging.getLogger(__name__)
        self._transport: Optional[asyncio.transports.DatagramTransport] = None
        self._pending: Dict[int, Tuple[asyncio.Future, Request]] = {}
        self._next_seq: int = 0
        self._closed = False
        self._stop_event = asyncio.Event()
    
    @classmethod
    async def create(cls, server: Tuple[str, int], logger: Optional[logging.Logger] = None) -> Self:
        self = cls(server, logger)
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: ZenRequestProtocol(self._receive_response, self.logger),
            remote_addr=server,  # Use connected UDP to maintain connection
        )
        self._transport = transport
        self.logger.info(f"Connected to Zen server at {server[0]}:{server[1]}")
        return self

    async def send_request(self, req: Request, *, timeout: Optional[float] = None, retries: int = 0) -> Response:
        if self._closed: raise RuntimeError("Client is closed")
        if self._transport is None: raise RuntimeError("Transport is none?!")

        if timeout is None: timeout = ClientConst.DEFAULT_TIMEOUT
        timeout = max(ClientConst.MIN_TIMEOUT, min(timeout, ClientConst.MAX_TIMEOUT))
        if retries < 0: retries = 0

        # Create a future to await the response
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        # Allocate a sequence number
        req.seq = self._alloc_seq()

        # Add the future and request to the pending requests
        if req.seq in self._pending: raise RuntimeError(f"sequence {req.seq} already pending, which shouldn't be possible because we just allocated it")
        self._pending[req.seq] = (fut, req)
        
        # Send the request and wait for the future to complete
        try:
            wire = req.to_bytes(checksum=self._checksum)
            for i in range(retries + 1):
                try:
                    req.timestamp = time.time() # Update timestamp when sending the request
                    self._transport.sendto(wire) # Connected socket doesn't need address
                except Exception:
                    pass
                try:
                    resp: Response = await asyncio.wait_for(fut, timeout=timeout)
                    resp.request = req
                    return resp
                except asyncio.TimeoutError:
                    if i == retries:
                        break
                    continue
            # Retries exhausted
            return Response(ResponseType.TIMEOUT, request=req)
        finally:
            # Delete the future and request from the pending requests
            self._pending.pop(req.seq, None)
            if not fut.done():
                fut.cancel()
    
    async def _receive_response(self, datagram: bytes, addr: Tuple[str, int]):
        
        # Too short to be a valid packet
        if len(datagram) < 4:
            return Response(ResponseType.INVALID, raw_rcvd=datagram, addr=addr)

        # Extract values
        response_type_byte = datagram[0]
        sequence_byte = datagram[1]
        data_length_byte = datagram[2]
        data_bytes = datagram[3:-1] # may be empty
        checksum_byte = datagram[-1]
        
        # Packet length mismatch
        if len(datagram) != data_length_byte + 3 + 1: # data_len + 3 header + 1 checksum
            return Response(ResponseType.INVALID, seq=sequence_byte, raw_rcvd=datagram, addr=addr)
        
        # Checksum mismatch
        if checksum_byte != self._checksum(datagram[:-1]):
            return Response(ResponseType.INVALID, seq=sequence_byte, raw_rcvd=datagram, addr=addr)

        # Unknown response type
        if response_type_byte not in (ResponseType.OK, ResponseType.ANSWER, ResponseType.NO_ANSWER, ResponseType.ERROR):
            return Response(ResponseType.INVALID, seq=sequence_byte, raw_rcvd=datagram, addr=addr)

        # Valid response
        response = Response(ResponseType(response_type_byte), seq=sequence_byte, data=data_bytes, raw_rcvd=datagram, addr=addr)
        
        # Find the pending request
        future, request = self._pending.get(response.seq)

        # The future has come
        if future and request:
            response.request = request
            if not future.done():
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

    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def is_connected(self) -> bool:
        """Check if client is connected"""
        return self._transport is not None and not self._closed

    async def close(self):
        """Close the client"""
        if self._transport:
            self._transport.close()
            self._closed = True