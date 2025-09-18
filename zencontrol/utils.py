"""
Utility functions for the ZenControl library
"""
import asyncio
import signal
import sys
from typing import Callable, Any


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
