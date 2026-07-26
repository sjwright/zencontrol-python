"""
ZenControl Python Library

A Python library for interfacing with ZenControl DALI lighting controllers.

This library provides three distinct layers of abstraction:

1. **zen_io**: Wire-level protocol implementation (UDP, message framing)
2. **zen_api**: Zen API calls using zen_io (DALI commands, TPI protocol)
3. **zen_interface**: Pythonic interface to Zen entities using zen_api (high-level objects)

Example usage:
    import zencontrol

    # High-level interface (recommended for most users)
    async with zencontrol.ZenControl() as zen:
        zen.add_controller(
            id=1,
            name="living",
            label="Living Room",
            host="192.168.1.100",
            port=5108,
        )
        await zen.start()
        lights = await zen.get_lights()
        for light in lights:
            await light.set(level=50)

    # Low-level API access (for advanced users)
    async with zencontrol.ZenControl() as zen:
        zen.add_controller(id=1, name="living", label="Living Room", host="192.168.1.100")
        await zen.start()
        for light in await zen.get_lights():
            await light.set(level=50)
"""

# High-level interface (recommended for most users)
# API-level models (used by zen_api)
from .api.models import DiscoveredController, ZenAddress, ZenColour, ZenInstance

# Shared types and exceptions
from .api.event_decode import ZenEventCode, ZenEventMask
from .api.event_router import EventHealth
from .api.types import (
    Transport,
    ZenAddressType,
    ZenColourType,
    ZenEventMode,
    ZenInstanceType,
)
from .exceptions import (
    ZenConfigurationError,
    ZenConnectionError,
    ZenError,
    ZenResponseError,
    ZenTimeoutError,
)
from .interface import (
    EntityContext,
    ZenAbsoluteInput,
    ZenButton,
    ZenControl,
    ZenController,
    ZenGroup,
    ZenLight,
    ZenMotionSensor,
    ZenProfile,
    ZenSystemVariable,
)

# Low-level models (used by zen_io)
from .io import (
    Request,
    RequestType,
    Response,
    ResponseType,
    ZenClient,
    ZenEvent,
)

# Utilities
from .utils import run_with_keyboard_interrupt

__version__ = "0.1.7"
__author__ = "Simon Wright"

# Public API - these are the main classes users should import
__all__ = [
    # High-level interface (recommended)
    "ZenControl",
    "EntityContext",

    # High-level models (for most users)
    "ZenController",
    "ZenProfile",
    "ZenLight",
    "ZenGroup",
    "ZenButton",
    "ZenAbsoluteInput",
    "ZenMotionSensor",
    "ZenSystemVariable",
    
    # API-level models (for advanced users)
    "ZenAddress",
    "ZenInstance",
    "ZenColour",
    "DiscoveredController",

    # Low-level models (for advanced users)
    "ZenClient",
    "ZenEvent",
    "Request",
    "RequestType",
    "Response",
    "ResponseType",
    
    # Exceptions
    "ZenError",
    "ZenTimeoutError",
    "ZenResponseError",
    "ZenConnectionError",
    "ZenConfigurationError",
    
    # Types and enums
    "ZenAddressType",
    "ZenInstanceType",
    "ZenColourType",
    "Transport",
    "ZenEventCode",
    "ZenEventMask",
    "ZenEventMode",
    "EventHealth",
    
    # Utilities
    "run_with_keyboard_interrupt",
]
