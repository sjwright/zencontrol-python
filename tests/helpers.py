"""Shared test helpers."""

from __future__ import annotations

from collections.abc import Callable

import asyncio

async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    interval: float = 0.05,
    message: str = "condition not met",
) -> None:
    """Poll until ``predicate()`` is true or raise AssertionError."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(message)
