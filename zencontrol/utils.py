"""
Utility functions for the ZenControl library
"""

import asyncio
import ipaddress
import signal
import socket
from typing import Any


def is_ipv4_address(host: str) -> bool:
    """True when ``host`` is already a dotted-quad IPv4 literal (no DNS)."""
    try:
        ipaddress.IPv4Address(host)
        return True
    except ValueError:
        return False


def resolve_host_sync(host: str) -> str:
    """Resolve ``host`` to an IPv4 address. Safe to call from an executor.

    IPv4 literals are returned unchanged — never passed to ``gethostbyname``,
    which Home Assistant flags as blocking even for literals.
    """
    if is_ipv4_address(host):
        return host
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


async def resolve_host(host: str) -> str:
    """Async-safe IPv4 resolve — DNS runs in the default executor."""
    if is_ipv4_address(host):
        return host
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, socket.gethostbyname, host)
    except OSError:
        return host


def local_ip_for_remote(remote_host: str) -> str:
    """Return the local IPv4 address used to reach ``remote_host``.

    Uses a UDP ``connect`` (no packets sent) so the OS picks the correct
    interface on multi-homed hosts, Docker, and macOS — unlike
    ``socket.gethostname()``.
    """
    remote_ip = resolve_host_sync(remote_host)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((remote_ip, 1))
            return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def setup_signal_handlers() -> None:
    """Set up SIGINT / SIGTERM handlers for graceful process exit."""

    def signal_handler(signum: int, frame: Any) -> None:
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
