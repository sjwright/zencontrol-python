"""
API wire-facing models
======================

This module holds classes used to describe the contents of a controller,
e.g. DALI addresses, instances, and colour payloads.

A "ZenController" represents a controller's identity and configuration.
The interface layer subclasses it for its own entity state. "ControllerRef"
is the structural protocol both satisfy so the API layer never imports
from the interface layer.

A "ZenAddress" represents a single DALI address on a specific controller.
It could be an ECG, ECD, or Group address.

A "ZenInstance" represents a single instance of an specific ZenAddress.

A colour is exactly one of "ZenTcColour", "ZenXyColour", or "ZenRgbColour"
("ZenColour" is the union). Wire decoding lives in "colour_from_bytes".

"""

import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Protocol, Self

from .types import Const, ZenAddressType, ZenColourType, ZenInstanceType

DEFAULT_CONTROLLER_PORT = 5108


class ControllerRef(Protocol):
    """What the API layer needs from a controller (address + command path).

    Both "ZenController" and the interface-layer subclass satisfy this
    structurally - no upward import from "api" into "interface".
    """

    id: str
    name: str
    label: str
    host: str
    port: int

    @property
    def ip(self) -> str: ...

    def refresh_ip(self) -> str: ...


def mac_to_bytes(mac: str | None) -> bytes | None:
    """Parse a colon/hyphen MAC string to 6 bytes, or None."""
    if mac is None:
        return None
    try:
        raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    except ValueError as err:
        raise ValueError(f"Invalid MAC address {mac!r}") from err
    if len(raw) != 6:
        raise ValueError(f"MAC address must be 6 bytes, got {len(raw)} from {mac!r}")
    return raw


def mac_bytes_to_str(mac: bytes) -> str:
    """Format 6 MAC bytes as uppercase colon-separated hex."""
    return ":".join(f"{b:02X}" for b in mac)


def mac_key(mac: bytes | str) -> str:
    """Canonical MAC dict key: uppercase colon-separated."""
    if isinstance(mac, bytes):
        return mac_bytes_to_str(mac)
    return mac.upper().replace("-", ":")


@dataclass(frozen=True, slots=True)
class DiscoveredController:
    """A controller identified from multicast events (not yet registered).

    "label" is left None on the consumer path - enrich via an explicit probe
    that uses the command plane, never from the event receiver.

    "last_seen" updates on every subsequent identity packet so a discover
    window can report controllers heard again, not only first-ever sightings.
    """

    host: str
    mac: str
    label: str | None = None
    port: int = DEFAULT_CONTROLLER_PORT
    first_seen: float = 0.0
    last_seen: float = 0.0


@dataclass
class ZenController:
    """Controller identity and config - no transport back-references (I9).

    "zencontrol.ZenController" (the interface layer) subclasses this and adds
    entity state; that subclass is what "ZenControl.add_controller()" returns
    and what registered controllers always are. This base exists so the API
    layer can talk about controllers without importing the interface layer.

    Transports live outside the model: "ZenCommandClient" owns UDP clients
    keyed by controller name; "ZenControl" / "EntityContext" hold the
    command client and (for the high-level path) the event session.

    The 'host' field can be any resolvable hostname or IP address.
    The 'ip' property will resolve the hostname to an IP address and cache it.
    """
    id: str
    name: str
    label: str
    host: str
    port: int
    mac: str | None = None
    version: str | None = None
    startup_complete: bool = False
    dali_ready: bool = False
    filtering: bool = False
    last_seen: float = field(default_factory=time.time)
    _ip: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        mac_to_bytes(self.mac)  # eager validate; mac_bytes is derived

    @property
    def mac_bytes(self) -> bytes | None:
        """Wire MAC derived from "mac" - cannot desync from a stored copy."""
        return mac_to_bytes(self.mac)

    @property
    def ip(self) -> str:
        """Get the IPv4 address for the controller hostname, cache it as self._ip."
        
        This is a blocking (synchronous) call because the main thread needs it immediately.
        Ideally the value is pre-seeded from an async-safe resolve (e.g. resolve_host)
        and saved using set_resolved_ip().
        """
        if self._ip is None:
            from ..utils import resolve_host_sync
            self._ip = resolve_host_sync(self.host)
        return self._ip

    def set_resolved_ip(self, ip: str) -> None:
        """Seed the ip cache from an async-safe resolve (e.g. resolve_host)."""
        self._ip = ip

    def refresh_ip(self) -> str:
        """Demand a fresh DNS lookup."""
        self._ip = None
        return self.ip


