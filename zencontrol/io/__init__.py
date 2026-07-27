"""
Wire-level protocol implementation.

This module contains the lowest-level communication components:
- ZenClient — connected UDP request/response
- ZenEndpoint / ZenEvent / parse_frame — event bind + envelope validation
- Message framing and connection management

Event queuing and routing live in ``zencontrol.api.event_router`` (one funnel).
"""

from .command import (
    ClientConst,
    ZenClient,
    ZenRequest,
    ZenRequestType,
    ZenResponse,
    ZenResponseType,
)
from .event import EventConst, ZenEndpoint, ZenEvent, accept_datagram, parse_frame

__all__ = [
    "ZenClient",
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
