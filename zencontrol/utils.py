"""
Utility functions for the ZenControl library
"""
import asyncio
import signal
import socket
import sys
from typing import Callable, Any


def local_ip_for_remote(remote_host: str) -> str:
    """Return the local IPv4 address used to reach ``remote_host``.

    Uses a UDP ``connect`` (no packets sent) so the OS picks the correct
    interface on multi-homed hosts, Docker, and macOS — unlike
    ``socket.gethostname()``.
    """
    try:
        remote_ip = socket.gethostbyname(remote_host)
    except OSError:
        remote_ip = remote_host

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((remote_ip, 1))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def run_with_keyboard_interrupt(main_func: Callable[[], Any]) -> None:
    """
    Run an async main function with graceful KeyboardInterrupt handling.
    
    This function wraps asyncio.run() to catch KeyboardInterrupt (Ctrl+C) and
    provide a clean shutdown experience.
    
    Args:
        main_func: The async main function to run
    """
    try:
        asyncio.run(main_func())
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user (Ctrl+C)")
        print("Shutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def setup_signal_handlers() -> None:
    """
    Set up signal handlers for graceful shutdown.
    
    This function sets up handlers for SIGINT (Ctrl+C) and SIGTERM to ensure
    clean shutdown of async operations.
    """
    def signal_handler(signum, frame):
        print(f"\n🛑 Received signal {signum}, shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
