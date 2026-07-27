#!/usr/bin/env python3
"""
Standalone TPI event listener — one funnel via ZenEventReceiver.

Demonstrates multicast / unicast leases and event delivery without the
high-level entity layer. Prefer ``examples/live/events.py`` + ``ZenControl``
for application code.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
import time

from zencontrol import Transport, ZenEvent, run_with_keyboard_interrupt
from zencontrol.api.event_router import ZenEventReceiver
from zencontrol.io.event import EventConst, parse_frame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_EVENT_NAMES = (
    "Button Press",
    "Button Hold",
    "Absolute Input",
    "Level Change",
    "Group Level Change",
    "Scene Change",
    "Is Occupied",
    "System Variable Change",
    "Colour Change",
    "Profile Change",
    "Group Occupied",
    "Level Change V2",
)


def _log_event(event: ZenEvent) -> None:
    name = (
        _EVENT_NAMES[event.code]
        if event.code < len(_EVENT_NAMES)
        else f"Unknown({event.code})"
    )
    logger.info(
        "%s: target=%s payload=%s mac=%s host=%s",
        name,
        event.target,
        event.payload.hex(),
        event.mac.hex(":"),
        event.host,
    )


def _make_packet(mac: str, code: int, target: int, payload: bytes) -> bytes:
    mac_bytes = bytes.fromhex(mac.replace(":", ""))
    body = bytearray()
    body.extend([0x5A, 0x43])
    body.extend(mac_bytes)
    body.extend(target.to_bytes(2, "big"))
    body.append(code)
    body.append(len(payload))
    body.extend(payload)
    checksum = 0
    for b in body:
        checksum ^= b
    body.append(checksum & 0xFF)
    return bytes(body)


async def _listen(*, unicast: bool, listen_port: int = 6969) -> None:
    receiver = ZenEventReceiver(logger=logger, unicast_port=listen_port)
    # Log every validated frame before routing / discovery.
    _route = receiver.handle

    async def handle(event: ZenEvent) -> None:
        _log_event(event)
        await _route(event)

    receiver.handle = handle  # type: ignore[method-assign]

    transport = Transport.UNICAST if unicast else Transport.MULTICAST
    lease = await receiver.acquire(transport)
    kind = "UNICAST" if unicast else "MULTICAST"
    if unicast:
        logger.info("Listening %s on 0.0.0.0:%s — Ctrl+C to stop", kind, listen_port)
    else:
        logger.info(
            "Listening %s on %s:%s — Ctrl+C to stop",
            kind,
            EventConst.MULTICAST_GROUP,
            EventConst.MULTICAST_PORT,
        )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await lease.release()
        await receiver.close()


async def _validate() -> None:
    """Bind unicast, inject a few crafted packets, confirm parse + delivery."""
    received: list[ZenEvent] = []
    receiver = ZenEventReceiver(logger=logger, unicast_port=6969)

    async def handle(event: ZenEvent) -> None:
        received.append(event)
        logger.info(
            "Received test event code=%s target=%s payload=%s",
            event.code,
            event.target,
            event.payload.hex(),
        )

    receiver.handle = handle  # type: ignore[method-assign]
    lease = await receiver.acquire(Transport.UNICAST)
    try:
        await asyncio.sleep(0.2)
        packets = [
            _make_packet("aa:bb:cc:dd:ee:ff", 0x00, 64, b"\x01"),
            _make_packet("aa:bb:cc:dd:ee:ff", 0x03, 0, b"\x80"),
            _make_packet("aa:bb:cc:dd:ee:ff", 0x05, 0, b"\x01"),
        ]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for packet in packets:
                sock.sendto(packet, ("127.0.0.1", 6969))
                await asyncio.sleep(0.05)
        finally:
            sock.close()

        deadline = time.monotonic() + 2.0
        while len(received) < len(packets) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        if len(received) == len(packets):
            logger.info("Packet validation PASSED (%d events)", len(received))
        else:
            logger.error(
                "Packet validation FAILED: expected %d, got %d",
                len(packets),
                len(received),
            )
            # Offline fallback: parse_frame still proves envelope validity
            for packet in packets:
                assert parse_frame(packet, ("127.0.0.1", 6969)) is not None
    finally:
        await lease.release()
        await receiver.close()


async def main() -> None:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "multicast"
    print("Zen TPI Event Listener")
    print("=" * 50)
    print("  multicast | unicast | validate")
    print("=" * 50)

    if mode == "validate":
        await _validate()
    elif mode == "unicast":
        await _listen(unicast=True)
    else:
        await _listen(unicast=False)


if __name__ == "__main__":
    run_with_keyboard_interrupt(main)
