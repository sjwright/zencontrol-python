"""Shared fake endpoint factory for lease/session unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from zencontrol.io.event import EventConst


def fake_endpoint_factory(*, unicast_port: int = 41234):
    """Return an async factory suitable for ``receiver._endpoint_factory``."""

    async def open_endpoint(**kwargs):
        ep = MagicMock()
        ep.is_open.return_value = True
        ep.unicast = bool(kwargs.get("unicast"))
        if ep.unicast:
            port = kwargs.get("listen_port") or 0
            ep.bound_port = port or unicast_port
        else:
            ep.bound_port = EventConst.MULTICAST_PORT
        ep.listen_port = ep.bound_port
        ep.close = AsyncMock()
        ep.sink = kwargs.get("sink")
        return ep

    return open_endpoint
