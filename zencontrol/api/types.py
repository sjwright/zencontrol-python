"""
API enums and typed query results
=================================

This module holds vocabulary shared by commands, models, and the event plane.

"ZenAddressType", "ZenInstanceType", "ZenColourType", and "ZenCgType" describe
DALI targets, colour modes, and control-gear device types. "ZenErrorCode" names
TPI error replies.

"Transport" and "ZenEventMode" describe how a controller emits events
(multicast or unicast) and build the emit-state bitmask for the command plane.

Frozen dataclasses such as TpiEventUnicastAddress and ControlGearStatus
type the structured replies from query commands.

"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
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


class ZenErrorCode(IntEnum):
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


class ZenCgType(IntEnum):
    """DALI control-gear device type numbers (bit index in DALI_QUERY_CG_TYPE)."""

    FLUORESCENT = 0
    EMERGENCY = 1
    DISCHARGE = 2
    HALOGEN = 3
    INCANDESCENT = 4
    DC = 5
    LED = 6
    RELAY = 7
    COLOUR_CONTROL = 8
    LOAD_REFERENCING = 15
    THERMAL_GEAR_PROTECTION = 16
    DIMMING_CURVE_SELECTION = 17


@dataclass(slots=True)
class ZenEventMode:
    """TPI event emit mode. Exactly one transport (multicast or unicast)."""

    enabled: bool = False
    filtering: bool = False
    transport: Transport = Transport.MULTICAST

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


@dataclass(frozen=True, slots=True)
class TpiEventUnicastAddress:
    """Result of QUERY_TPI_EVENT_UNICAST_ADDRESS."""

    mode: ZenEventMode
    port: int
    ip: str


@dataclass(frozen=True, slots=True)
class ControlGearStatus:
    """Result of DALI_QUERY_CONTROL_GEAR_STATUS (one status byte)."""

    cg_failure: bool
    lamp_failure: bool
    lamp_power_on: bool
    limit_error: bool
    fade_running: bool
    reset: bool
    missing_short_address: bool
    power_failure: bool


@dataclass(frozen=True, slots=True)
class DaliColourFeatures:
    """Result of QUERY_DALI_COLOUR_FEATURES."""

    supports_xy: bool
    supports_tunable: bool
    primary_count: int
    rgbwaf_channels: int


@dataclass(frozen=True, slots=True)
class OccupancyInstanceTimers:
    """Result of QUERY_OCCUPANCY_INSTANCE_TIMERS."""

    deadtime: int
    hold: int
    report: int
    last_detect: int


@dataclass(frozen=True, slots=True)
class DaliColourTempLimits:
    """Result of QUERY_DALI_COLOUR_TEMP_LIMITS (kelvin)."""

    physical_warmest: int
    physical_coolest: int
    soft_warmest: int
    soft_coolest: int
    step_value: int


@dataclass(frozen=True, slots=True)
class GroupStatus:
    """Result of QUERY_GROUP_BY_NUMBER."""

    number: int
    occupied: bool
    level: int


@dataclass(frozen=True, slots=True)
class InstanceGroups:
    """Result of QUERY_INSTANCE_GROUPS.

    Each field is a DALI group 0-15, or None when unconfigured (wire 0xFF).
    primary is typically where the physical device resides.
    """

    primary: int | None
    first: int | None
    second: int | None


@dataclass(frozen=True, slots=True)
class ProfileBehaviour:
    """One profile record from QUERY_PROFILE_INFORMATION."""

    enabled: bool
    priority: int
    priority_label: str


@dataclass(frozen=True, slots=True)
class ProfileState:
    """Header of QUERY_PROFILE_INFORMATION."""

    current_active_profile: int
    last_scheduled_profile: int
    last_overridden_profile_utc: datetime
    last_scheduled_profile_utc: datetime


