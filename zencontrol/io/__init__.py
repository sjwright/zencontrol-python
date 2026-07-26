"""
Wire-level protocol implementation.

This module contains the lowest-level communication components:
- ZenClient, ZenEndpoint - Raw UDP communication
- ZenEvent / parse_frame - Envelope validation (codes opaque)
- Message framing and connection management

Event queuing and routing live in ``zencontrol.api.event_router`` (one funnel).
"""

from .command import (
    ClientConst,
    Request,
    RequestType,
    Response,
    ResponseType,
    ZenClient,
)
from .event import EventConst, ZenEndpoint, ZenEvent, parse_frame

__all__ = [
    "ZenClient",
    "ZenEndpoint",
    "ZenEvent",
    "parse_frame",
    "Request",
    "Response",
    "ResponseType",
    "RequestType",
    "EventConst",
    "ClientConst",
]
