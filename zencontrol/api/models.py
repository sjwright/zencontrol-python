"""
ZenControl API-level models.

This module contains models that belong to the zen_api layer:
- ZenController, ZenAddress, ZenInstance (API-level concepts)
- ZenColour, ZenProfile (API-level concepts used by TPI protocol)
- These are the core objects used by the TPI protocol
"""

import struct
import time
from dataclasses import dataclass, field
from typing import Optional, Self, TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import ZenProtocol

from ..io import ZenClient
from .types import ZenAddressType, ZenInstanceType, ZenColourType, Const


@dataclass
class ZenController:
    """Represents a ZenControl controller"""
    id: str
    name: str
    label: str
    host: str
    port: int
    mac: str
    protocol: Optional["ZenProtocol"] = None
    version: Optional[str] = None
    startup_complete: bool = False
    dali_ready: bool = False
    filtering: bool = False
    last_seen: float = field(default_factory=time.time)
    client: Optional[ZenClient] = None


@dataclass
class ZenAddress:
    """Represents a DALI address"""
    controller: ZenController
    type: ZenAddressType
    number: int
    label: Optional[str] = field(default=None, init=False)
    serial: Optional[str] = field(default=None, init=False)
    
    @classmethod
    def broadcast(cls, controller: ZenController) -> Self:
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
    
    def __post_init__(self):
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


@dataclass
class ZenInstance:
    """Represents a DALI ECD instance"""
    address: ZenAddress
    type: ZenInstanceType
    number: int
    active: Optional[bool] = None
    error: Optional[bool] = None
    def __post_init__(self):
        if not 0 <= self.number < Const.MAX_INSTANCE: 
            raise ValueError(f"Instance number must be between 0 and {Const.MAX_INSTANCE-1}, received {self.number}")


@dataclass
class ZenColour:
    """Represents a DALI color"""
    type: ZenColourType = None
    kelvin: Optional[int] = None
    r: Optional[int] = None
    g: Optional[int] = None
    b: Optional[int] = None
    w: Optional[int] = None
    a: Optional[int] = None
    f: Optional[int] = None
    x: Optional[int] = None
    y: Optional[int] = None
    
    @classmethod
    def from_bytes(cls, bytes: bytes) -> Optional[Self]:
        if not bytes: # If bytes is empty, return None
            return None
        if bytes[0] == ZenColourType.RGBWAF.value and len(bytes) == 7:
            return cls(type=ZenColourType.RGBWAF, r=bytes[1], g=bytes[2], b=bytes[3], w=bytes[4], a=bytes[5], f=bytes[6])
        if bytes[0] == ZenColourType.TC.value and (len(bytes) == 3 or len(bytes) == 7):
            kelvin = (bytes[1] << 8) | bytes[2]
            return cls(type=ZenColourType.TC, kelvin=kelvin)
        if bytes[0] == ZenColourType.XY.value and (len(bytes) == 5 or len(bytes) == 7):
            x = (bytes[1] << 8) | bytes[2]
            y = (bytes[3] << 8) | bytes[4]
            return cls(type=ZenColourType.XY, x=x, y=y)
        return None
    
    def __post_init__(self):
        if self.type == ZenColourType.TC:
            if not Const.MIN_KELVIN <= self.kelvin <= Const.MAX_KELVIN:
                #raise ValueError(f"Kelvin must be between {Const.MIN_KELVIN} and {Const.MAX_KELVIN}, received {self.kelvin}")
                print(f"Kelvin must be between {Const.MIN_KELVIN} and {Const.MAX_KELVIN}, received {self.kelvin}")
                # set to the nearest valid value
                self.kelvin = max(Const.MIN_KELVIN, min(Const.MAX_KELVIN, self.kelvin))
                print(f"Setting to {self.kelvin} instead")
        if self.type == ZenColourType.RGBWAF:
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
        if self.type == ZenColourType.XY:
            if not 0 <= self.x <= 65535:
                raise ValueError(f"X must be between 0 and 65535, received {self.x}")
            if not 0 <= self.y <= 65535:
                raise ValueError(f"Y must be between 0 and 65535, received {self.y}")
    
    def __repr__(self) -> str:
        if self.type == ZenColourType.TC:
            return f"ZenColour(kelvin={self.kelvin})"
        if self.type == ZenColourType.RGBWAF:
            return f"ZenColour(r={self.r}, g={self.g}, b={self.b}, w={self.w}, a={self.a}, f={self.f})"
        if self.type == ZenColourType.XY:
            return f"ZenColour(x={self.x}, y={self.y})"
    
    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.__dict__ == other.__dict__
        else:
            return False
    
    def to_bytes(self, level: int = 255) -> bytes:
        if self.type == ZenColourType.TC:
            return struct.pack('>BBH', level, 0x20, self.kelvin)
        if self.type == ZenColourType.RGBWAF:
            return struct.pack('BBBBBBBB', level, 0x80, self.r, self.g, self.b, self.w if self.w is not None else 0, self.a if self.a is not None else 0, self.f if self.f is not None else 0)
        if self.type == ZenColourType.XY:
            return struct.pack('>BBHH', level, 0x10, self.x, self.y)
        return b''


@dataclass
class ZenProfile:
    """Represents a DALI profile"""
    controller: ZenController
    address: ZenAddress
    profile: int
    
    def __post_init__(self):
        if not (0 <= self.profile <= 255):
            raise ValueError(f"Profile must be 0-255, got {self.profile}")
