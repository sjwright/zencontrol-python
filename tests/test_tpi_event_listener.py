#!/usr/bin/env python3
"""
Test script for the ZenTpiEventListener

This script tests the standalone Zen TPI Event Listener with both
multicast and unicast modes, including packet validation and callback handling.
"""

import asyncio
import logging
import signal
import sys
import socket
import struct
import time
from zencontrol import ZenListener, ZenEvent, EventConst, run_with_keyboard_interrupt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EventListenerTest:
    def __init__(self):
        self.listener = None
        self.running = True
        self.events_received = []
        
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self.running = False
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def process_event(self, event: ZenEvent):
        """Process received event"""
        event_data = {
            'event_code': event.event_code,
            'target': event.target,
            'payload': event.payload,
            'mac_address': event.mac_address,
            'ip_address': event.ip_address,
            'timestamp': event.timestamp
        }
        self.events_received.append(event_data)
        
        # Event names for display
        event_names = [
            "Button Press", "Button Hold", "Absolute Input", "Level Change",
            "Group Level Change", "Scene Change", "Is Occupied", "System Variable Change",
            "Colour Change", "Profile Change", "Group Occupied"
        ]
        
        event_name = event_names[event.event_code] if event.event_code < len(event_names) else f"Unknown({event.event_code})"
        
        logger.info(f"🌈 {event_name}: Target: {event.target} - "
                   f"Payload: {event.payload.hex()} - MAC: {event.mac_address} - IP: {event.ip_address[0]}")
    
    async def test_multicast_mode(self):
        """Test the event listener in multicast mode"""
        logger.info("🧪 Testing multicast mode...")
        
        self.listener = await ZenListener.create(
            unicast=False,  # Multicast mode
            logger=logger
        )
        
        async with self.listener:
            logger.info("🎧 Event listener started in MULTICAST mode")
            logger.info(f"📡 Listening for events on {EventConst.MULTICAST_GROUP}:{EventConst.MULTICAST_PORT}")
            logger.info("⏹️  Press Ctrl+C to stop...")
            
            # Use async generator to process events
            async for event in self.listener.events():
                self.process_event(event)
                
                # Print stats every 10 events
                if len(self.events_received) % 10 == 0:
                    logger.info(f"📊 Received {len(self.events_received)} events so far...")
                
                # Check if we should stop
                if not self.running:
                    break
    
    async def test_unicast_mode(self):
        """Test the event listener in unicast mode"""
        logger.info("🧪 Testing unicast mode...")
        
        self.listener = await ZenListener.create(
            unicast=True,  # Unicast mode
            listen_ip="0.0.0.0",
            listen_port=6969,
            logger=logger
        )
        
        async with self.listener:
            logger.info("🎧 Event listener started in UNICAST mode")
            logger.info("📡 Listening for events on 0.0.0.0:6969")
            logger.info("⏹️  Press Ctrl+C to stop...")
            
            # Use async generator to process events
            async for event in self.listener.events():
                self.process_event(event)
                
                # Print stats every 10 events
                if len(self.events_received) % 10 == 0:
                    logger.info(f"📊 Received {len(self.events_received)} events so far...")
                
                # Check if we should stop
                if not self.running:
                    break

class PacketGenerator:
    """Helper class to generate test packets for validation"""
    
    @staticmethod
    def create_test_packet(mac_address: str, event_code: int, target: int, payload: bytes) -> bytes:
        """Create a valid Zen TPI event packet for testing"""
        # Convert MAC address to bytes
        mac_bytes = bytes.fromhex(mac_address.replace(':', ''))
        
        # Build packet: [0x5a, 0x43, mac(6), target(2), event_code(1), payload_len(1), payload, checksum(1)]
        packet = bytearray()
        packet.extend([0x5a, 0x43])  # Magic bytes
        packet.extend(mac_bytes)     # MAC address (6 bytes)
        packet.extend(target.to_bytes(2, byteorder='big'))  # Target (2 bytes)
        packet.append(event_code)    # Event code (1 byte)
        packet.append(len(payload))  # Payload length (1 byte)
        packet.extend(payload)       # Payload
        
        # Calculate checksum (XOR of all bytes except checksum)
        checksum = 0
        for byte in packet:
            checksum ^= byte
        packet.append(checksum & 0xFF)
        
        return bytes(packet)
    
    @staticmethod
    async def send_test_packet(host: str, port: int, packet: bytes):
        """Send a test packet to the specified host and port"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(packet, (host, port))
            sock.close()
            logger.info(f"📤 Sent test packet to {host}:{port}")
        except Exception as e:
            logger.error(f"❌ Failed to send test packet: {e}")

async def run_packet_validation_test():
    """Run a test to validate packet parsing"""
    logger.info("🧪 Running packet validation test...")
    
    events_received = []
    
    def process_test_event(event: ZenEvent):
        events_received.append({
            'event_code': event.event_code,
            'target': event.target,
            'payload': event.payload,
            'mac_address': event.mac_address,
            'ip_address': event.ip_address
        })
        logger.info(f"✅ Received test event: {event.event_code}, target: {event.target}, payload: {event.payload.hex()}")
    
    # Create listener for testing
    listener = await ZenListener.create(unicast=True, listen_port=6969, logger=logger)
    
    async with listener:
        logger.info("🎧 Test listener started on port 6969")
        
        # Wait a moment for listener to be ready
        await asyncio.sleep(0.5)
        
        # Generate and send test packets
        test_packets = [
            PacketGenerator.create_test_packet("aa:bb:cc:dd:ee:ff", 0x00, 64, b'\x01'),  # Button press
            PacketGenerator.create_test_packet("aa:bb:cc:dd:ee:ff", 0x03, 0, b'\x80'),   # Level change
            PacketGenerator.create_test_packet("aa:bb:cc:dd:ee:ff", 0x05, 0, b'\x01'),   # Scene change
        ]
        
        for i, packet in enumerate(test_packets):
            await PacketGenerator.send_test_packet("127.0.0.1", 6969, packet)
            await asyncio.sleep(0.1)  # Small delay between packets
        
        # Process events using async generator
        event_count = 0
        async for event in listener.events(timeout=2.0):
            process_test_event(event)
            event_count += 1
            if event_count >= len(test_packets):
                break
        
        # Validate results
        if len(events_received) == len(test_packets):
            logger.info(f"✅ Packet validation test PASSED: Received {len(events_received)} events")
            for i, event in enumerate(events_received):
                logger.info(f"   Event {i+1}: Code={event['event_code']}, Target={event['target']}, Payload={event['payload'].hex()}")
        else:
            logger.error(f"❌ Packet validation test FAILED: Expected {len(test_packets)}, got {len(events_received)}")

async def main():
    """Main function"""
    test = EventListenerTest()
    test.setup_signal_handlers()
    
    print("Zen TPI Event Listener Test")
    print("=" * 50)
    print("Available tests:")
    print("  1. multicast  - Test multicast mode (default)")
    print("  2. unicast    - Test unicast mode")
    print("  3. validate   - Run packet validation test")
    print("=" * 50)
    print()
    
    # Check command line arguments
    if len(sys.argv) > 1:
        test_mode = sys.argv[1].lower()
    else:
        test_mode = "multicast"
    
    try:
        if test_mode == "validate":
            await run_packet_validation_test()
        elif test_mode == "unicast":
            await test.test_unicast_mode()
        else:  # multicast (default)
            await test.test_multicast_mode()
            
        logger.info("Test completed successfully")
        
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
