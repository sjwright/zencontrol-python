"""
ZenControl API-level models.

This module contains models that belong to the zen_api layer:
- ZenController, ZenAddress, ZenInstance (API-level concepts)
- ZenColour, ZenProfile (API-level concepts used by TPI protocol)
- These are the core objects used by the TPI protocol
"""

import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from ..io import ZenClient
from .types import Const, ZenAddressType, ZenColourType, ZenInstanceType

if TYPE_CHECKING:
    # Addresses are only ever built from registered controllers, which are
    # interface-layer objects. Type-only import; at runtime the interface layer
    # imports this module, so importing it here would be circular.
    from ..interface.interface import ZenController as ZenInterfaceController


DEFAULT_CONTROLLER_PORT = 5108


@dataclass(frozen=True, slots=True)
class DiscoveredController:
    """A controller identified from multicast events (not yet registered)."""

    host: str
    mac: str
    label: str | None = None
    port: int = DEFAULT_CONTROLLER_PORT


@dataclass
class ZenController:
    """Base class holding a controller's config and transport state.

    ``zencontrol.ZenController`` (the interface layer) subclasses this and adds
    entity state; that subclass is what ``ZenControl.add_controller()`` returns
    and what registered controllers always are. This base exists so the API
    layer can talk about controllers without importing the interface layer.

    The 'host' field can be any resolvable hostname or IP address.
    The 'ip' property will resolve the hostname to an IP address and cache it.
    """
    id: str
    name: str
    label: str
    host: str
    port: int
    mac: str | None = None
    mac_bytes: bytes | None = field(init=False, default=None)
    # Any avoids an import cycle with protocol.py (which imports this module).
    protocol: Any | None = None
    version: str | None = None
    startup_complete: bool = False
    dali_ready: bool = False
    filtering: bool = False
    last_seen: float = field(default_factory=time.time)
    client: ZenClient | None = None
    _ip: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._update_mac_bytes(self.mac)

    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)
        # After init, keep mac_bytes in sync when mac is assigned.
        if name == "mac" and "mac_bytes" in self.__dict__:
            self._update_mac_bytes(value if isinstance(value, str) or value is None else None)

    def _update_mac_bytes(self, value: str | None) -> None:
        """Update mac_bytes from a MAC string (or clear it)."""
        if value is not None:
            try:
                mac_bytes = bytes.fromhex(value.replace(":", "").replace("-", ""))
            except ValueError as err:
                raise ValueError(f"Invalid MAC address {value!r}") from err
            if len(mac_bytes) != 6:
                raise ValueError(
                    f"MAC address must be 6 bytes, got {len(mac_bytes)} from {value!r}"
                )
            object.__setattr__(self, "mac_bytes", mac_bytes)
        else:
            object.__setattr__(self, "mac_bytes", None)
    
    @property
    def ip(self) -> str:
        """Get the resolved IP address from the host field.
        
        Resolves DNS names to IP addresses and caches the result.
        If resolution fails, returns the original host value.
        """
        if self._ip is None:
            try:
                # Try to resolve the hostname to an IP address
                self._ip = socket.gethostbyname(self.host)
            except (socket.gaierror, socket.herror):
                # If resolution fails, use the host value as-is (might already be an IP)
                self._ip = self.host
        return self._ip
    
    def refresh_ip(self) -> str:
        """Force a fresh DNS lookup and return the resolved IP address."""
        self._ip = None
        return self.ip


