"""
ZenControl wire-level event listener.

This module implements the event/listener side of ZenControl TPI Advanced using asyncio.
It contains the ZenListener class for receiving multicast or unicast event packets.

Terms:
- Event = A multicast or unicast packet sent by a controller
- Listener = A class which receives Events

Example usage:
async def listen_for_events():
    listener = await ZenListener.create(unicast=False)  # Multicast mode
    async with listener:
        async for event in listener.events():
            print(f"Event: {event.event_code}, Target: {event.target}, Payload: {event.payload.hex()}")

asyncio.run(listen_for_events())
"""

import asyncio
import logging
import socket
import struct
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Self


# Event classes
@dataclass(slots=True)
class ZenEvent:
    """Represents a Zen TPI event"""
    raw_data: bytes
    event_code: int
    target: int
    payload: bytes
    mac_address: bytes
    ip_address: str
    ip_port: int
    timestamp: float = field(default_factory=time.time)

# Constants
class EventConst:
    """Constants for event handling"""
    MULTICAST_GROUP = "239.255.90.67"
    MULTICAST_PORT = 6969
    DEFAULT_MAX_QUEUE_SIZE = 1000
    DROP_LOG_INTERVAL = 5.0  # seconds between queue-full warnings

class ZenEventProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        event_handler: Callable[[bytes, tuple[str, int]], Awaitable[None]],
        logger: logging.Logger | None = None,
    ) -> None:
        self.event_handler = event_handler
        self.logger = logger or logging.getLogger(__name__)
        self.transport: asyncio.BaseTransport | None = None
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._run_handler(self.event_handler(data, addr))

    def _run_handler(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        task.add_done_callback(self._handler_done)

    def _handler_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            self.logger.error(f"Event handler failed: {exc}", exc_info=exc)
    def error_received(self, exc: Exception) -> None:
        self.logger.error(f"Event protocol error: {exc}")
    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            self.logger.error(f"Event connection lost: {exc}")
        else:
            self.logger.info("Event connection closed")

class ZenListener:
    def __init__(
        self,
        unicast: bool = False,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 0,
        logger: logging.Logger | None = None,
        max_queue_size: int = EventConst.DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        self.unicast = unicast
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.logger = logger or logging.getLogger(__name__)
        self.max_queue_size = max(1, max_queue_size)

        # Modern asyncio components
        self.transport: asyncio.DatagramTransport | None = None
        self.protocol: ZenEventProtocol | None = None
        self._stop_event = asyncio.Event()
        # Exact IP_ADD_MEMBERSHIP bytes so DROP can leave the same group on unload/reload
        self._mreq: bytes | None = None

        # Bounded queue: drop-oldest under backpressure so the UDP path never stalls
        self._event_queue: asyncio.Queue[ZenEvent] = asyncio.Queue(maxsize=self.max_queue_size)
        self.dropped_events = 0
        self._last_drop_log = 0.0

    @classmethod
    async def create(
        cls,
        unicast: bool = False,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 0,
        logger: logging.Logger | None = None,
        max_queue_size: int = EventConst.DEFAULT_MAX_QUEUE_SIZE,
    ) -> ZenListener:
        """Create and start a ZenListener instance"""
        self = cls(unicast, listen_ip, listen_port, logger, max_queue_size=max_queue_size)
        await self._create_datagram_endpoint()
        self.logger.info(f"Started event listener in {'unicast' if self.unicast else 'multicast'} mode")
        return self

    async def start_listening(self) -> None:
        if self.transport and not self.transport.is_closing():
            self.logger.warning("Event listener already running")
            return
        
        self._stop_event.clear()
        await self._create_datagram_endpoint()
        self.logger.info(f"Started event listener in {'unicast' if self.unicast else 'multicast'} mode")

    async def stop_listening(self) -> None:
        if self.transport and not self.transport.is_closing():
            self._stop_event.set()
            self._drop_multicast_membership()
            self.transport.close()
            self.transport = None
            self.protocol = None
            self._mreq = None

            # Clear any remaining events in queue
            while not self._event_queue.empty():
                try:
                    self._event_queue.get_nowait()
                    self._event_queue.task_done()
                except asyncio.QueueEmpty:
                    break

        self.logger.info("Stopped event listener")

    async def close(self) -> None:
        """Close the listener (alias for stop_listening for async context manager compatibility)"""
        await self.stop_listening()

    def is_listening(self) -> bool:
        """Check if the listener is active and ready"""
        return self.transport is not None and not self.transport.is_closing()

    def _drop_multicast_membership(self) -> None:
        """Leave the IGMP group before close to reduce reload bind races."""
        if self.unicast or self._mreq is None or self.transport is None:
            return
        try:
            sock = self.transport.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, self._mreq)
        except OSError as err:
            self.logger.debug(f"Error dropping multicast membership: {err}")

    def _create_multicast_socket(self) -> socket.socket:
        """Bind a reusable UDP socket and join the ZenControl event group."""
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
        return sock

    async def _create_datagram_endpoint(self) -> None:
        loop = asyncio.get_running_loop()

        if self.unicast:
            # Unicast mode
            self.transport, _ = await loop.create_datagram_endpoint(
                lambda: ZenEventProtocol(self._receive_event, self.logger),
                local_addr=(self.listen_ip, self.listen_port),
                reuse_port=True
            )
            sockname = self.transport.get_extra_info('sockname')
            if sockname:
                self.listen_port = sockname[1]
            self.logger.info(f"Listening for unicast events on {self.listen_ip}:{self.listen_port}")
        else:
            # Multicast: reuse + join before asyncio owns the socket
            sock = self._create_multicast_socket()
            try:
                self.transport, _ = await loop.create_datagram_endpoint(
                    lambda: ZenEventProtocol(self._receive_event, self.logger),
                    sock=sock,
                )
            except Exception as e:
                self.logger.critical(f"Failed to create multicast endpoint: {e}")
                try:
                    if self._mreq is not None:
                        sock.setsockopt(
                            socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, self._mreq
                        )
                except OSError:
                    pass
                sock.close()
                self._mreq = None
                raise
            self.logger.info(
                f"Listening for multicast events on "
                f"{EventConst.MULTICAST_GROUP}:{EventConst.MULTICAST_PORT}"
            )

    async def _receive_event(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process received event data"""
        typecast = "unicast" if self.unicast else "multicast"
        
        # Drop packet if it doesn't match the expected structure
        if len(data) < 13 or data[0:2] != bytes([0x5a, 0x43]):
            self.logger.debug(f"Received {typecast} invalid packet: {addr} - {', '.join(f'0x{b:02x}' for b in data)}")
            return

        # Extract values
        mac_address = data[2:8]
        target = int.from_bytes(data[8:10], byteorder='big')
        event_code = data[10]
        payload_len = data[11]
        payload = data[12:-1]
        received_checksum = data[-1]

        # Verify checksum
        calculated_checksum = self._checksum(data[:-1])
        if received_checksum != calculated_checksum:
            self.logger.error(f"{typecast.capitalize()} packet has invalid checksum: {calculated_checksum} != {received_checksum}")
            return
        
        # Verify data length
        if len(payload) != payload_len:
            self.logger.error(f"{typecast.capitalize()} packet has invalid payload length: {len(payload)} != {payload_len}")
            return
        
        # Create event object and put in queue
        event = ZenEvent(
            raw_data=data,
            mac_address=mac_address,
            target=target,
            event_code=event_code,
            payload=payload,
            ip_address=addr[0],
            ip_port=addr[1],
            timestamp=time.time()
        )

        if event_code != 7 and event_code != 8: # 7=system varaiable, 8=colour changed
            self.logger.debug(f"Received {typecast} from {addr[0]}:{addr[1]}: target {target} event {event_code} payload [{', '.join(f'0x{b:02x}' for b in payload)}]")

        self._enqueue_event(event)

    def _enqueue_event(self, event: ZenEvent) -> None:
        """Enqueue event; if full, drop the oldest and keep the newest."""
        try:
            self._event_queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        try:
            self._event_queue.get_nowait()
            self._event_queue.task_done()
        except asyncio.QueueEmpty:
            pass

        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

        self.dropped_events += 1
        now = time.time()
        if now - self._last_drop_log >= EventConst.DROP_LOG_INTERVAL:
            self._last_drop_log = now
            self.logger.warning(
                "Event queue full (max=%d); dropped %d event(s) total",
                self.max_queue_size,
                self.dropped_events,
            )

    async def events(self, timeout: float | None = None) -> AsyncGenerator[ZenEvent]:
        """Async generator yielding events as they arrive"""
        while not self._stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), 
                    timeout=timeout
                )
                yield event
                self._event_queue.task_done()
            except TimeoutError:
                if timeout is not None:
                    break
                continue
    
    async def get_event(self, timeout: float | None = None) -> ZenEvent | None:
        """Get next event from queue"""
        try:
            event = await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
            self._event_queue.task_done()
            return event
        except TimeoutError:
            return None
    
    async def get_events(self, count: int, timeout: float | None = None) -> list[ZenEvent]:
        """Get multiple events from queue"""
        events = []
        for _ in range(count):
            event = await self.get_event(timeout)
            if event is None:
                break
            events.append(event)
        return events

    async def __aenter__(self) -> Self:
        """Async context manager entry"""
        # If not already started, start listening
        if not self.transport or self.transport.is_closing():
            await self.start_listening()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit"""
        await self.stop_listening()

    def _checksum(self, buf: bytes) -> int:
        acc = 0
        for b in buf:
            acc ^= b
        return acc & 0xFF