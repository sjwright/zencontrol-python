"""
ZenControl Python Library

A Python library for interfacing with ZenControl DALI lighting controllers.

Layers:

1. **io** — wire-level UDP framing (``ZenClient``, request/response envelopes)
2. **api** — TPI command client + event receiver (``ZenCommandClient``, ``ZenEventReceiver``)
3. **interface** — high-level entities and session orchestration (``ZenControl``)

Recommended entry point is ``ZenControl`` (commands + events + discovery).
``EntityContext`` is advanced/command-only: entity identity and callbacks without
an event session — prefer ``ZenControl`` unless you intentionally drive the
command plane yourself.

Example::

    import zencontrol

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
"""

# High-level interface (recommended for most users)
# API-level models
# Shared types and exceptions
from .api.event_decode import ZenEventCode, ZenEventMask
from .api.event_router import EventHealth
from .api.models import DiscoveredController, ZenAddress, ZenColour, ZenInstance
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
    ZenCallbacks,
    ZenControl,
    ZenController,
    ZenGroup,
    ZenLight,
    ZenMotionSensor,
    ZenProfile,
    ZenSystemVariable,
)

# Low-level models
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
    "ZenCallbacks",
    # Advanced: command-only entity context (no event session)
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