@dataclass(slots=True)
class ZenAddress:
    """Represents a DALI address"""
    controller: ZenInterfaceController
    type: ZenAddressType
    number: int
    label: str | None = field(default=None, init=False)
    serial: str | None = field(default=None, init=False)
    
    @classmethod
    def broadcast(cls, controller: ZenInterfaceController) -> Self:
        return cls(controller=controller, type=ZenAddressType.BROADCAST, number=255)
    
    def ecg(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        raise ValueError("Address is not a Control Gear")
    
    def ecg_or_group(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.GROUP: return self.number+64
        raise ValueError("Address is not a Control Gear or Group")
    
    def ecg_or_group_or_broadcast(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.GROUP: return self.number+64
        if self.type == ZenAddressType.BROADCAST: return 255
        raise ValueError("Address is not a Control Gear, Group or Broadcast")
    
    def ecg_or_ecd(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.ECD: return self.number+64
        raise ValueError("Address is not a Control Gear or Control Device")
    
    def ecg_or_ecd_or_broadcast(self) -> int:
        if self.type == ZenAddressType.ECG: return self.number
        if self.type == ZenAddressType.ECD: return self.number+64
        if self.type == ZenAddressType.BROADCAST: return 255
        raise ValueError("Address is not a Control Gear, Control Device or Broadcast")
    
    def ecd(self) -> int:
        if self.type == ZenAddressType.ECD: return self.number+64
        raise ValueError("Address is not a Control Device")
    
    def group(self) -> int:
        if self.type == ZenAddressType.GROUP: return self.number
        raise ValueError("Address is not a Group")

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


@dataclass(slots=True)
class ZenColour:
    """Represents a DALI color"""
    type: ZenColourType | None = None
    kelvin: int | None = None
    r: int | None = None
    g: int | None = None
    b: int | None = None
    w: int | None = None
    a: int | None = None
    f: int | None = None
    x: int | None = None
    y: int | None = None
    
    @classmethod
    def from_bytes(cls, data: bytes) -> Self | None:
        match list(data):
            case [ZenColourType.RGBWAF.value, r, g, b, *rest] if len(rest) <= 3:
                # COLOUR_CHANGED_EVENT from a fixture with fewer than six channels
                # carries only channels + 1 bytes, so an RGB fixture sends
                # [0x80, R, G, B]. Channels the fixture does not have stay None.
                w, a, f = (list(rest) + [None, None, None])[:3]
                return cls(type=ZenColourType.RGBWAF, r=r, g=g, b=b, w=w, a=a, f=f)
            case [ZenColourType.TC.value, hi, lo] | [ZenColourType.TC.value, hi, lo, *_]:
                if len(data) not in (3, 7):
                    return None
                return cls(type=ZenColourType.TC, kelvin=(hi << 8) | lo)
            case [ZenColourType.XY.value, xh, xl, yh, yl] | [ZenColourType.XY.value, xh, xl, yh, yl, *_]:
                if len(data) not in (5, 7):
                    return None
                return cls(type=ZenColourType.XY, x=(xh << 8) | xl, y=(yh << 8) | yl)
            case _:
                return None
    
    def __post_init__(self) -> None:
        match self.type:
            case ZenColourType.TC:
                kelvin = self.kelvin
                if kelvin is None:
                    raise ValueError("Kelvin is required for TC colour type")
                if not Const.MIN_KELVIN <= kelvin <= Const.MAX_KELVIN:
                    logging.getLogger(__name__).warning(
                        "Kelvin %s out of range [%s, %s]; clamping",
                        kelvin, Const.MIN_KELVIN, Const.MAX_KELVIN,
                    )
                    self.kelvin = max(Const.MIN_KELVIN, min(Const.MAX_KELVIN, kelvin))
            case ZenColourType.RGBWAF:
                r, g, b = self.r, self.g, self.b
                if r is None or not 0 <= r <= 255:
                    raise ValueError(f"R must be between 0 and 255, received {self.r}")
                if g is None or not 0 <= g <= 255:
                    raise ValueError(f"G must be between 0 and 255, received {self.g}")
                if b is None or not 0 <= b <= 255:
                    raise ValueError(f"B must be between 0 and 255, received {self.b}")
                if self.w is not None and not 0 <= self.w <= 255:
                    raise ValueError(f"W must be between 0 and 255, received {self.w}")
                if self.a is not None and not 0 <= self.a <= 255:
                    raise ValueError(f"A must be between 0 and 255, received {self.a}")
                if self.f is not None and not 0 <= self.f <= 255:
                    raise ValueError(f"F must be between 0 and 255, received {self.f}")
            case ZenColourType.XY:
                x, y = self.x, self.y
                if x is None or not 0 <= x <= 65535:
                    raise ValueError(f"X must be between 0 and 65535, received {self.x}")
                if y is None or not 0 <= y <= 65535:
                    raise ValueError(f"Y must be between 0 and 65535, received {self.y}")
            case _:
                pass
    
    def __repr__(self) -> str:
        match self.type:
            case ZenColourType.TC:
                return f"ZenColour(kelvin={self.kelvin})"
            case ZenColourType.RGBWAF:
                return f"ZenColour(r={self.r}, g={self.g}, b={self.b}, w={self.w}, a={self.a}, f={self.f})"
            case ZenColourType.XY:
                return f"ZenColour(x={self.x}, y={self.y})"
            case _:
                return f"ZenColour(type={self.type})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ZenColour):
            return NotImplemented
        return (
            self.type == other.type
            and self.kelvin == other.kelvin
            and self.r == other.r
            and self.g == other.g
            and self.b == other.b
            and self.w == other.w
            and self.a == other.a
            and self.f == other.f
            and self.x == other.x
            and self.y == other.y
        )
    
    def to_bytes(self, level: int = 255) -> bytes:
        """Encode colour data as returned by QUERY_DALI_COLOUR (no address or arc level)."""
        match self.type:
            case ZenColourType.TC:
                return struct.pack(">BH", 0x20, self.kelvin)
            case ZenColourType.RGBWAF:
                return struct.pack(
                    "BBBBBBB",
                    0x80,
                    self.r,
                    self.g,
                    self.b,
                    self.w if self.w is not None else 0,
                    self.a if self.a is not None else 0,
                    self.f if self.f is not None else 0,
                )
            case ZenColourType.XY:
                return struct.pack(">BHH", 0x10, self.x, self.y)
            case _:
                return b""

    def command_payload(self) -> bytes:
        """Colour type and channel bytes for DALI_COLOUR (follows address and arc level)."""
        return self.to_bytes()


@dataclass(slots=True)
class ZenProfile:
    """Represents a DALI profile"""
    controller: ZenController
    address: ZenAddress
    profile: int
    
    def __post_init__(self) -> None:
        if not (0 <= self.profile <= 255):
            raise ValueError(f"Profile must be 0-255, got {self.profile}")