@dataclass(slots=True)
class ZenAddress:
    """Represents a DALI address"""
    controller: ControllerRef
    type: ZenAddressType
    number: int
    label: str | None = field(default=None, init=False)
    serial: str | None = field(default=None, init=False)
    ean: int | None = field(default=None, init=False)

    @classmethod
    def broadcast(cls, controller: ControllerRef) -> Self:
        return cls(controller=controller, type=ZenAddressType.BROADCAST, number=255)
    
    def ecg(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.ECD: raise ValueError("Address is ECD, expected ECG")
        if self.type == ZenAddressType.GROUP: raise ValueError("Address is GROUP, expected ECG")
        if self.type == ZenAddressType.BROADCAST: raise ValueError("Address is BROADCAST, expected ECG")
        raise ValueError("Address type is unknown, expected ECG")
    
    def ecg_or_group(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.GROUP: return self.number+64
        if self.type == ZenAddressType.ECD: raise ValueError("Address is ECD, expected ECG or GROUP")
        if self.type == ZenAddressType.BROADCAST: raise ValueError("Address is BROADCAST, expected ECG or GROUP")
        raise ValueError("Address type is unknown, expected ECG or GROUP")
    
    def ecg_or_group_or_broadcast(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.GROUP: return self.number+64
        if self.type == ZenAddressType.BROADCAST: return 255
        if self.type == ZenAddressType.ECD: raise ValueError("Address is ECD, expected ECG or GROUP")
        raise ValueError("Address type is unknown, expected ECG, GROUP or BROADCAST")
    
    def ecg_or_ecd(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.ECD: return self.number+64
        if self.type == ZenAddressType.GROUP: raise ValueError("Address is GROUP, expected ECG or ECD")
        if self.type == ZenAddressType.BROADCAST: raise ValueError("Address is BROADCAST, expected ECG or ECD")
        raise ValueError("Address type is unknown, expected ECG or ECD")
    
    def ecg_or_ecd_or_broadcast(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.ECD: return self.number+64
        if self.type == ZenAddressType.BROADCAST: return 255
        if self.type == ZenAddressType.GROUP: raise ValueError("Address is GROUP, expected ECG or ECD or BROADCAST")
        raise ValueError("Address type is unknown, expected ECG, ECD or BROADCAST")
    
    def ecd(self) -> int:
        if self.type == ZenAddressType.ECD: return self.number+64
        if self.type == ZenAddressType.ECG: raise ValueError("Address is ECG, expected ECD")
        if self.type == ZenAddressType.GROUP: raise ValueError("Address is GROUP, expected ECD")
        if self.type == ZenAddressType.BROADCAST: raise ValueError("Address is BROADCAST, expected ECD")
        raise ValueError("Address type is unknown, expected ECD")
    
    def group(self) -> int:
        if self.type == ZenAddressType.GROUP: return self.number
        if self.type == ZenAddressType.ECG: raise ValueError("Address is ECG, expected GROUP")
        if self.type == ZenAddressType.ECD: raise ValueError("Address is ECD, expected GROUP")
        if self.type == ZenAddressType.BROADCAST: raise ValueError("Address is BROADCAST, expected GROUP")
        raise ValueError("Address type is unknown, expected GROUP")

    def entity_id_string(self) -> str:
        """Return a stable HA-friendly identifier for this address."""
        return f"{self.type.name.casefold()}{self.number}"
    
    def __post_init__(self) -> None:
        match self.type:
            case ZenAddressType.BROADCAST:
                if self.number != 255:
                    raise ValueError("Broadcast address must be 255")
            case ZenAddressType.ECG:
                if not (0 <= self.number <= 63):
                    raise ValueError(f"ECG address must be 0-63, got {self.number}")
            case ZenAddressType.ECD:
                if not (0 <= self.number <= 63):
                    raise ValueError(f"ECD address must be 0-63, got {self.number}")
            case ZenAddressType.GROUP:
                if not (0 <= self.number <= 15):
                    raise ValueError(f"Group address must be 0-15, got {self.number}")


def ecd_address_from_target(controller: ControllerRef, target: int) -> ZenAddress | None:
    """Decode an ECD event target (64–127) to a ZenAddress, or None if out of range."""
    number = target - 64
    if not 0 <= number <= 63:
        return None
    return ZenAddress(controller=controller, type=ZenAddressType.ECD, number=number)


def ecg_or_group_address_from_target(controller: ControllerRef, target: int) -> ZenAddress | None:
    """Decode an ECG (0–63) or group (64–79) event target to a ZenAddress."""
    if target <= 63:
        return ZenAddress(controller=controller, type=ZenAddressType.ECG, number=target)
    if 64 <= target <= 79:
        return ZenAddress(controller=controller, type=ZenAddressType.GROUP, number=target - 64)
    return None


@dataclass(slots=True)
class ZenInstance:
    """Represents a DALI ECD instance"""
    address: ZenAddress
    type: ZenInstanceType
    number: int
    active: bool | None = None
    error: bool | None = None
    def __post_init__(self) -> None:
        if not 0 <= self.number < Const.MAX_INSTANCE: 
            raise ValueError(f"Instance number must be between 0 and {Const.MAX_INSTANCE-1}, received {self.number}")

    def entity_id_string(self) -> str:
        """Return a stable HA-friendly identifier for this instance."""
        return f"{self.address.entity_id_string()}_{self.number}"


@dataclass(frozen=True, slots=True)
class ZenTcColour:
    """Tunable-white colour temperature in kelvin."""

    kelvin: int

    def __post_init__(self) -> None:
        if not Const.MIN_KELVIN <= self.kelvin <= Const.MAX_KELVIN:
            logging.getLogger(__name__).warning(
                "Kelvin %s out of range [%s, %s]; clamping",
                self.kelvin, Const.MIN_KELVIN, Const.MAX_KELVIN,
            )
            object.__setattr__(
                self, "kelvin",
                max(Const.MIN_KELVIN, min(Const.MAX_KELVIN, self.kelvin)),
            )

    def to_bytes(self) -> bytes:
        """Encode as returned by QUERY_DALI_COLOUR (no address or arc level)."""
        return struct.pack(">BH", ZenColourType.TC.value, self.kelvin)

    def command_payload(self) -> bytes:
        """Colour type and channel bytes for DALI_COLOUR (follows address and arc level)."""
        return self.to_bytes()


@dataclass(frozen=True, slots=True)
class ZenXyColour:
    """CIE XY chromaticity (0–65535 wire units)."""

    x: int
    y: int

    def __post_init__(self) -> None:
        if not 0 <= self.x <= 65535:
            raise ValueError(f"X must be between 0 and 65535, received {self.x}")
        if not 0 <= self.y <= 65535:
            raise ValueError(f"Y must be between 0 and 65535, received {self.y}")

    def to_bytes(self) -> bytes:
        """Encode as returned by QUERY_DALI_COLOUR (no address or arc level)."""
        return struct.pack(">BHH", ZenColourType.XY.value, self.x, self.y)

    def command_payload(self) -> bytes:
        """Colour type and channel bytes for DALI_COLOUR (follows address and arc level)."""
        return self.to_bytes()


@dataclass(frozen=True, slots=True)
class ZenRgbColour:
    """RGBWAF channel levels. Optional W/A/F stay None when the fixture lacks those channels."""

    r: int
    g: int
    b: int
    w: int | None = None
    a: int | None = None
    f: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.r <= 255:
            raise ValueError(f"R must be between 0 and 255, received {self.r}")
        if not 0 <= self.g <= 255:
            raise ValueError(f"G must be between 0 and 255, received {self.g}")
        if not 0 <= self.b <= 255:
            raise ValueError(f"B must be between 0 and 255, received {self.b}")
        if self.w is not None and not 0 <= self.w <= 255:
            raise ValueError(f"W must be between 0 and 255, received {self.w}")
        if self.a is not None and not 0 <= self.a <= 255:
            raise ValueError(f"A must be between 0 and 255, received {self.a}")
        if self.f is not None and not 0 <= self.f <= 255:
            raise ValueError(f"F must be between 0 and 255, received {self.f}")

    def to_bytes(self) -> bytes:
        """Encode as returned by QUERY_DALI_COLOUR (no address or arc level).

        Missing W/A/F channels encode as 0xFF (unused / no change).
        """
        return struct.pack(
            "BBBBBBB",
            ZenColourType.RGBWAF.value,
            self.r,
            self.g,
            self.b,
            self.w if self.w is not None else 0xFF,
            self.a if self.a is not None else 0xFF,
            self.f if self.f is not None else 0xFF,
        )

    def command_payload(self) -> bytes:
        """Colour type and channel bytes for DALI_COLOUR (follows address and arc level)."""
        return self.to_bytes()


ZenColour = ZenTcColour | ZenXyColour | ZenRgbColour


def colour_from_bytes(data: bytes) -> ZenColour | None:
    """Decode a DALI colour payload; None if the bytes are not a known colour."""
    match list(data):
        case [ZenColourType.RGBWAF.value, r, g, b, *rest] if len(rest) <= 3:
            # COLOUR_CHANGED_EVENT from a fixture with fewer than six channels
            # carries only channels + 1 bytes, so an RGB fixture sends
            # [0x80, R, G, B]. Channels the fixture does not have stay None.
            w, a, f = (list(rest) + [None, None, None])[:3]
            return ZenRgbColour(r=r, g=g, b=b, w=w, a=a, f=f)
        case [ZenColourType.TC.value, hi, lo] | [ZenColourType.TC.value, hi, lo, *_]:
            if len(data) not in (3, 7):
                return None
            return ZenTcColour(kelvin=(hi << 8) | lo)
        case [ZenColourType.XY.value, xh, xl, yh, yl] | [ZenColourType.XY.value, xh, xl, yh, yl, *_]:
            if len(data) not in (5, 7):
                return None
            return ZenXyColour(x=(xh << 8) | xl, y=(yh << 8) | yl)
        case _:
            return None

