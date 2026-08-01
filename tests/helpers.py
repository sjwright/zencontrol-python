"""Shared test helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

# The first-generation DALI lighting commands (arc/scene/up/down/off/recall/
# go-to-last-active) acknowledge with REPLY_NO_ANSWER rather than REPLY_OK, which
# maps to False for return_type='ok'. Anything built on those commands therefore
# reports False on success.
LEGACY_ACK = False


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    interval: float = 0.05,
    message: str = "condition not met",
) -> None:
    """Poll until predicate() is true or raise AssertionError."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(message)
