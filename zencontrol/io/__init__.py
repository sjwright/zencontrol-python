"""
Wire-level protocol implementation.

This module contains the lowest-level communication components:
- ZenClient - connected UDP request/response
- ZenTcpClient - connected TCP request/response (same framing; FW ≥ 2.2.32)
- ZenEndpoint / ZenEvent / parse_frame - event bind + envelope validation
- Message framing and connection management

Event queuing and routing live in zencontrol.api.event_router (one funnel).
"""

from .command import ZenClient
from .command_tcp import ZenTcpClient
from .const import ClientConst
from .event import EventConst, ZenEndpoint, ZenEvent, accept_datagram, parse_frame
from .models import ZenRequest, ZenRequestType, ZenResponse, ZenResponseType

__all__ = [
    "ZenClient",
    "ZenTcpClient",
    "ZenEndpoint",
    "ZenEvent",
    "accept_datagram",
    "parse_frame",
    "ZenRequest",
    "ZenResponse",
    "ZenResponseType",
    "ZenRequestType",
    "EventConst",
    "ClientConst",
]
