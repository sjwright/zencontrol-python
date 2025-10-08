"""
ZenControl Python Library

A Python library for interfacing with ZenControl DALI lighting controllers.

This library provides three distinct layers of abstraction:

1. **zen_io**: Wire-level protocol implementation (TCP/UDP, message framing)
2. **zen_api**: Zen API calls using zen_io (DALI commands, TPI protocol)
3. **zen_interface**: Pythonic interface to Zen entities using zen_api (high-level objects)

Example usage:
    import zencontrol
    
    # High-level interface (recommended for most users)
    async with zencontrol.ZenControl() as zen:
        await zen.add_controller(host="192.168.1.100", port=5108, ...)
        lights = await zen.get_lights()
        for light in lights:
            await light.set_level(50)
    
    # Low-level API access (for advanced users)
    async with zencontrol.ZenProtocol() as protocol:
        controller = zencontrol.ZenController(protocol=protocol, ...)
        await protocol.dali_arc_level(address, 50)
"""

# High-level interface (recommended for most users)
from .interface import ZenControl

# API-level models (used by zen_api and zen_interface)
from .api.models import ZenController, ZenAddress, ZenInstance, ZenColour, ZenProfile
from .api.protocol import ZenProtocol

# High-level models (used by zen_interface)
from .interface import ZenLight, ZenGroup, ZenButton, ZenMotionSensor, ZenSystemVariable

# Low-level models (used by zen_io)
from .io import ZenClient, ZenListener, ZenEvent, Request, Response, ResponseType, RequestType

# Shared types and exceptions
from .api.types import ZenAddressType, ZenInstanceType, ZenColourType, ZenEventCode, ZenEventMask, ZenEventMode
from .exceptions import ZenError, ZenTimeoutError, ZenResponseError

# Utilities
from .utils import run_with_keyboard_interrupt

__version__ = "0.0.0"
__author__ = "Simon Wright"

# Public API - these are the main classes users should import
__all__ = [
    # High-level interface (recommended)
    "ZenControl",
    
    # High-level models (for most users)
    "ZenLight", 
    "ZenGroup",
    "ZenButton",
    "ZenMotionSensor",
    "ZenSystemVariable",
    
    # API-level models (for advanced users)
    "ZenController", 
    "ZenAddress",
    "ZenInstance",
    "ZenProtocol",
    "ZenColour",
    "ZenProfile",

    # Low-level models (for advanced users)
    "ZenClient",
    "ZenListener",
    "ZenEvent",
    "Request",
    "RequestType",
    "Response",
    "ResponseType",
    
    # Exceptions
    "ZenError",
    "ZenTimeoutError", 
    "ZenResponseError",
    
    # Types and enums
    "ZenAddressType",
    "ZenInstanceType",
    "ZenColourType",
    "ZenEventCode",
    "ZenEventMask",
    "ZenEventMode",
    
    # Utilities
    "run_with_keyboard_interrupt",
]