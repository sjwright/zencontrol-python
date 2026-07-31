"""
API enums and constants
=======================

This module holds vocabulary shared by commands, models, and the event plane.

"ZenAddressType", "ZenInstanceType", and "ZenColourType" describe DALI targets
and colour modes. "ZenErrorCode" names TPI error replies.

"Transport" and "ZenEventMode" describe how a controller emits events
(multicast or unicast) and build the emit-state bitmask for the command plane.

"Const" is a collection of constants and defaults used throughout the API.

"""

from dataclasses import dataclass
from enum import Enum
from typing import Self


class Transport(Enum):
    """Event delivery transport. A controller emits on exactly one (I3)."""

    MULTICAST = "multicast"
    UNICAST = "unicast"


class ZenAddressType(Enum):
    BROADCAST = 0
    ECG = 1  # Control Gear
    ECD = 2  # Control Device  
    GROUP = 3


class ZenInstanceType(Enum):
    PUSH_BUTTON = 0x01
    ABSOLUTE_INPUT = 0x02
    OCCUPANCY_SENSOR = 0x03
    LIGHT_SENSOR = 0x04
    GENERAL_SENSOR = 0x06


class ZenColourType(Enum):
    XY = 0x10
    TC = 0x20  # Tunable White
    RGBWAF = 0x80


class ZenErrorCode(Enum):
    CHECKSUM = 0x01
    SHORT_CIRCUIT = 0x02
    RECEIVE_ERROR = 0x03
    UNKNOWN_CMD = 0x04
    PAID_FEATURE = 0xB0
    INVALID_ARGS = 0xB1
    CMD_REFUSED = 0xB2
    QUEUE_FAILURE = 0xB3
    RESPONSE_UNAVAIL = 0xB4
    OTHER_DALI_ERROR = 0xB5
    MAX_LIMIT = 0xB6
    UNEXPECTED_RESULT = 0xB7
    UNKNOWN_TARGET = 0xB8


@dataclass(slots=True)
class ZenEventMode:
    """TPI event emit mode. Exactly one transport - dual bools are unrepresentable."""

    enabled: bool = False
    filtering: bool = False
    transport: Transport = Transport.MULTICAST

    def __init__(
        self,
        enabled: bool = False,
        filtering: bool = False,
        transport: Transport | None = None,
        *,
        unicast: bool | None = None,
        multicast: bool | None = None,
    ) -> None:
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "filtering", filtering)
        if transport is not None:
            if unicast is not None or multicast is not None:
                raise ValueError("pass transport= or unicast=/multicast=, not both")
            object.__setattr__(self, "transport", transport)
        elif unicast is not None or multicast is not None:
            u = bool(unicast)
            m = bool(multicast) if multicast is not None else not u
            if u and m:
                raise ValueError("a controller emits on exactly one transport (I3)")
            object.__setattr__(self, "transport", Transport.UNICAST if u else Transport.MULTICAST)
        else:
            object.__setattr__(self, "transport", Transport.MULTICAST)

    @property
    def unicast(self) -> bool:
        return self.transport is Transport.UNICAST

    @property
    def multicast(self) -> bool:
        return self.transport is Transport.MULTICAST

    def bitmask(self) -> int:
        # 0x80 is inverted: set when multicast is OFF.
        # MULTICAST -> neither 0x40 nor 0x80; UNICAST -> both.
        mode_flag = 0x00
        if self.enabled:
            mode_flag |= 0x01
        if self.filtering:
            mode_flag |= 0x02
        if self.transport is Transport.UNICAST:
            mode_flag |= 0x40
            mode_flag |= 0x80
        return mode_flag

    @classmethod
    def from_byte(cls, mode_flag: int) -> Self:
        return cls(
            enabled=(mode_flag & 0x01) != 0,
            filtering=(mode_flag & 0x02) != 0,
            transport=(Transport.UNICAST if (mode_flag & 0x40) != 0 else Transport.MULTICAST),
        )


# API-level constants
class Const:
    """API-level constants"""
    # ZenControl.start() waits this long for the first successful event-listener connect
    START_TIMEOUT = 30.0

    # DALI limits
    MAX_ECG = 64  # 0-63
    MAX_ECD = 64  # 0-63
    MAX_INSTANCE = 32  # 0-31
    MAX_GROUP = 16  # 0-15
    MAX_SCENE = 12  # DALI protocol is 16 (0-15) but zencontrol cloud is soft-limited to 12 (0-11)
    MAX_SYSVAR = 148  # 0-147
    MAX_LEVEL = 254  # highest dimming arc
    MASK_LEVEL = 255  # DAPC mask (no change / stop fade on blinds)
    MIN_KELVIN = 1000
    MAX_KELVIN = 20000

    # Color temperature defaults (only used if query_dali_colour_temp_limits fails)
    DEFAULT_WARMEST_TEMP = 2700
    DEFAULT_COOLEST_TEMP = 6500
    
    # RGB channel counts
    RGB_CHANNELS = 3
    RGBW_CHANNELS = 4
    RGBWW_CHANNELS = 5
    
    # Button press constants
    LONG_PRESS_COUNT = 2
    DEFAULT_HOLD_TIME = 60

    # Event-listener reconnect (ZenControl supervisor)
    RECONNECT_MIN_DELAY = 1.0
    RECONNECT_MAX_DELAY = 30.0
    RECONNECT_HEALTHY_SECONDS = 60.0
    
    # Periodic emit-state check - controllers that reboot while our listener
    # stays up lose TPI event config until we re-assert it.
    EVENT_KEEPALIVE_INTERVAL = 30.0

    # Event-plane silence: RECEIVING demotes to SILENT when last_seen is older
    # than this. Absence is ambiguous - expose it for diagnostics, do not
    # treat it as transport failure.
    EVENT_SILENT_AFTER = 60.0
