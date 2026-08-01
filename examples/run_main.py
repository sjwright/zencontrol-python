"""Shared runner for live examples - not part of the library API."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any


def run_with_keyboard_interrupt(main_func: Callable[[], Awaitable[Any]]) -> None:
    """Run an async main with clean KeyboardInterrupt / error exit."""
    try:
        asyncio.run(main_func())
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C)")
        print("Shutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
