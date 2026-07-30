"""
TPI event vocabulary and payload decoding
=========================================

This module turns a well-formed "ZenEvent" envelope into a typed dataclass.
It is a pure module with no I/O, no logging, no controller lookup, no sockets.

"decode_zen_event(event)" is the single entry point. It returns a typed
dataclass (e.g. "ButtonPress") or None if the event is unknown or the payload
is the wrong length. No exceptions are ever raised.

"ZenEventCode" is the wire vocabulary.

"ZenEventMask" uses that vocabulary to build bitmasks to enable or filter
events on the controller.

-----------------------------------------------------
Basic example:

    from zencontrol.io.event import parse_frame
    from zencontrol.api.event_decode import decode_zen_event

    event = parse_frame(datagram, addr)
    if event is None:
        return
    decoded = decode_zen_event(event)
    if decoded is None:
        return
    print(decoded)

-----------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Self

from ..io.event import ZenEvent
from .types import Const


class ZenEventCode(IntEnum):
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


class ZenEventMask(IntFlag):
    """Bitmask over ZenEventCode, one flag per event code."""

    BUTTON_PRESS = 1 << ZenEventCode.BUTTON_PRESS
    BUTTON_HOLD = 1 << ZenEventCode.BUTTON_HOLD
    ABSOLUTE_INPUT = 1 << ZenEventCode.ABSOLUTE_INPUT
    LEVEL_CHANGE = 1 << ZenEventCode.LEVEL_CHANGE
    GROUP_LEVEL_CHANGE = 1 << ZenEventCode.GROUP_LEVEL_CHANGE
    SCENE_CHANGE = 1 << ZenEventCode.SCENE_CHANGE
    IS_OCCUPIED = 1 << ZenEventCode.IS_OCCUPIED
    SYSTEM_VARIABLE_CHANGE = 1 << ZenEventCode.SYSTEM_VARIABLE_CHANGE
    COLOUR_CHANGE = 1 << ZenEventCode.COLOUR_CHANGE
    PROFILE_CHANGE = 1 << ZenEventCode.PROFILE_CHANGE
    GROUP_OCCUPIED = 1 << ZenEventCode.GROUP_OCCUPIED
    LEVEL_CHANGE_V2 = 1 << ZenEventCode.LEVEL_CHANGE_V2

    @classmethod
    def all_events(cls) -> Self:
        # Subscribe mask for normal use. Excludes:
        # - deprecated LEVEL_CHANGE / GROUP_LEVEL_CHANGE (use LEVEL_CHANGE_V2)
        # - GROUP_OCCUPIED (not used by the interface layer)
        return (
            cls.BUTTON_PRESS | cls.BUTTON_HOLD | cls.ABSOLUTE_INPUT
            | cls.SCENE_CHANGE | cls.IS_OCCUPIED | cls.SYSTEM_VARIABLE_CHANGE
            | cls.COLOUR_CHANGE | cls.PROFILE_CHANGE | cls.LEVEL_CHANGE_V2
        )

    @classmethod
    def from_upper_lower(cls, upper: int, lower: int) -> Self:
        return cls((upper << 8) | lower)

    def upper(self) -> int:
        return (int(self) >> 8) & 0xFF

    def lower(self) -> int:
        return int(self) & 0xFF


@dataclass(frozen=True, slots=True)
class ButtonPress:
    target: int
    instance: int


@dataclass(frozen=True, slots=True)
class ButtonHold:
    target: int
    instance: int


@dataclass(frozen=True, slots=True)
class AbsoluteInput:
    target: int
    instance: int
    value: int


@dataclass(frozen=True, slots=True)
class LevelChange:
    """Deprecated LEVEL_CHANGE (0x03); superseded by LevelChangeV2."""
    target: int
    level: int


@dataclass(frozen=True, slots=True)
class GroupLevelChange:
    """Deprecated GROUP_LEVEL_CHANGE (0x04); superseded by LevelChangeV2."""
    target: int
    level: int


@dataclass(frozen=True, slots=True)
class SceneChange:
    target: int
    scene: int
    active: bool


@dataclass(frozen=True, slots=True)
class IsOccupied:
    target: int
    instance: int


@dataclass(frozen=True, slots=True)
class SystemVariableChange:
    target: int
    value: int


@dataclass(frozen=True, slots=True)
class ColourChange:
    target: int
    colour: bytes  # DALI encoding; model layer decodes via colour_from_bytes


@dataclass(frozen=True, slots=True)
class ProfileChange:
    profile: int


@dataclass(frozen=True, slots=True)
class GroupOccupied:
    target: int
    occupied: bool


@dataclass(frozen=True, slots=True)
class LevelChangeV2:
    target: int
    current: int
    level: int  # arc level dimming to (authoritative destination)


ZenDecodedEvent = (
    ButtonPress
    | ButtonHold
    | AbsoluteInput
    | LevelChange
    | GroupLevelChange
    | SceneChange
    | IsOccupied
    | SystemVariableChange
    | ColourChange
    | ProfileChange
    | GroupOccupied
    | LevelChangeV2
)


def decode_zen_event(event: ZenEvent) -> ZenDecodedEvent | None:
    """Interpret event code and payload. Returns None if unknown or wrong length.

    Fixed-size codes require len(payload) == N - trailing junk on a
    checksummed frame is rejection, not silent ignore. COLOUR_CHANGE is
    variable (3-7 bytes) per DALI colour type.
    """
    try:
        code = ZenEventCode(event.code)
    except ValueError:
        return None

    payload = event.payload
    target = event.target

    match code:
        case ZenEventCode.BUTTON_PRESS:
            if len(payload) != 1:
                return None
            return ButtonPress(target=target, instance=payload[0])

        case ZenEventCode.BUTTON_HOLD:
            if len(payload) != 1:
                return None
            return ButtonHold(target=target, instance=payload[0])

        case ZenEventCode.ABSOLUTE_INPUT:
            if len(payload) != 3:
                return None
            value = (payload[1] << 8) | payload[2]
            return AbsoluteInput(target=target, instance=payload[0], value=value)

        case ZenEventCode.LEVEL_CHANGE:
            if len(payload) != 1:
                return None
            return LevelChange(target=target, level=payload[0])

        case ZenEventCode.GROUP_LEVEL_CHANGE:
            if len(payload) != 1:
                return None
            return GroupLevelChange(target=target, level=payload[0])

        case ZenEventCode.SCENE_CHANGE:
            if len(payload) != 2:
                return None
            return SceneChange(
                target=target,
                scene=payload[0],
                active=bool(payload[1]),
            )

        case ZenEventCode.IS_OCCUPIED:
            # Wire: [instance, unused] - PDF example unused byte is 0x01.
            if len(payload) != 2:
                return None
            return IsOccupied(target=target, instance=payload[0])

        case ZenEventCode.SYSTEM_VARIABLE_CHANGE:
            if len(payload) != 5:
                return None
            if not 0 <= target < Const.MAX_SYSVAR:
                return None
            raw_value = int.from_bytes(payload[0:4], byteorder="big", signed=True)
            magnitude = int.from_bytes(payload[4:5], byteorder="big", signed=True)
            return SystemVariableChange(
                target=target,
                value=raw_value * (10**magnitude),
            )

        case ZenEventCode.COLOUR_CHANGE:
            # TC=3, RGB=4..RGBWAF=7, XY=5; padded forms up to 7
            if not 3 <= len(payload) <= 7:
                return None
            return ColourChange(target=target, colour=bytes(payload))

        case ZenEventCode.PROFILE_CHANGE:
            if len(payload) != 2:
                return None
            return ProfileChange(profile=(payload[0] << 8) | payload[1])

        case ZenEventCode.GROUP_OCCUPIED:
            if len(payload) != 2:
                return None
            return GroupOccupied(target=target, occupied=bool(payload[1]))

        case ZenEventCode.LEVEL_CHANGE_V2:
            if len(payload) != 2:
                return None
            return LevelChangeV2(
                target=target,
                current=payload[0],
                level=payload[1],
            )
