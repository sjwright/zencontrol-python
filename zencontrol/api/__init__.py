"""
API-level models and protocol implementation.

This module contains models and types that belong to the API layer:
- ZenController, ZenAddress, ZenInstance (API-level concepts)
- ZenCommandClient (implements TPI commands)
- ZenColour, ZenProfile (API-level concepts used by TPI protocol)
- Types and enums used by the API layer
"""

from .models import (
    ControllerRef,
    DiscoveredController,
    ZenAddress,
    ZenColour,
    ZenController,
    ZenInstance,
    ZenProfile,
)
from .commands import ZenCommandClient
from .event_decode import ZenEventCode, ZenEventMask
from .event_router import EventHealth
from .identity import IdentityLog
from .types import (
    Transport,
    ZenAddressType,
    ZenColourType,
    ZenEventMode,
    ZenInstanceType,
)

__all__ = [
    # API-level models
    "ControllerRef",
    "ZenController",
    "ZenAddress",
    "ZenInstance",
    "ZenColour",
    "ZenProfile",
    "DiscoveredController",
    "IdentityLog",
    "ZenCommandClient",

    # API-level types
    "ZenAddressType",
    "ZenInstanceType",
    "ZenColourType",
    "Transport",
    "ZenEventCode",
    "ZenEventMask",
    "ZenEventMode",
    "EventHealth",
]
