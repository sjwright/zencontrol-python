"""
Wire-level event endpoint for ZenControl TPI Advanced
=====================================================

This module takes care of maintaining multicast membership, binding UDP sockets,
and validating event envelopes (magic, length, checksum, positional fields),
but has no knowledge of event codes, TPI semantics, or DALI state.

Because events can be multicast, listeners must be shared across controllers.
The host typically creates one "ZenEndpoint" for the whole process, and will
need to handle routing of events.

In this library, routing is handled by "ZenEventReceiver" in the "api" module.

Malformed datagrams are dropped here (debug-logged).

-----------------------------------------------------
Basic example:

    def on_event(event: ZenEvent) -> None:
        print(event.mac.hex(":"), event.code, event.target, event.payload.hex())

    endpoint = await ZenEndpoint.open(unicast=False, sink=on_event)
    try:
        await asyncio.Future()  # run until cancelled
    finally:
        await endpoint.close()

-----------------------------------------------------
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

# Sync sink: endpoint never awaits; the funnel owner enqueues.
EventSink = Callable[["ZenEvent"], None]

_MAGIC = bytes([0x5A, 0x43])
_MIN_FRAME_LEN = 13  # magic(2)+mac(6)+target(2)+code(1)+len(1)+checksum(1)


@dataclass(frozen=True, slots=True)
class ZenEvent:
    """Validated frame: envelope fields plus an opaque payload."""

    mac: bytes
    target: int
    code: int  # opaque wire byte; vocabulary lives in api.event_decode
    payload: bytes
    host: str
    received_at: float


def _checksum(buf: bytes) -> int:
    acc = 0
    for b in buf:
        acc ^= b
    return acc & 0xFF


def parse_frame(data: bytes, addr: tuple[str, int]) -> ZenEvent | None:
    """Validate envelope and extract fields. Returns None if malformed.

    Pure: no logging, no socket state. Used by the endpoint on receive and by
    tests / offline tools that need framing without a live socket.
    """
    if len(data) < _MIN_FRAME_LEN or data[0:2] != _MAGIC:
        return None

    mac = data[2:8]
    target = int.from_bytes(data[8:10], byteorder="big")
    code = data[10]
    payload_len = data[11]
    payload = data[12:-1]
    received_checksum = data[-1]

    if received_checksum != _checksum(data[:-1]):
        return None
    if len(payload) != payload_len:
        return None

    return ZenEvent(
        mac=mac,
        target=target,
        code=code,
        payload=bytes(payload),
        host=addr[0],
        received_at=time.time(),
    )


class EventConst:
    """Wire constants for the event plane."""
    MULTICAST_GROUP = "239.255.90.67"
    MULTICAST_PORT = 6969


def accept_datagram(
    data: bytes,
    addr: tuple[str, int],
    sink: EventSink,
    logger: logging.Logger | None = None,
) -> bool:
    """Parse a datagram like the live protocol and invoke ``sink`` if valid.

    Returns True when the sink was called. Shared by ``ZenEventProtocol`` and
    tests that simulate the endpoint handoff without a socket.
    """
    log = logger or logging.getLogger(__name__)
    event = parse_frame(data, addr)
    if event is None:
        log.debug(
            "Invalid event packet from %s: %s",
            addr,
            ", ".join(f"0x{b:02x}" for b in data),
        )
        return False
    try:
        sink(event)
    except Exception as exc:
        log.error(f"Event sink failed: {exc}", exc_info=exc)
        return False
    return True


class ZenEventProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        sink: EventSink,
        logger: logging.Logger | None = None,
    ) -> None:
        self.sink = sink
        self.logger = logger or logging.getLogger(__name__)
        self.transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        accept_datagram(data, addr, self.sink, self.logger)

    def error_received(self, exc: Exception) -> None:
        self.logger.error(f"Event protocol error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            self.logger.error(f"Event connection lost: {exc}")
        else:
            self.logger.info("Event connection closed")


def _reuse_port_supported() -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.close()
        return True
    except (AttributeError, OSError):
        return False


class ZenEndpoint:
    """One bound UDP socket. Parses envelopes and pushes ZenEvents to a sink."""

    def __init__(
        self,
        *,
        unicast: bool,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 0,
        sink: EventSink,
        logger: logging.Logger | None = None,
    ) -> None:
        self.unicast = unicast
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.sink = sink
        self.logger = logger or logging.getLogger(__name__)
        self.transport: asyncio.DatagramTransport | None = None
        self.protocol: ZenEventProtocol | None = None
        self._mreq: bytes | None = None

    @classmethod
    async def open(
        cls,
        *,
        unicast: bool,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 0,
        sink: EventSink,
        logger: logging.Logger | None = None,
    ) -> ZenEndpoint:
        endpoint = cls(
            unicast=unicast,
            listen_ip=listen_ip,
            listen_port=listen_port,
            sink=sink,
            logger=logger,
        )
        await endpoint._bind()
        kind = "unicast" if unicast else "multicast"
        endpoint.logger.info("Opened %s event endpoint on %s:%s", kind, endpoint.listen_ip, endpoint.listen_port)
        return endpoint

    @property
    def bound_port(self) -> int:
        return self.listen_port

    def is_open(self) -> bool:
        return self.transport is not None and not self.transport.is_closing()

    async def close(self) -> None:
        """Drop multicast membership (if any) then close. Idempotent."""
        if self.transport is None or self.transport.is_closing():
            self.transport = None
            self.protocol = None
            self._mreq = None
            return

        self._drop_multicast_membership()
        self.transport.close()
        self.transport = None
        self.protocol = None
        self._mreq = None
        self.logger.info("Closed event endpoint")

    def _drop_multicast_membership(self) -> None:
        if self.unicast or self._mreq is None or self.transport is None:
            return
        try:
            sock = self.transport.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, self._mreq)
        except OSError as err:
            self.logger.debug(f"Error dropping multicast membership: {err}")

    def _create_multicast_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass

        bind_ip = self.listen_ip or "0.0.0.0"
        sock.bind((bind_ip, EventConst.MULTICAST_PORT))

        group = socket.inet_aton(EventConst.MULTICAST_GROUP)
        self._mreq = struct.pack("=4sI", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self._mreq)
        self.listen_port = EventConst.MULTICAST_PORT
        return sock

    async def _bind(self) -> None:
        loop = asyncio.get_running_loop()

        if self.unicast:
            reuse_port = _reuse_port_supported()
            self.transport, _ = await loop.create_datagram_endpoint(
                lambda: ZenEventProtocol(self.sink, self.logger),
                local_addr=(self.listen_ip, self.listen_port),
                reuse_port=reuse_port,
            )
            sockname = self.transport.get_extra_info("sockname")
            if sockname:
                self.listen_port = sockname[1]
            return

        sock = self._create_multicast_socket()
        try:
            self.transport, _ = await loop.create_datagram_endpoint(
                lambda: ZenEventProtocol(self.sink, self.logger),
                sock=sock,
            )
        except Exception:
            try:
                if self._mreq is not None:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, self._mreq)
            except OSError:
                pass
            sock.close()
            self._mreq = None
            raise
