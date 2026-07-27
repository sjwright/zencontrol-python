"""Shared fake endpoint factory for lease/session unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from zencontrol.io.event import EventConst, ZenEvent, accept_datagram, parse_frame


def require_event(data: bytes, addr: tuple[str, int] = ("127.0.0.1", 1)) -> ZenEvent:
    """Parse a crafted frame or fail the test — same gate as the live endpoint."""
    event = parse_frame(data, addr)
    assert event is not None, f"invalid event frame from {addr!r}"
    return event


def push_datagram(sink, data: bytes, addr: tuple[str, int] = ("127.0.0.1", 1)) -> bool:
    """Simulate ZenEventProtocol: parse then sink (production handoff)."""
    return accept_datagram(data, addr, sink)


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
        # Stash sink so tests can push via push_datagram / inject(ZenEvent)
        ep.sink = kwargs.get("sink")
        return ep

    return open_endpoint
