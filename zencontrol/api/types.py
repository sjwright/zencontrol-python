"""
API-level type definitions.

This module contains types and enums that belong to the API layer:
- DALI address types, instance types, color types
- Event masks and modes used by the TPI protocol
- Constants used by the API layer
"""

from enum import Enum
from typing import Optional, Self
from dataclasses import dataclass


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


@dataclass
class ZenEventMode:
    enabled: bool = False
    filtering: bool = False
    unicast: bool = False
    multicast: bool = False
    
    def bitmask(self) -> int:
        mode_flag = 0x00
        if self.enabled: mode_flag |= 0x01
        if self.filtering: mode_flag |= 0x02
        if self.unicast: mode_flag |= 0x40
        if not self.multicast: mode_flag |= 0x80
        return mode_flag
    
    @classmethod
    def from_byte(cls, mode_flag: int) -> Self:
        return cls(
            enabled = (mode_flag & 0x01) != 0,
            filtering = (mode_flag & 0x02) != 0,
            unicast = (mode_flag & 0x40) != 0,
            multicast = (mode_flag & 0x80) == 0
        )


class ZenEventCode(Enum):
    BUTTON_PRESS = 0x00
    BUTTON_HOLD = 0x01
    ABSOLUTE_INPUT = 0x02
    LEVEL_CHANGE = 0x03
    GROUP_LEVEL_CHANGE = 0x04
    SCENE_CHANGE = 0x05
    IS_OCCUPIED = 0x06
    SYSTEM_VARIABLE_CHANGE = 0x07
    COLOUR_CHANGE = 0x08
    PROFILE_CHANGE = 0x09
    GROUP_OCCUPIED = 0x0A
    LEVEL_CHANGE_V2 = 0x0B


@dataclass
class ZenEventMask:
    button_press: bool = False
    button_hold: bool = False
    absolute_input: bool = False
    level_change: bool = False
    group_level_change: bool = False
    scene_change: bool = False
    is_occupied: bool = False
    system_variable_change: bool = False
    colour_change: bool = False
    profile_change: bool = False
    group_occupied: bool = False
    level_change_v2: bool = False
    
    @classmethod
    def all_events(cls):
        return cls(
            button_press = True,
            button_hold = True,
            absolute_input = True,
            level_change = True,
            group_level_change = True,
            scene_change = True,
            is_occupied = True,
            system_variable_change = True,
            colour_change = True,
            profile_change = True,
            group_occupied = True,
            level_change_v2 = True
        )
    
    @classmethod
    def from_upper_lower(cls, upper: int, lower: int) -> Self:
        return cls.from_double_byte((upper << 8) | lower)
    
    @classmethod
    def from_double_byte(cls, event_mask: int) -> Self:
        return cls(
            button_press = (event_mask & (1 << ZenEventCode.BUTTON_PRESS.value)) != 0,
            button_hold = (event_mask & (1 << ZenEventCode.BUTTON_HOLD.value)) != 0,
            absolute_input = (event_mask & (1 << ZenEventCode.ABSOLUTE_INPUT.value)) != 0,
            level_change = (event_mask & (1 << ZenEventCode.LEVEL_CHANGE.value)) != 0,
            group_level_change = (event_mask & (1 << ZenEventCode.GROUP_LEVEL_CHANGE.value)) != 0,
            scene_change = (event_mask & (1 << ZenEventCode.SCENE_CHANGE.value)) != 0,
            is_occupied = (event_mask & (1 << ZenEventCode.IS_OCCUPIED.value)) != 0,
            system_variable_change = (event_mask & (1 << ZenEventCode.SYSTEM_VARIABLE_CHANGE.value)) != 0,
            colour_change = (event_mask & (1 << ZenEventCode.COLOUR_CHANGE.value)) != 0,
            profile_change = (event_mask & (1 << ZenEventCode.PROFILE_CHANGE.value)) != 0,
            group_occupied = (event_mask & (1 << ZenEventCode.GROUP_OCCUPIED.value)) != 0,
            level_change_v2 = (event_mask & (1 << ZenEventCode.LEVEL_CHANGE_V2.value)) != 0
        )
    
    def bitmask(self) -> int:
        event_mask = 0x00
        if self.button_press: event_mask |= (1 << ZenEventCode.BUTTON_PRESS.value)
        if self.button_hold: event_mask |= (1 << ZenEventCode.BUTTON_HOLD.value)
        if self.absolute_input: event_mask |= (1 << ZenEventCode.ABSOLUTE_INPUT.value)
        if self.level_change: event_mask |= (1 << ZenEventCode.LEVEL_CHANGE.value)
        if self.group_level_change: event_mask |= (1 << ZenEventCode.GROUP_LEVEL_CHANGE.value)
        if self.scene_change: event_mask |= (1 << ZenEventCode.SCENE_CHANGE.value)
        if self.is_occupied: event_mask |= (1 << ZenEventCode.IS_OCCUPIED.value)
        if self.system_variable_change: event_mask |= (1 << ZenEventCode.SYSTEM_VARIABLE_CHANGE.value)
        if self.colour_change: event_mask |= (1 << ZenEventCode.COLOUR_CHANGE.value)
        if self.profile_change: event_mask |= (1 << ZenEventCode.PROFILE_CHANGE.value)
        if self.group_occupied: event_mask |= (1 << ZenEventCode.GROUP_OCCUPIED.value)
        if self.level_change_v2: event_mask |= (1 << ZenEventCode.LEVEL_CHANGE_V2.value)
        return event_mask
    
    def upper(self) -> int:
        return (self.bitmask() >> 8) & 0xFF
    
    def lower(self) -> int:
        return self.bitmask() & 0xFF


# API-level constants
class Const:
    """API-level constants"""
    # UDP protocol - use zen_protocol constants
    RESPONSE_TIMEOUT = 3.0  # Default timeout from ClientConst

    # DALI limits
    MAX_ECG = 64  # 0-63
    MAX_ECD = 64  # 0-63
    MAX_INSTANCE = 32  # 0-31
    MAX_GROUP = 16  # 0-15
    MAX_SCENE = 12  # 0-11
    MAX_SYSVAR = 148  # 0-147
    MAX_LEVEL = 254  # 255 is mask value (i.e. no change)
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

    # Cache
    CACHE_TIMEOUT = 1*60*60  # 1 hour
