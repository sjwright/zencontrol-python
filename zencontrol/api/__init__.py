"""
API layer: TPI command client, event receiver, and wire-facing models.
"""

from .commands import ZenCommandClient
from .event_decode import ZenEventCode, ZenEventMask
from .event_router import EventHealth, Lease, Subscription, ZenEventReceiver
from .discovery import DiscoveryLog
from .models import (
    ControllerRef,
    DiscoveredController,
    ZenRgbColour,
    ZenTcColour,
    ZenXyColour,
    ZenAddress,
    ZenColour,
    ZenController,
    ZenInstance,
    colour_from_bytes,
    colour_from_dict,
)
from .types import (
    Transport,
    ZenAddressType,
    ZenColourType,
    ZenEventMode,
    ZenInstanceType,
)

__all__ = [
    # Command / event planes
    "ZenCommandClient",
    "ZenEventReceiver",
    "Lease",
    "Subscription",
    "EventHealth",
    "DiscoveryLog",

    # Models
    "ControllerRef",
    "ZenController",
    "ZenAddress",
    "ZenInstance",
    "ZenColour",
    "ZenTcColour",
    "ZenXyColour",
    "ZenRgbColour",
    "colour_from_bytes",
    "colour_from_dict",
    "DiscoveredController",

    # Types
    "ZenAddressType",
    "ZenInstanceType",
    "ZenColourType",
    "Transport",
    "ZenEventCode",
    "ZenEventMask",
    "ZenEventMode",
]
