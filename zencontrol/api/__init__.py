"""
API-level models and protocol implementation.

This module contains models and types that belong to the API layer:
- ZenController, ZenAddress, ZenInstance (API-level concepts)
- ZenProtocol (implements TPI commands)
- ZenColour, ZenProfile (API-level concepts used by TPI protocol)
- Types and enums used by the API layer
"""

from .models import ZenController, ZenAddress, ZenInstance, ZenColour, ZenProfile, DiscoveredController
from .protocol import ZenProtocol
from .types import ZenAddressType, ZenInstanceType, ZenColourType, ZenEventMask, ZenEventMode

__all__ = [
    # API-level models
    "ZenController",
    "ZenAddress", 
    "ZenInstance",
    "ZenColour",
    "ZenProfile",
    "DiscoveredController",
    "ZenProtocol",
    
    # API-level types
    "ZenAddressType",
    "ZenInstanceType",
    "ZenColourType",
    "ZenEventMask",
    "ZenEventMode",
]