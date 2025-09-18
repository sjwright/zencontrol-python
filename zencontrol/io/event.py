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
import socket
import struct
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, AsyncGenerator

# Event classes
@dataclass
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

    def __post_init__(self):
        self.timestamp = time.time()

# Constants
class EventConst:
    """Constants for event handling"""
    MULTICAST_GROUP = "239.255.90.67"
    MULTICAST_PORT = 6969

class ZenEventProtocol(asyncio.DatagramProtocol):
    def __init__(self, event_handler, logger: Optional[logging.Logger] = None):
        self.event_handler = event_handler
        self.logger = logger or logging.getLogger(__name__)
        self.transport = None
    def connection_made(self, transport):
        self.transport = transport
    def datagram_received(self, data, addr):
        asyncio.create_task(self.event_handler(data, addr))
    def error_received(self, exc):
        self.logger.error(f"Event protocol error: {exc}")
    def connection_lost(self, exc):
        if exc:
            self.logger.error(f"Event connection lost: {exc}")
        else:
            self.logger.info("Event connection closed")

class ZenListener:
    def __init__(self, 
                 unicast: bool = False,
                 listen_ip: str = "0.0.0.0",
                 listen_port: int = 0,
                 logger: Optional[logging.Logger] = None):
        self.unicast = unicast
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.logger = logger or logging.getLogger(__name__)
        
        # Modern asyncio components
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional[ZenEventProtocol] = None
        self._stop_event = asyncio.Event()
        
        # Event queue for async generator pattern
        self._event_queue: asyncio.Queue = asyncio.Queue()

    @classmethod
    async def create(
        cls,
        unicast: bool = False,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 0,
        logger: Optional[logging.Logger] = None,
    ) -> "ZenListener":
        """Create and start a ZenListener instance"""
        self = cls(unicast, listen_ip, listen_port, logger)
        await self._create_datagram_endpoint()
        self.logger.info(f"Started event listener in {'unicast' if self.unicast else 'multicast'} mode")
        return self

    async def start_listening(self):
        if self.transport and not self.transport.is_closing():
            self.logger.warning("Event listener already running")
            return
        
        self._stop_event.clear()
        await self._create_datagram_endpoint()
        self.logger.info(f"Started event listener in {'unicast' if self.unicast else 'multicast'} mode")

    async def stop_listening(self):
        if self.transport and not self.transport.is_closing():
            self._stop_event.set()
            self.transport.close()
            self.transport = None
            self.protocol = None
            
            # Clear any remaining events in queue
            while not self._event_queue.empty():
                try:
                    self._event_queue.get_nowait()
                    self._event_queue.task_done()
                except asyncio.QueueEmpty:
                    break
        
        self.logger.info("Stopped event listener")

    async def close(self):
        """Close the listener (alias for stop_listening for async context manager compatibility)"""
        await self.stop_listening()

    def is_listening(self) -> bool:
        """Check if the listener is active and ready"""
        return self.transport is not None and not self.transport.is_closing()

    async def _create_datagram_endpoint(self):
        loop = asyncio.get_running_loop()
        
        if self.unicast:
            # Unicast mode
            self.transport, _ = await loop.create_datagram_endpoint(
                lambda: ZenEventProtocol(self._receive_event, self.logger),
                local_addr=(self.listen_ip, self.listen_port),
                reuse_port=True
            )
            self.logger.info(f"Listening for unicast events on {self.listen_ip}:{self.listen_port}")
        else:  
            # Multicast mode
            try:
                self.transport, _ = await loop.create_datagram_endpoint(
                    lambda: ZenEventProtocol(self._receive_event, self.logger),
                    local_addr=('0.0.0.0', EventConst.MULTICAST_PORT),
                    reuse_port=True
                )
                self.logger.info(f"Listening for multicast events on {EventConst.MULTICAST_GROUP}:{EventConst.MULTICAST_PORT}")

                # Join the multicast group
                sock = self.transport.get_extra_info('socket')
                if sock:
                    # Set socket options for multicast
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    group = socket.inet_aton(EventConst.MULTICAST_GROUP)
                    mreq = struct.pack('4sl', group, socket.INADDR_ANY)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except Exception as e:
                self.logger.fatal(f"Failed to create multicast endpoint: {e}")
                raise

    async def _receive_event(self, data: bytes, addr: Tuple[str, int]):
        """Process received event data"""
        typecast = "unicast" if self.unicast else "multicast"
        
        # Drop packet if it doesn't match the expected structure
        if len(data) < 2 or data[0:2] != bytes([0x5a, 0x43]):
            self.logger.debug(f"Received {typecast} invalid packet: {addr} - {', '.join(f'0x{b:02x}' for b in data)}")
            return

        # Extract values
        mac_address = data[2:8]
        # mac_bytes = bytes.fromhex(mac_address.hex())
        # mac_string = ':'.join(f'{b:02x}' for b in mac_address)
        target = int.from_bytes(data[8:10], byteorder='big')
        event_code = data[10]
        payload_len = data[11]
        payload = data[12:-1]
        received_checksum = data[-1]

        # Verify checksum
        calculated_checksum = self._checksum(list(data[:-1]))
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

        # Put event in queue for async generator
        await self._event_queue.put(event)

    async def events(self, timeout: Optional[float] = None) -> AsyncGenerator[ZenEvent, None]:
        """Async generator yielding events as they arrive"""
        while not self._stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), 
                    timeout=timeout
                )
                yield event
                self._event_queue.task_done()
            except asyncio.TimeoutError:
                if timeout is not None:
                    break
                continue
    
    async def get_event(self, timeout: Optional[float] = None) -> Optional[ZenEvent]:
        """Get next event from queue"""
        try:
            return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    async def get_events(self, count: int, timeout: Optional[float] = None) -> list[ZenEvent]:
        """Get multiple events from queue"""
        events = []
        for _ in range(count):
            event = await self.get_event(timeout)
            if event is None:
                break
            events.append(event)
        return events

    async def __aenter__(self):
        """Async context manager entry"""
        # If not already started, start listening
        if not self.transport or self.transport.is_closing():
            await self.start_listening()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.stop_listening()

    def _checksum(self, buf: bytes) -> int:
        acc = 0
        for b in buf:
            acc ^= b
        return acc & 0xFF