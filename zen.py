import socket
import struct
import time
import logging
from datetime import datetime as dt
from typing import Optional, Self, Callable
from enum import Enum
from threading import Thread, Event
from colorama import Fore, Back, Style
from dataclasses import dataclass, field

class ZenProtocol:
    pass

class Const:
    # UDP protocol
    MAGIC_BYTE = 0x04
    RESPONSE_TIMEOUT = 0.5

    # DALI limits
    MAX_ECG = 64 # 0-63
    MAX_ECD = 64 # 0-63
    MAX_INSTANCE = 32 # 0-31
    MAX_GROUP = 16 # 0-15
    MAX_SCENE = 12 # 0-11
    MAX_SYSVAR = 148 # 0-147
    MAX_LEVEL = 254 # 255 is mask value (i.e. no change)
    MIN_KELVIN = 1000
    MAX_KELVIN = 20000

    # Multicast
    MULTICAST_GROUP = "239.255.90.67"
    MULTICAST_PORT = 6969

    # Unicast
    UNICAST_PORT = 6969

class ZenError(Exception):
    """Base exception for Zen protocol errors"""
    pass

class ZenTimeoutError(ZenError):
    """Raised when a command times out"""
    pass

class ZenResponseError(ZenError):
    """Raised when receiving an invalid response"""
    pass

@dataclass
class ZenController:
    protocol: ZenProtocol
    name: str
    label: str
    host: str
    port: int = 5108
    mac: Optional[str] = None
    filtering: bool = False
    mac_bytes: bytes = field(init=False)
    connected: bool = False
    def __post_init__(self):
        if self.mac:
            self.mac_bytes = bytes.fromhex(self.mac.replace(':', ''))

class ZenAddressType(Enum):
    BROADCAST = 0
    ECG = 1
    ECD = 2
    GROUP = 3

@dataclass
class ZenAddress:
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
                self.number = 255
            case ZenAddressType.ECG:
                if not 0 <= self.number < Const.MAX_ECG: raise ValueError("Control Gear address must be between 0 and 63")
            case ZenAddressType.ECD:
                if not 0 <= self.number < Const.MAX_ECD: raise ValueError("Control Device address must be between 0 and 63")
            case ZenAddressType.GROUP:
                if not 0 <= self.number < Const.MAX_GROUP: raise ValueError("Group number must be between 0 and 15")
            case _:
                raise ValueError("Invalid address type")

class ZenErrorCode(Enum):
    CHECKSUM = 0x01           # Checksum Error
    SHORT_CIRCUIT = 0x02      # A short on the DALI line was detected
    RECEIVE_ERROR = 0x03      # Receive error
    UNKNOWN_CMD = 0x04        # The command in the request is unrecognised
    PAID_FEATURE = 0xB0       # The command requires a paid feature not purchased or enabled
    INVALID_ARGS = 0xB1       # Invalid arguments
    CMD_REFUSED = 0xB2        # The command couldn't be processed
    QUEUE_FAILURE = 0xB3      # A queue or buffer required to process the command is full or broken
    RESPONSE_UNAVAIL = 0xB4   # Some feature isn't available for some reason, refer to docs
    OTHER_DALI_ERROR = 0xB5   # The DALI related request couldn't be processed due to an error
    MAX_LIMIT = 0xB6          # A resource limit was reached on the controller
    UNEXPECTED_RESULT = 0xB7  # An unexpected result occurred
    UNKNOWN_TARGET = 0xB8     # Device doesn't exist

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
    
class ZenInstanceType(Enum):
    PUSH_BUTTON = 0x01         # Push button - generates short/long press events
    ABSOLUTE_INPUT = 0x02      # Absolute input (slider/dial) - generates integer values
    OCCUPANCY_SENSOR = 0x03    # Occupancy/motion sensor - generates occupied events
    LIGHT_SENSOR = 0x04        # Light sensor - events not currently forwarded
    GENERAL_SENSOR = 0x06      # General sensor (water flow, power etc) - events not currently forwarded

@dataclass
class ZenInstance:
    address: ZenAddress
    type: ZenInstanceType
    number: int
    active: Optional[bool] = None
    error: Optional[bool] = None
    def __post_init__(self):
        if not 0 <= self.number < Const.MAX_INSTANCE: raise ValueError("Instance number must be between 0 and 31")

class ZenColourType(Enum):
    XY = 0x10
    TC = 0x20
    RGBWAF = 0x80

@dataclass
class ZenColour:
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
        if bytes[0] == ZenColourType.RGBWAF.value and len(bytes) == 7:
            return cls(type=ZenColourType.RGBWAF, r=bytes[1], g=bytes[2], b=bytes[3], w=bytes[4], a=bytes[5], f=bytes[6])
        if bytes[0] == ZenColourType.TC.value and len(bytes) == 3:
            kelvin = (bytes[1] << 8) | bytes[2]
            return cls(type=ZenColourType.TC, kelvin=kelvin)
        if bytes[0] == ZenColourType.XY.value and len(bytes) == 5:
            x = (bytes[1] << 8) | bytes[2]
            y = (bytes[3] << 8) | bytes[4]
            return ZenColour(type=ZenColourType.XY, x=x, y=y)
        return None
    def __post_init__(self):
        if self.type == ZenColourType.TC:
            if not Const.MIN_KELVIN <= self.kelvin <= Const.MAX_KELVIN: raise ValueError(f"Kelvin must be between {Const.MIN_KELVIN} and {Const.MAX_KELVIN}")
        if self.type == ZenColourType.RGBWAF:
            if not 0 <= self.r <= 255: raise ValueError("R must be between 0 and 255")
            if not 0 <= self.g <= 255: raise ValueError("G must be between 0 and 255")
            if not 0 <= self.b <= 255: raise ValueError("B must be between 0 and 255")
            if self.w is not None and not 0 <= self.w <= 255: raise ValueError("W must be between 0 and 255")
            if self.a is not None and not 0 <= self.a <= 255: raise ValueError("A must be between 0 and 255")
            if self.f is not None and not 0 <= self.f <= 255: raise ValueError("F must be between 0 and 255")
        if self.type == ZenColourType.XY:
            if not 0 <= self.x <= 65535: raise ValueError("X must be between 0 and 65535")
            if not 0 <= self.y <= 65535: raise ValueError("Y must be between 0 and 65535")
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
    def to_bytes(self, level: int = 255) ->bytes:
        if self.type == ZenColourType.TC:
            return struct.pack('>BBH', level, 0x20, self.kelvin)
        if self.type == ZenColourType.RGBWAF:
            return struct.pack('BBBBBBBBB', level, 0x80, self.r, self.g, self.b, self.w if not None else 0, self.a if not None else 0, self.f if not None else 0)
        if self.type == ZenColourType.XY:
            return struct.pack('>BBHH', level, 0x10, self.x, self.y)
        return b''

@dataclass()
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
            profile_change = True
        )
    @classmethod
    def from_upper_lower(cls, upper: int, lower: int) -> Self:
        return cls.from_double_byte((upper << 8) | lower)
    @classmethod
    def from_double_byte(cls, event_mask: int) -> Self:
        return cls(
            button_press = (event_mask & (1 << 0)) != 0,
            button_hold = (event_mask & (1 << 1)) != 0,
            absolute_input = (event_mask & (1 << 2)) != 0,
            level_change = (event_mask & (1 << 3)) != 0,
            group_level_change = (event_mask & (1 << 4)) != 0,
            scene_change = (event_mask & (1 << 5)) != 0,
            is_occupied = (event_mask & (1 << 6)) != 0,
            system_variable_change = (event_mask & (1 << 7)) != 0,
            colour_change = (event_mask & (1 << 8)) != 0,
            profile_change = (event_mask & (1 << 9)) != 0
        )
    def bitmask(self) -> int:
        event_mask = 0x00
        if self.button_press: event_mask |= (1 << 0)
        if self.button_hold: event_mask |= (1 << 1)
        if self.absolute_input: event_mask |= (1 << 2)
        if self.level_change: event_mask |= (1 << 3)
        if self.group_level_change: event_mask |= (1 << 4)
        if self.scene_change: event_mask |= (1 << 5)
        if self.is_occupied: event_mask |= (1 << 6)
        if self.system_variable_change: event_mask |= (1 << 7)
        if self.colour_change: event_mask |= (1 << 8)
        if self.profile_change: event_mask |= (1 << 9)
        return event_mask
    def upper(self) -> int:
        return (self.bitmask() >> 8) & 0xFF
    def lower(self) -> int:
        return self.bitmask() & 0xFF

class ZenProtocol:

    # Define commands as a dictionary
    CMD: dict[str, int] = {
        # Controller
        "QUERY_CONTROLLER_VERSION_NUMBER": 0x1C,    # Query ZenController Version Number
        "QUERY_CONTROLLER_LABEL": 0x24,             # Query the label of the controller
        "QUERY_CONTROLLER_FITTING_NUMBER": 0x25,    # Query the fitting number of the controller itself
        "QUERY_CONTROLLER_STARTUP_COMPLETE": 0x27,  # Query whether controller startup is complete
        "QUERY_IS_DALI_READY": 0x26,                # Query whether DALI bus is ready (or has a fault)
        # System variables
        "SET_SYSTEM_VARIABLE": 0x36,                # Set a system variable value
        "QUERY_SYSTEM_VARIABLE": 0x37,              # Query system variable
        "QUERY_SYSTEM_VARIABLE_NAME": 0x42,         # Query the name of a system variable
        # TPI settings
        "ENABLE_TPI_EVENT_EMIT": 0x08,              # Enable or disable TPI Events
        "QUERY_TPI_EVENT_EMIT_STATE": 0x07,         # Query whether TPI Events are enabled or disabled
        "DALI_ADD_TPI_EVENT_FILTER": 0x31,          # Request that filters be added for DALI TPI Events
        "QUERY_DALI_TPI_EVENT_FILTERS": 0x32,       # Query DALI TPI Event filters on a address
        "DALI_CLEAR_TPI_EVENT_FILTERS": 0x33,       # Request that DALI TPI Event filters be cleared
        "SET_TPI_EVENT_UNICAST_ADDRESS": 0x40,      # Set a TPI Events unicast address and port
        "QUERY_TPI_EVENT_UNICAST_ADDRESS": 0x41,    # Query TPI Events State, unicast address and port
        # Any address
        "QUERY_OPERATING_MODE_BY_ADDRESS": 0x28,    # Query the operating mode for a device
        "QUERY_DALI_DEVICE_LABEL": 0x03,            # Query the label for a DALI ECD or ECG by address
        "QUERY_DALI_SERIAL": 0xB9,                  # Query the Serial Number at a address
        "QUERY_DALI_FITTING_NUMBER": 0x22,          # Query the fitting number for control gear/devices
        "QUERY_DALI_EAN": 0xB8,                     # Query the DALI European Article Number at an address
        # Groups / Group-scenes
        "QUERY_GROUP_MEMBERSHIP_BY_ADDRESS": 0x15,  # Query DALI Group membership by address
        "QUERY_GROUP_NUMBERS": 0x09,                # Query the DALI Group numbers
        "QUERY_GROUP_LABEL": 0x01,                  # Query the label for a DALI Group by Group Number
        "QUERY_SCENE_NUMBERS_FOR_GROUP": 0x1A,      # Query Scene Numbers attributed to a group
        "QUERY_SCENE_LABEL_FOR_GROUP": 0x1B,        # Query Scene Labels attributed to a group scene
        "QUERY_GROUP_BY_NUMBER": 0x12,              # Query DALI Group information by Group Number
        # Profiles
        "QUERY_PROFILE_INFORMATION": 0x43,          # Query profile numbers, behaviours etc
        "QUERY_PROFILE_NUMBERS": 0x0B,              # Query all available Profile numbers (superseded by QUERY_PROFILE_INFORMATION)
        "QUERY_PROFILE_LABEL": 0x04,                # Query the label for a controller profile
        "QUERY_CURRENT_PROFILE_NUMBER": 0x05,       # Query the current profile number
        "CHANGE_PROFILE_NUMBER": 0xC0,              # Request a Profile Change on the controller
        # Instances
        "QUERY_DALI_ADDRESSES_WITH_INSTANCES": 0x16, # Query DALI addresses that have instances
        "QUERY_INSTANCES_BY_ADDRESS": 0x0D,         # Query information of instances
        "QUERY_DALI_INSTANCE_FITTING_NUMBER": 0x23, # Query the fitting number for an instance
        "QUERY_DALI_INSTANCE_LABEL": 0xB7,          # Query DALI Instance for its label
        "QUERY_INSTANCE_GROUPS": 0x21,              # Query group targets related to an instance
        "QUERY_OCCUPANCY_INSTANCE_TIMERS": 0x0C,    # Query an occupancy instance for its timer values
        # ECG (Lights)
        "QUERY_CONTROL_GEAR_DALI_ADDRESSES": 0x1D,  # Query Control Gear present in database
        "DALI_QUERY_LEVEL": 0xAA,                   # Query the the level on a address
        "DALI_QUERY_CG_TYPE": 0xAC,                 # Query Control Gear type data on a address
        "QUERY_DALI_COLOUR_FEATURES": 0x35,         # Query the DALI colour features/capabilities
        "QUERY_DALI_COLOUR_TEMP_LIMITS": 0x38,      # Query Colour Temperature max/min + step in Kelvin
        "DALI_QUERY_CONTROL_GEAR_STATUS": 0xAB,     # Query status data on a address, group or broadcast
        "QUERY_DALI_COLOUR": 0x34,                  # Query the Colour information on a DALI target
        "DALI_COLOUR": 0x0E,                        # Set a DALI target to a colour
        "DALI_INHIBIT": 0xA0,                       # Inhibit sensors from affecting a target for n seconds
        "DALI_ARC_LEVEL": 0xA2,                     # Set an Arc-Level on a address
        "DALI_ON_STEP_UP": 0xA3,                    # On-if-Off and Step Up on a address
        "DALI_STEP_DOWN_OFF": 0xA4,                 # Step Down and off-at-min on a address
        "DALI_UP": 0xA5,                            # Step Up on a address
        "DALI_DOWN": 0xA6,                          # Step Down on a address
        "DALI_RECALL_MAX": 0xA7,                    # Recall the max level on a address
        "DALI_RECALL_MIN": 0xA8,                    # Recall the min level on a address
        "DALI_OFF": 0xA9,                           # Set a address to Off
        "DALI_QUERY_MIN_LEVEL": 0xAF,               # Query the min level for a DALI device
        "DALI_QUERY_MAX_LEVEL": 0xB0,               # Query the max level for a DALI device
        "DALI_QUERY_FADE_RUNNING": 0xB1,            # Query whether a fade is running on a address
        "DALI_ENABLE_DAPC_SEQ": 0xB2,               # Begin a DALI DAPC sequence
        "DALI_CUSTOM_FADE": 0xB4,                   # Call a DALI Arc Level with a custom fade-length
        "DALI_GO_TO_LAST_ACTIVE_LEVEL": 0xB5,       # Command DALI addresses to go to last active level
        "DALI_STOP_FADE": 0xC1,                     # Request a running DALI fade be stopped
        # Scenes
        "QUERY_SCENE_NUMBERS_BY_ADDRESS": 0x14,     # Query for DALI Scenes an address has levels for
        "QUERY_SCENE_LEVELS_BY_ADDRESS": 0x1E,      # Query Scene level values for a given address
        "DALI_SCENE": 0xA1,                         # Call a DALI Scene on a address
        "DALI_QUERY_LAST_SCENE": 0xAD,              # Query Last heard DALI Scene
        "DALI_QUERY_LAST_SCENE_IS_CURRENT": 0xAE,   # Query if last heard Scene is current scene

        # Implemented but not tested
        "OVERRIDE_DALI_BUTTON_LED_STATE": 0x29,     # Override a button LED state
        "QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE": 0x30, # Query button last known button LED state

        # Won't implement (because I can't test)
        "TRIGGER_SDDP_IDENTIFY": 0x06,              # Trigger a Control4 SDDP Identify
        "DMX_COLOUR": 0x10,                         # Send values to a set of DMX channels and configure fading
        "QUERY_DMX_DEVICE_NUMBERS": 0x17,           # Query DMX Device information
        "QUERY_DMX_DEVICE_BY_NUMBER": 0x18,         # Query for DMX Device information by channel number
        "QUERY_DMX_LEVEL_BY_CHANNEL": 0x19,         # Query DMX Channel value by Channel number
        "QUERY_DMX_DEVICE_LABEL_BY_NUMBER": 0x20,   # Query DMX Device for its label
        "VIRTUAL_INSTANCE": 0xB3,                   # Perform an action on a Virtual Instance
        "QUERY_VIRTUAL_INSTANCES": 0xB6,            # Query for virtual instances and their types

        # Deprecated (described as a legacy command in docs)
        "QUERY_SCENE_LABEL": 0x02,
        "QUERY_SCENE_NUMBERS": 0x0A,
        "QUERY_SCENE_BY_NUMBER": 0x13,
    }

    def __init__(self,
                 logger: logging.Logger=None,
                 narration: bool = False,
                 unicast: bool = False,
                 listen_ip: Optional[str] = None,
                 listen_port: Optional[int] = None
                 ):
        self.logger = logger
        self.narration = narration
        self.unicast = unicast
        self.listen_ip = (listen_ip if listen_ip else "0.0.0.0") if unicast else None
        self.listen_port = (listen_port if listen_port else Const.UNICAST_PORT) if unicast else None
        
        # If unicast, and we're binding to 0.0.0.0, we still need to know our actual IP address
        self.local_ip = (socket.gethostbyname(socket.gethostname()) if self.listen_ip == "0.0.0.0" else self.listen_ip) if self.unicast else None

        # Log to null if no logger provided
        if not self.logger:
            self.logger = logging.getLogger('null')
            self.logger.addHandler(logging.NullHandler())

        # Setup socket for sending/receiving commands
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.command_socket.settimeout(Const.RESPONSE_TIMEOUT)
        
        # Setup event monitoring
        self.event_socket = None
        self.event_thread = None
        self.stop_event = Event()
        
        # Setup event listeners
        self.button_press_callback: Optional[Callable] = None
        self.button_hold_callback: Optional[Callable] = None
        self.absolute_input_callback: Optional[Callable] = None
        self.level_change_callback: Optional[Callable] = None
        self.group_level_change_callback: Optional[Callable] = None
        self.scene_change_callback: Optional[Callable] = None
        self.is_occupied_callback: Optional[Callable] = None
        self.colour_change_callback: Optional[Callable] = None
        self.profile_change_callback: Optional[Callable] = None
        self.system_variable_change_callback: Optional[Callable] = None

    def __del__(self):
        """Cleanup when object is destroyed"""
        self.stop_event_monitoring()
        self.command_socket.close()

    def set_controllers(self, controllers: list[ZenController]):
        self.controllers = controllers # Used to match events to controllers, and include controller objects in callbacks

    # ============================
    # PACKET SENDING
    # ============================

    @staticmethod
    def _checksum(packet: list[int]) -> int:
        acc = 0x00
        for d in packet:
            acc = d ^ acc
        return acc

    def _send_basic(self,
                   controller: ZenController,
                   command: int,
                   address: int = 0x00,
                   data: list[int] = [0x00, 0x00, 0x00], 
                   return_type: str = 'bytes') -> Optional[bytes | str | list[int] | int | bool]:
        if len(data) > 3: 
            raise ValueError("data must be 0-3 bytes")
        data = data + [0x00] * (3 - len(data))  # Pad data to 3 bytes
        response_data, response_code = self._send_packet_retry_on_timeout(controller, command, [address] + data)
        if response_data is None and response_code is None:
            return None
        match response_code:
            case 0xA0: # OK
                match return_type:
                    case 'ok':
                        return True
                    case _:
                        raise ValueError(f"Invalid return_type '{return_type}' for response code {response_code}")
            case 0xA1: # ANSWER
                match return_type:
                    case 'bytes':
                        return response_data
                    case 'str':
                        try:
                            return response_data.decode('ascii')
                        except UnicodeDecodeError:
                            return None
                    case 'list':
                        if response_data: return list(response_data)
                    case 'int':
                        if response_data and len(response_data) == 1: return int(response_data[0])
                    case 'bool':
                        if response_data and len(response_data) == 1: return bool(response_data[0])
                    case _:
                        raise ValueError(f"Invalid return_type '{return_type}' for response code {response_code}")
            case 0xA2: # NO_ANSWER
                match return_type:
                    case 'ok':
                        return False
                    case _:
                        return None
            case 0xA3: # ERROR
                if response_data:
                    error_code = ZenErrorCode(response_data[0]) if response_data[0] in ZenErrorCode else None
                    error_label = error_code.name if error_code else f"Unknown error code: {hex(response_data[0])}"
                    if self.narration: print(f"Command error code: {error_label}")
                else:
                    if self.narration: print("Command error (no error code)")
            case _:
                if self.narration: print(f"Unknown response code: {response_code}")
        return None
        
    def _send_colour(self, controller: ZenController, command: int, address: int, colour: ZenColour, level: int = 255) -> Optional[bool]:
        """Send a DALI colour command."""
        response_data, response_code = self._send_packet_retry_on_timeout(controller, command, [address] + list(colour.to_bytes(level)))
        match response_code:
            case 0xA0: # OK
                return True
            case 0xA2: # NO_ANSWER
                return False
        return None

    def _send_dynamic(self, controller: ZenController, command: int, data: list[int]) -> Optional[bytes]:
        # Calculate data length and prepend it to data
        response_data, response_code = self._send_packet_retry_on_timeout(controller, command, [len(data)] + data)
        # Check response type
        match response_code:
            case 0xA0: # OK
                pass  # Request processed successfully
            case 0xA1: # ANSWER
                pass  # Answer is in data bytes
            case 0xA2: # NO_ANSWER
                if response_data > 0:
                    if self.narration: print(f"No answer with code: {response_data}")
                return None
            case 0xA3: # ERROR
                if response_data:
                    error_code = ZenErrorCode(response_data[0]) if response_data[0] in ZenErrorCode else None
                    error_label = error_code.name if error_code else f"Unknown error code: {hex(response_data[0])}"
                    if self.narration: print(f"Command error code: {error_label}")
                else:
                    if self.narration: print("Command error (no error code)")
                return None
            case _:
                if self.narration: print(f"Unknown response type: {response_code}")
                return None
        if response_data:
            return response_data
        return None
    
    def _send_packet_retry_on_timeout(self, controller: ZenController, command: int, data: list[int]) -> tuple[Optional[bytes], int]:
        for _ in range(3):
            if _ > 0:
                self.logger.error(f"Trying again...")
            try:
                return self._send_packet(controller, command, data)
            except ZenTimeoutError:
                pass
            controller.connected = False
        return None, None
    
    def _send_packet(self, controller: ZenController, command: int, data: list[int]) -> tuple[Optional[bytes], int]:
        # Acquire lock to ensure serial execution
        if not hasattr(self, '_send_lock'):
            self._send_lock = False
            
        # Wait up to 1 second for lock
        start_time = time.time()
        while self._send_lock:
            if time.time() - start_time > 1.0:
                self.logger.error(f"Timeout waiting for lock")
                raise ZenTimeoutError(f"No response from {controller.host}:{controller.port}")
            time.sleep(0.1)
            
        self._send_lock = True

        try:
            # Maintain sequence counter
            self._sequence_counter = (self._sequence_counter + 1) % 256 if hasattr(self, '_sequence_counter') else 0
            
            # Construct packet with checksum
            packet = [Const.MAGIC_BYTE, self._sequence_counter, command] + data
            checksum = self._checksum(packet)
            complete_packet = bytes(packet + [checksum])
            
            # Command_socket is safe for multiple controllers because the server host/port is specified each time.
            # The client port is arbitrarly assigned once and reused for every packet to every controller.
            self.logger.debug(f"UDP packet sent to {controller.host}:{controller.port}: [{', '.join(f'0x{b:02x}' for b in complete_packet)}]")
            self.command_socket.sendto(complete_packet, (controller.host, controller.port))
            _recv_start = time.time()
            response, addr = self.command_socket.recvfrom(1024)
            _recv_msec = (time.time() - _recv_start) * 1000
            self.logger.debug(f"UDP response from {controller.host}:{controller.port}: [{', '.join(f'0x{b:02x}' for b in response)}]")
            if self.narration: print(Fore.MAGENTA + f"SEND: [{', '.join(f'0x{b:02x}' for b in complete_packet)}]  "
                                        + Fore.WHITE + Style.DIM + f"RTT: {_recv_msec:.0f}ms".ljust(10)
                                        + Style.BRIGHT + Fore.CYAN + f"  RECV: [{', '.join(f'0x{b:02x}' for b in response)}]"
                                        + Style.RESET_ALL)

            # Verify response format and sequence counter
            if len(response) < 4:  # Minimum valid response is 4 bytes
                self.logger.error(f"UDP response too short (len={len(response)})")
                raise ZenResponseError(f"Invalid response format from {controller.host}:{controller.port}")
                
            response_type = response[0]
            sequence = response[1]
            data_length = response[2]
            
            # Verify sequence counter matches
            if sequence != self._sequence_counter:
                self.logger.error(f"UDP response sequence counter mismatch (expected {self._sequence_counter}, got {sequence})")
                raise ZenResponseError(f"Sequence mismatch from {controller.host}:{controller.port}")
                
            # Verify total packet length matches data_length
            expected_length = 4 + data_length  # type + seq + len + data + checksum
            if len(response) != expected_length:
                self.logger.error(f"UDP response length mismatch (expected {expected_length}, got {len(response)})")
                raise ZenResponseError(f"Length mismatch from {controller.host}:{controller.port}")
            
            # Return data bytes if present, otherwise None
            if data_length > 0:
                return response[3:3+data_length], response_type
            return None, response_type
        
        except socket.timeout:
            self.logger.error(f"UDP packet response from {controller.host}:{controller.port} not received in time, probably offline")
            raise ZenTimeoutError(f"No response from {controller.host}:{controller.port} in time")
        except Exception as e:
            self.logger.error(f"UDP packet error sending command: {e}")
            raise ZenError(f"Error sending command: {e}")
            
        finally:
            # Always release lock when done
            self._send_lock = False

    # ============================
    # EVENT LISTENING
    # ============================

    def set_callbacks(self,
                            button_press_callback: Optional[Callable] = None,
                            button_hold_callback: Optional[Callable] = None,
                            absolute_input_callback: Optional[Callable] = None,
                            level_change_callback: Optional[Callable] = None, 
                            group_level_change_callback: Optional[Callable] = None,
                            scene_change_callback: Optional[Callable] = None,
                            is_occupied_callback: Optional[Callable] = None,
                            colour_change_callback: Optional[Callable] = None,
                            profile_change_callback: Optional[Callable] = None,
                            system_variable_change_callback: Optional[Callable] = None
                            ):
        self.button_press_callback = button_press_callback
        self.button_hold_callback = button_hold_callback
        self.absolute_input_callback = absolute_input_callback
        self.level_change_callback = level_change_callback
        self.group_level_change_callback = group_level_change_callback
        self.scene_change_callback = scene_change_callback
        self.is_occupied_callback = is_occupied_callback
        self.colour_change_callback = colour_change_callback
        self.profile_change_callback = profile_change_callback
        self.system_variable_change_callback = system_variable_change_callback
        
    def start_event_monitoring(self):
        if self.event_thread and self.event_thread.is_alive():
            if self.narration: print("Event monitoring already running")
            return
        
        # For the sake of our sanity, all controllers must send event packets in the same way: either multicast or unicast (on one port)
        for controller in self.controllers:
            self.set_tpi_event_unicast_address(controller, ipaddr=self.local_ip if self.unicast else None, port=self.listen_port if self.unicast else None)
            self.tpi_event_emit(controller, ZenEventMode(enabled=True, filtering=controller.filtering, unicast=self.unicast, multicast=not self.unicast))
        
        self.stop_event.clear()
        self.event_thread = Thread(target=self._event_listener)
        self.event_thread.daemon = True
        self.event_thread.start()

    def stop_event_monitoring(self):
        """Stop listening for multicast events"""
        self.stop_event.set()
        if self.event_thread:
            self.event_thread.join()
        if self.event_socket:
            self.event_socket.close()
            self.event_socket = None

    def _event_listener(self):
        """Internal method to handle multicast event listening"""
        try:
            self.event_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.event_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if self.unicast:
                # Bind to unicast socket
                self.event_socket.bind((self.listen_ip, self.listen_port))
            else:
                # Bind to multicast socket
                self.event_socket.bind(('', Const.MULTICAST_PORT))
                group = socket.inet_aton(Const.MULTICAST_GROUP)
                mreq = struct.pack('4sl', group, socket.INADDR_ANY)
                self.event_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            while not self.stop_event.is_set():
                data, ip_address = self.event_socket.recvfrom(1024)
                
                # Logging string
                typecast = "unicast" if self.unicast else "multicast"

                self.logger.debug(f"Received {typecast} from {ip_address}: [{', '.join(f'0x{b:02x}' for b in data)}]")
                if self.narration: print(Fore.MAGENTA + f"{typecast.upper()} FROM: {ip_address}" + Fore.CYAN + f"  RECV: [{', '.join(f'0x{b:02x}' for b in data)}]" + Style.RESET_ALL)
                
                # Drop packet if it doesn't match the expected structure
                if len(data) < 2 or data[0:2] != bytes([0x5a, 0x43]):
                    self.logger.debug(f"Received {typecast} invalid packet: {ip_address} - {', '.join(f'0x{b:02x}' for b in data)}")
                    if self.narration: print(f"Received {typecast} invalid packet: {ip_address} - {', '.join(f'0x{b:02x}' for b in data)}")
                    return

                # Extract packet fields
                macbytes = bytes.fromhex(data[2:8].hex())
                mac_address = ':'.join(f'{b:02x}' for b in data[2:8])
                target = int.from_bytes(data[8:10], byteorder='big')
                event_code = data[10]
                payload_len = data[11]
                payload = data[12:-1]
                received_checksum = data[-1]

                self.logger.debug(f" ... IP: {ip_address} - MAC: {mac_address} - EVENT: {event_code} - TARGET: {target} - PAYLOAD: {payload}")
                if self.narration: print(Fore.CYAN + Style.DIM + f"         IP: {ip_address} - MAC: {mac_address} - EVENT: {event_code} - TARGET: {target} - PAYLOAD: {payload}" + Style.RESET_ALL)
                
                # Find controller where macbytes matches mac_address
                controller = next((c for c in self.controllers if c.mac_bytes == macbytes), None)

                # If no controller found, skip event
                if not controller:
                    self.logger.info(f"{typecast.capitalize()} packet received from unknown controller")
                    continue

                # Verify data length
                if len(payload) != payload_len:
                    self.logger.error(f"{typecast.capitalize()} packet has invalid payload length: {len(payload)} != {payload_len}")
                    continue
                
                # Verify checksum
                calculated_checksum = self._checksum(list(data[:-1]))
                if received_checksum != calculated_checksum:
                    self.logger.error(f"{typecast.capitalize()} packet has invalid checksum: {calculated_checksum} != {received_checksum}")
                    continue
                
                match event_code:
                    case 0x00: # Button Press - Button has been pressed
                        if self.button_press_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.ECD, number=target-64)
                            instance = ZenInstance(address=address, type=ZenInstanceType.PUSH_BUTTON, number=payload[0])
                            self.button_press_callback(instance=instance, payload=payload)

                    case 0x01: # Button Hold - Button has been pressed and is being held down
                        if self.button_hold_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.ECD, number=target-64)
                            instance = ZenInstance(address=address, type=ZenInstanceType.PUSH_BUTTON, number=payload[0])
                            self.button_hold_callback(instance=instance, payload=payload)

                    case 0x02: # Absolute Input - Absolute input has changed
                        if self.absolute_input_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.ECD, number=target-64)
                            instance = ZenInstance(address=address, type=ZenInstanceType.PUSH_BUTTON, number=payload[0])
                            self.absolute_input_callback(instance=instance, payload=payload)

                    case 0x03: # Level Change - Arc Level on an Address target has changed
                        if self.level_change_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.ECG, number=target)
                            self.level_change_callback(address=address, arc_level=payload[0], payload=payload)

                    case 0x04: # Group Level Change - Arc Level on a Group target has changed
                        if self.group_level_change_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.GROUP, number=target)
                            self.group_level_change_callback(address=address, arc_level=payload[0], payload=payload)

                    case 0x05: # Scene Change - Scene has been recalled
                        if self.scene_change_callback:
                            if target <= 63:
                                address = ZenAddress(controller=controller, type=ZenAddressType.ECG, number=target)
                            elif 64 <= target <= 79:
                                address = ZenAddress(controller=controller, type=ZenAddressType.GROUP, number=target-64)
                            else:
                                self.logger.error(f"Invalid scene change event target: {target}")
                                continue
                            self.scene_change_callback(address=address, scene=payload[0], payload=payload)
                        
                    case 0x06: # Is Occupied - An occupancy sensor has been triggered, area is occupied
                        if self.is_occupied_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.ECD, number=target-64)
                            instance = ZenInstance(address=address, type=ZenInstanceType.OCCUPANCY_SENSOR, number=payload[0])
                            self.is_occupied_callback(instance=instance, payload=payload)
                        
                    case 0x07: # System Variable Change - A system variable has changed

                        if not 0 <= target < Const.MAX_SYSVAR:
                            self.logger.error(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {target}")
                            continue
                        if self.system_variable_change_callback:
                            raw_value = int.from_bytes(payload[0:4], byteorder='big', signed=True)
                            magnitude = int.from_bytes([payload[4]], byteorder='big', signed=True)
                            value = raw_value * (10 ** magnitude)
                            self.system_variable_change_callback(controller=controller, target=target, value=value, payload=payload)

                    case 0x08: # Colour Change - A Tc, RGBWAF or XY colour change has occurred
                        if self.colour_change_callback:
                            address = ZenAddress(controller=controller, type=ZenAddressType.ECG if target < 64 else ZenAddressType.GROUP, number=target)
                            colour = ZenColour.from_bytes(payload)
                            self.colour_change_callback(address=address, colour=colour, payload=payload)
                                
                    case 0x09: # Profile Change - The active profile on the controller has changed
                        if self.profile_change_callback:
                            payload_int = int.from_bytes(payload, byteorder='big')
                            self.profile_change_callback(controller=controller, profile=payload_int, payload=payload)
                
        except Exception as e:
            if self.narration: print(f"Event listener error: {e}")
            raise
        finally:
            print(f"Event listener no longer listening!")
            if self.event_socket:
                self.event_socket.close()

    # ============================
    # API COMMANDS
    # ============================

    def query_group_label(self, address: ZenAddress, generic_if_none: bool=False) -> Optional[str]:
        """Get the label for a DALI Group. Returns a string, or None if no label is set."""
        label = self._send_basic(address.controller, self.CMD["QUERY_GROUP_LABEL"], address.group(), return_type='str')
        if label is None and generic_if_none: return f"Group {address.number}"
        return label;
    
    def query_dali_device_label(self, address: ZenAddress, generic_if_none: bool=False) -> Optional[str]:
        """Query the label for a DALI device (control gear or control device). Returns a string, or None if no label is set."""
        label = self._send_basic(address.controller, self.CMD["QUERY_DALI_DEVICE_LABEL"], address.ecg_or_ecd(), return_type='str')
        if label is None and generic_if_none: label = f"{address.controller.label} ECD {address.number}"
        return label
        
    def query_profile_label(self, controller: ZenController, profile: int) -> Optional[str]:
        """Get the label for a Profile number (0-65535). Returns a string if a label exists, else None."""
        # Profile numbers are 2 bytes long, so check valid range
        if not 0 <= profile <= 65535:
            raise ValueError("Profile number must be between 0 and 65535")
        # Split profile number into upper and lower bytes
        profile_upper = (profile >> 8) & 0xFF
        profile_lower = profile & 0xFF
        # Send request
        return self._send_basic(controller, self.CMD["QUERY_PROFILE_LABEL"], 0x00, [0x00, profile_upper, profile_lower], return_type='str')
    
    def query_current_profile_number(self, controller: ZenController) -> Optional[int]:
        """Get the current/active Profile number for a controller. Returns int, else None if query fails."""
        response = self._send_basic(controller, self.CMD["QUERY_CURRENT_PROFILE_NUMBER"])
        if response and len(response) >= 2: # Profile number is 2 bytes, combine them into a single integer. First byte is high byte, second is low byte
            return (response[0] << 8) | response[1]
        return None

    def query_tpi_event_emit_state(self, controller: ZenController) -> Optional[bool]:
        """Get the current TPI Event multicast emitter state for a controller. Returns True if enabled, False if disabled, None if query fails."""
        response = self._send_basic(controller, self.CMD["QUERY_TPI_EVENT_EMIT_STATE"])
        return ZenEventMode.from_byte(response[0])
    
    def dali_add_tpi_event_filter(self, address: ZenAddress|ZenInstance, filter: ZenEventMask = ZenEventMask.all_events()) -> bool:
        """Stop specific events from an address/instance from being sent. Events in mask will be muted. Returns true if filter was added successfully."""
        instance_number = 0xFF
        if isinstance(address, ZenInstance):
            instance: ZenInstance = address
            instance_number = instance.number
            address = instance.address
        return self._send_basic(address.controller,
                             self.CMD["DALI_ADD_TPI_EVENT_FILTER"],
                             address.ecg_or_ecd_or_broadcast(),
                             [instance_number, filter.upper(), filter.lower()],
                             return_type='bool')
    
    def dali_clear_tpi_event_filter(self, address: ZenAddress|ZenInstance, unfilter: ZenEventMask = ZenEventMask.all_events()) -> bool:
        """Allow specific events from an address/instance to be sent again. Events in mask will be unmuted. Returns true if filter was cleared successfully."""
        instance_number = 0xFF
        if isinstance(address, ZenInstance):
            instance: ZenInstance = address
            instance_number = instance.number
            address = instance.address
        return self._send_basic(address.controller,
                             self.CMD["DALI_CLEAR_TPI_EVENT_FILTERS"],
                             address.ecg_or_ecd_or_broadcast(),
                             [instance_number, unfilter.upper(), unfilter.lower()],
                             return_type='bool')

    def query_dali_tpi_event_filters(self, address: ZenAddress|ZenInstance, start_at: int = 0) -> list[dict]: # TODO: remove start_at and page results automatically
        """Query active event filters for an address (or a specific instance). Returns a list of dictionaries containing filter info, or None if query fails."""
        instance_number = 0xFF
        if isinstance(address, ZenInstance):
            instance: ZenInstance = address
            instance_number = instance.number
            address = instance.address
            
        response = self._send_basic(address.controller, 
                                 self.CMD["QUERY_DALI_TPI_EVENT_FILTERS"],
                                 address.ecg_or_ecd_or_broadcast(),
                                 [start_at, 0x00, instance_number])
                                 
        if response and len(response) >= 5:  # Need at least modes + one result
            results = []
            modes_active = response[0] # same as query_tpi_event_emit_state()
            
            # Process results in groups of 4 bytes
            for i in range(1, len(response)-3, 4):
                result = {
                    'address': response[i],
                    'instance': response[i+1],
                    'event_mask': ZenEventMask.from_upper_lower(response[i+2], response[i+3])
                }
                results.append(result)
                
            return results
        return []

    def tpi_event_emit(self, controller: ZenController, mode: ZenEventMode = ZenEventMode(enabled=True, filtering=False, unicast=False, multicast=True)) -> bool:
        """Enable or disable TPI Event emission. Returns True if successful, else False."""
        mask = mode.bitmask()
        response = self._send_basic(controller, self.CMD["ENABLE_TPI_EVENT_EMIT"], 0x00) # disable first to clear any existing state... I think this is a bug?
        response = self._send_basic(controller, self.CMD["ENABLE_TPI_EVENT_EMIT"], mask)
        if response:
            if response[0] == mask:
                return True
        return False

    def set_tpi_event_unicast_address(self, controller: ZenController, ipaddr: Optional[str] = None, port: Optional[int] = None):
        """Configure TPI Events for Unicast mode with IP and port as defined in the ZenController instance."""
        data = [0,0,0,0,0,0]
        if port is not None:
            # Valid port number
            if not 0 <= port <= 65535: raise ValueError("Port must be between 0 and 65535")

            # Split port into upper and lower bytes
            port_upper = (port >> 8) & 0xFF 
            port_lower = port & 0xFF
            
            # Convert IP string to bytes
            try:
                ip_bytes = [int(x) for x in ipaddr.split('.')]
                if len(ip_bytes) != 4 or not all(0 <= x <= 255 for x in ip_bytes):
                    raise ValueError
            except ValueError:
                raise ValueError("Invalid IP address format")
                
            # Construct data payload: [port_upper, port_lower, ip1, ip2, ip3, ip4]
            data = [port_upper, port_lower] + ip_bytes
        
        return self._send_dynamic(controller, self.CMD["SET_TPI_EVENT_UNICAST_ADDRESS"], data)

    def query_tpi_event_unicast_address(self, controller: ZenController) -> Optional[dict]:
        """Query TPI Events state and unicast configuration.
        Sends a Basic frame to query the TPI Event emit state, Unicast Port and Unicast Address.
       
        Args:
            controller: ZenController instance
            
        Returns:
            Optional dict containing:
            - bool: Whether TPI Events are enabled
            - bool: Whether Unicast mode is enabled  
            - int: Configured unicast port
            - str: Configured unicast IP address
            
            Returns None if query fails
        """
        response = self._send_basic(controller, self.CMD["QUERY_TPI_EVENT_UNICAST_ADDRESS"])
        if response and len(response) >= 7:
            return {
                'mode': ZenEventMode.from_byte(response[0]),
                'port': (response[1] << 8) | response[2],
                'ip': f"{response[3]}.{response[4]}.{response[5]}.{response[6]}"
            }
        return None

    def query_group_numbers(self, controller: ZenController) -> list[ZenAddress]:
        """Query a controller for groups."""
        groups = self._send_basic(controller, self.CMD["QUERY_GROUP_NUMBERS"], return_type='list')
        zen_groups = []
        if groups is not None:
            groups.sort()
            for group in groups:
                zen_groups.append(ZenAddress(controller=controller, type=ZenAddressType.GROUP, number=group))
        return zen_groups
        
    def query_dali_colour(self, address: ZenAddress) -> Optional[ZenColour]:
        """Query colour information from a DALI address."""
        response = self._send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR"], address.ecg())
        return ZenColour.from_bytes(response)
    
    def query_profile_information(self, controller: ZenController) -> Optional[tuple[dict, dict]]:
        """Query a controller for profile information. Returns a tuple of two dicts, or None if query fails."""
        response = self._send_basic(controller, self.CMD["QUERY_PROFILE_INFORMATION"])
        # Initial 12 bytes:
        # 0-1 0x00 Current Active Profile Number
        # 2-3 0x00 Last Scheduled Profile Number
        # 4-7 0x22334455 Last Overridden Profile UTC
        # 8-11 0x44556677 Last Scheduled Profile UTC
        unpacked = struct.unpack('>HHII', response[0:12])
        state = {
            'current_active_profile': unpacked[0],
            'last_scheduled_profile': unpacked[1],
            'last_overridden_profile_utc': dt.fromtimestamp(unpacked[2]),
            'last_scheduled_profile_utc': dt.fromtimestamp(unpacked[3])
        }
        # Process profiles in groups of 3 bytes (2 bytes for profile number, 1 byte for profile behaviour)
        profiles: dict[int, int] = {}
        for i in range(12, len(response), 3):
            profile_number = struct.unpack('>H', response[i:i+2])[0]
            profile_behaviour = response[i+2]
            # bit 0: enabled: 0 = disabled, 1 = enabled
            # bit 1-2: priority: two bit int where 0 = scheduled, 1 = medium, 2 = high, 3 = emergency
            enabled = not bool(profile_behaviour & 0x01)
            priority = (profile_behaviour >> 1) & 0x03
            priority_label = ["Scheduled", "Medium", "High", "Emergency"][priority]
            profiles[profile_number] = {"enabled": enabled, "priority": priority, "priority_label": priority_label}
        # Return tuple of state and profiles
        return state, profiles
    
    def query_profile_numbers(self, controller: ZenController) -> Optional[list[int]]:
        """Query a controller for a list of available Profile Numbers. Returns a list of profile numbers, or None if query fails."""
        response = self._send_basic(controller, self.CMD["QUERY_PROFILE_NUMBERS"])
        if response and len(response) >= 2:
            # Response contains pairs of bytes for each profile number
            profile_numbers = []
            for i in range(0, len(response), 2):
                if i + 1 < len(response):
                    profile_num = (response[i] << 8) | response[i+1]
                    profile_numbers.append(profile_num)
            return profile_numbers
        return None

    def query_occupancy_instance_timers(self, instance: ZenInstance) -> Optional[dict]:
        """Query timer values for a DALI occupancy sensor instance. Returns dict, or None if query fails.

        Returns:
            dict:
                - int: Deadtime in seconds (0-255)
                - int: Hold time in seconds (0-255)
                - int: Report time in seconds (0-255)
                - int: Seconds since last occupied status (0-255)
        """
        response = self._send_basic(instance.address.controller, self.CMD["QUERY_OCCUPANCY_INSTANCE_TIMERS"], instance.address.ecd(), [0x00, 0x00, instance.number])
        if response and len(response) >= 5:
            return {
                'deadtime': response[0],
                'hold': response[1],
                'report': response[2],
                'last_detect': (response[3] << 8) | response[4]
            }
        return None

    def query_instances_by_address(self, address: ZenAddress) -> list[ZenInstance]:
        """Query a DALI address (ECD) for associated instances. Returns a list of ZenInstance, or an empty list if nothing found."""
        response = self._send_basic(address.controller, self.CMD["QUERY_INSTANCES_BY_ADDRESS"], address.ecd())
        if response and len(response) >= 4:
            instances = []
            # Process groups of 4 bytes for each instance
            for i in range(0, len(response), 4):
                if i + 3 < len(response):
                    instances.append(ZenInstance(
                        address=address,
                        number=response[i], # first byte
                        type=ZenInstanceType(response[i+1]) if response[i+1] in ZenInstanceType._value2member_map_ else None, # second byte
                        active=bool(response[i+2] & 0x02), # third byte, second bit
                        error=bool(response[i+2] & 0x01), # third byte, first bit
                    ))
            return instances
        return []

    def query_operating_mode_by_address(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG or ECD) for its operating mode. Returns an int containing the operating mode value, or None if the query fails."""
        response = self._send_basic(address.controller, self.CMD["QUERY_OPERATING_MODE_BY_ADDRESS"], address.ecg_or_ecd())
        if response and len(response) == 1:
            return response[0]  # Operating mode is in first byte
        return None

    def dali_colour(self, address: ZenAddress, colour: ZenColour, level: int = 255) -> bool:
        """Set a DALI address (ECG, group, broadcast) to a colour. Returns True if command succeeded, False otherwise."""
        return self._send_colour(address.controller, self.CMD["DALI_COLOUR"], address.ecg_or_group_or_broadcast(), colour, level)

    def query_group_by_number(self, address: ZenAddress) -> Optional[tuple[int, bool, int]]: # TODO: change to a dict or special class?
        """Query a DALI group for its occupancy status and level. Returns a tuple containing group number, occupancy status, and actual level."""
        response = self._send_basic(address.controller, self.CMD["QUERY_GROUP_BY_NUMBER"], address.group())
        if response and len(response) == 3:
            group_num = response[0]
            occupancy = bool(response[1])
            level = response[2]
            return (group_num, occupancy, level)
        return None

    def query_scene_numbers_by_address(self, address: ZenAddress) -> Optional[list[int]]:
        """Query a DALI address (ECG) for associated scenes. Returns a list of scene numbers where levels have been set."""
        return self._send_basic(address.controller, self.CMD["QUERY_SCENE_NUMBERS_BY_ADDRESS"], address.ecg(), return_type='list')

    def query_scene_levels_by_address(self, address: ZenAddress) -> Optional[list[int]]:
        """Query a DALI address (ECG) for its DALI scene levels. Returns a list of 16 scene level values (0-254, or None if not part of scene)."""
        response = self._send_basic(address.controller, self.CMD["QUERY_SCENE_LEVELS_BY_ADDRESS"], address.ecg(), return_type='list')
        if response:
            return [None if x == 255 else x for x in response]
        return None
    
    def query_group_membership_by_address(self, address: ZenAddress) -> list[ZenAddress]:
        """Query an address (ECG) for which DALI groups it belongs to. Returns a list of ZenAddress group instances."""
        response = self._send_basic(address.controller, self.CMD["QUERY_GROUP_MEMBERSHIP_BY_ADDRESS"], address.ecg())
        if response and len(response) == 2:
            groups = []
            # Process high byte (groups 8-15)
            for i in range(8):
                if response[0] & (1 << i):
                    groups.append(i + 8)
            # Process low byte (groups 0-7)  
            for i in range(8):
                if response[1] & (1 << i):
                    groups.append(i)
            # Process into ZenAddress instances
            groups.sort()
            zen_groups = []
            for number in groups:
                zen_groups.append(ZenAddress(
                    controller=address.controller,
                    type=ZenAddressType.GROUP,
                    number=number
                ))
            return zen_groups
        return []

    def query_dali_addresses_with_instances(self, controller: ZenController, start_address: int=0) -> list[ZenAddress]: # TODO: automate iteration over start_address=0, start_address=60, etc.
        """Query for DALI addresses that have instances associated with them.
        
        Due to payload restrictions, this needs to be called multiple times with different
        start addresses to check all possible devices (e.g. start_address=0, then start_address=60)
        
        Args:
            controller: ZenController instance
            start_address: Starting DALI address to begin searching from (0-127)
            
        Returns:
            List of DALI addresses that have instances, or None if query fails
        """
        addresses = self._send_basic(controller, self.CMD["QUERY_DALI_ADDRESSES_WITH_INSTANCES"], 0, [0,0,start_address], return_type='list')
        if not addresses:
            return []
        zen_addresses = []
        for number in addresses:
            if 64 <= number <= 127:  # Only process valid device addresses (64-127)
                zen_addresses.append(ZenAddress(
                    controller=controller,
                    type=ZenAddressType.ECD,
                    number=number-64 # subtract 64 to get actual DALI device address
                ))
        return zen_addresses
    
    def query_scene_numbers_for_group(self, address: ZenAddress) -> list[int]:
        """Query which DALI scenes are associated with a given group number. Returns list of scene numbers."""
        response = self._send_basic(address.controller, self.CMD["QUERY_SCENE_NUMBERS_FOR_GROUP"], address.group())
        if response and len(response) == 2:
            scenes = []
            # Process high byte (scenes 8-15)
            for i in range(8):
                if response[0] & (1 << i):
                    scenes.append(i + 8)
            # Process low byte (scenes 0-7)
            for i in range(8):
                if response[1] & (1 << i):
                    scenes.append(i)
            return sorted(scenes)
        return []
    
    def query_scene_label_for_group(self, address: ZenAddress, scene: int, generic_if_none: bool=False) -> Optional[str]:
        """Query the label for a scene (0-11) and group number combination. Returns string, or None if no label is set."""
        if not 0 <= scene < Const.MAX_SCENE: raise ValueError("Scene must be between 0 and 11")
        label = self._send_basic(address.controller, self.CMD["QUERY_SCENE_LABEL_FOR_GROUP"], address.group(), [scene], return_type='str')
        if label is None and generic_if_none:
            return f"Scene {scene}"
        return label
    
    def query_controller_version_number(self, controller: ZenController) -> Optional[str]:
        """Query the controller's version number. Returns string, or None if query fail s."""
        response = self._send_basic(controller, self.CMD["QUERY_CONTROLLER_VERSION_NUMBER"])
        if response and len(response) == 3:
            return f"{response[0]}.{response[1]}.{response[2]}"
        return None
    
    def query_control_gear_dali_addresses(self, controller: ZenController) -> list[ZenAddress]:
        """Query which DALI control gear addresses are present in the database. Returns a list of ZenAddress instances."""
        response = self._send_basic(controller, self.CMD["QUERY_CONTROL_GEAR_DALI_ADDRESSES"])
        if response and len(response) == 8:
            addresses = []
            # Process each byte which represents 8 addresses
            for byte_index, byte_value in enumerate(response):
                # Check each bit in the byte
                for bit_index in range(8):
                    if byte_value & (1 << bit_index):
                        # Calculate actual address from byte and bit position
                        number = byte_index * 8 + bit_index
                        addresses.append(
                            ZenAddress(
                                controller=controller,
                                type=ZenAddressType.ECG,
                                number=number
                            )
                        )
            return addresses
        return []
    
    def dali_inhibit(self, address: ZenAddress, time_seconds: int) -> bool:
        """Inhibit sensors from changing a DALI address (ECG or group or broadcast) for specified time in seconds (0-65535). Returns True if acknowledged, else False."""
        time_hi = (time_seconds >> 8) & 0xFF  # Convert time to 16-bit value
        time_lo = time_seconds & 0xFF
        return self._send_basic(address.controller, self.CMD["DALI_INHIBIT"], address.ecg_or_group_or_broadcast(), [0x00, time_hi, time_lo], return_type='ok')
    
    def dali_scene(self, address: ZenAddress, scene: int) -> bool:
        """Send RECALL SCENE (0-11) to an address (ECG or group or broadcast). Returns True if acknowledged, else False."""
        if not 0 <= scene < Const.MAX_SCENE: raise ValueError(f"Scene number must be between 0 and {Const.MAX_SCENE}, got {scene}")
        return self._send_basic(address.controller, self.CMD["DALI_SCENE"], address.ecg_or_group_or_broadcast(), [0x00, 0x00, scene], return_type='ok')
    
    def dali_arc_level(self, address: ZenAddress, level: int) -> bool:
        """Send DIRECT ARC level (0-254) to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        if not 0 <= level <= Const.MAX_LEVEL: raise ValueError(f"Level must be between 0 and {Const.MAX_LEVEL}, got {level}")
        return self._send_basic(address.controller, self.CMD["DALI_ARC_LEVEL"], address.ecg_or_group_or_broadcast(), [0x00, 0x00, level], return_type='ok')
    
    def dali_on_step_up(self, address: ZenAddress) -> bool:
        """Send ON AND STEP UP to an address (ECG or group or broadcast). If a device is off, it will turn it on. If a device is on, it will step up. No fade."""
        return self._send_basic(address.controller, self.CMD["DALI_ON_STEP_UP"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_step_down_off(self, address: ZenAddress) -> bool:
        """Send STEP DOWN AND OFF to an address (ECG or group or broadcast). If a device is at min, it will turn off. If a device isn't yet at min, it will step down. No fade."""
        return self._send_basic(address.controller, self.CMD["DALI_STEP_DOWN_OFF"], address.ecg_or_group_or_broadcast(), return_type='ok')

    def dali_up(self, address: ZenAddress) -> bool:
        """Send DALI UP to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        return self._send_basic(address.controller, self.CMD["DALI_UP"], address.ecg_or_group_or_broadcast(), return_type='ok')

    def dali_down(self, address: ZenAddress) -> bool:
        """Send DALI DOWN to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        return self._send_basic(address.controller, self.CMD["DALI_DOWN"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_recall_max(self, address: ZenAddress) -> bool:
        """Send RECALL MAX to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return self._send_basic(address.controller, self.CMD["DALI_RECALL_MAX"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_recall_min(self, address: ZenAddress) -> bool:
        """Send RECALL MIN to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return self._send_basic(address.controller, self.CMD["DALI_RECALL_MIN"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_off(self, address: ZenAddress) -> bool:
        """Send OFF to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return self._send_basic(address.controller, self.CMD["DALI_OFF"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_query_level(self, address: ZenAddress) -> Optional[int]:
        """Query the Arc Level for a DALI address (ECG or group). Returns arc level as int, or None if mixed levels."""
        response = self._send_basic(address.controller, self.CMD["DALI_QUERY_LEVEL"], address.ecg_or_group(), return_type='int')
        if response == 255: return None # 255 indicates mixed levels
        return response
    
    def dali_query_control_gear_status(self, address: ZenAddress) -> Optional[dict]:
        """Query the Status for a DALI address (ECG or group or broadcast). Returns a dictionary of status flags."""
        response = self._send_basic(address.controller, self.CMD["DALI_QUERY_CONTROL_GEAR_STATUS"], address.ecg_or_group_or_broadcast())
        if response and len(response) == 1:
            return {
                "cg_failure": bool(response[0] & 0x01),
                "lamp_failure": bool(response[0] & 0x02),
                "lamp_power_on": bool(response[0] & 0x04),
                "limit_error": bool(response[0] & 0x08), # (an Arc-level > Max or < Min requested)
                "fade_running": bool(response[0] & 0x10),
                "reset": bool(response[0] & 0x20),
                "missing_short_address": bool(response[0] & 0x40),
                "power_failure": bool(response[0] & 0x80)
            }
        return None
    
    def dali_query_cg_type(self, address: ZenAddress) -> Optional[list[int]]:
        """Query device type information for a DALI address (ECG).
            
        Returns:
            Optional[list[int]]: List of device type numbers that the control gear belongs to.
                                Returns empty list if device doesn't exist.
                                Returns None if query fails.
        """
        response = self._send_basic(address.controller, self.CMD["DALI_QUERY_CG_TYPE"], address.ecg())
        if response and len(response) == 4:
            device_types = []
            # Process each byte which represents 8 device types
            for byte_index, byte_value in enumerate(response):
                # Check each bit in the byte
                for bit in range(8):
                    if byte_value & (1 << bit):
                        # Calculate actual device type number
                        device_type = byte_index * 8 + bit
                        device_types.append(device_type)
            return device_types
        return None
    
    def dali_query_last_scene(self, address: ZenAddress) -> Optional[int]:
        """Query the last heard Scene for a DALI address (ECG or group or broadcast). Returns scene number, or None if query fails.
            
        Note:
            Changes to a single DALI device done through group or broadcast scene commands
            also change the last heard scene for the individual device address. For example,
            if A10 is member of G0 and we send a scene command to G0, A10 will show the 
            same last heard scene as G0.
        """
        return self._send_basic(address.controller, self.CMD["DALI_QUERY_LAST_SCENE"], address.ecg_or_group_or_broadcast(), return_type='int')
    
    def dali_query_last_scene_is_current(self, address: ZenAddress) -> Optional[bool]:
        """Query if the last heard scene is the current active scene for a DALI address (ECG or group or broadcast).
        Returns True if still active, False if another command has been issued since, or None if query fails."""
        return self._send_basic(address.controller, self.CMD["DALI_QUERY_LAST_SCENE_IS_CURRENT"], address.ecg_or_group_or_broadcast(), return_type='bool')
    
    def dali_query_min_level(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG) for its minimum level (0-254). Returns the minimum level if successful, None if query fails."""
        return self._send_basic(address.controller, self.CMD["DALI_QUERY_MIN_LEVEL"], address.ecg(), return_type='int')

    def dali_query_max_level(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG) for its maximum level (0-254). Returns the maximum level if successful, None if query fails."""
        return self._send_basic(address.controller, self.CMD["DALI_QUERY_MAX_LEVEL"], address.ecg(), return_type='int')
    
    def dali_query_fade_running(self, address: ZenAddress) -> Optional[bool]:
        """Query a DALI address (ECG) if a fade is currently running. Returns True if a fade is currently running, False if not, None if query fails."""
        return self._send_basic(address.controller, self.CMD["DALI_QUERY_FADE_RUNNING"], address.ecg(), return_type='bool')
    
    def dali_enable_dapc_sequence(self, address: ZenAddress) -> Optional[bool]:
        """Begin a DALI Direct Arc Power Control (DAPC) Sequence.
        
        DAPC allows overriding of the fade rate for immediate level setting. The sequence
        continues for 250ms. If no arc levels are received within 250ms, the sequence ends
        and normal fade rates resume.
        
        Args:
            address: ZenAddress instance (ECG address)
            
        Returns:
            Optional[bool]: True if successful, False if failed, None if no response
        """
        return self._send_basic(address.controller, self.CMD["DALI_ENABLE_DAPC_SEQ"], address.ecg(), return_type='bool')
    
    def query_dali_ean(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG or ECD) for its European Article Number (EAN/GTIN). Returns an integer if successful, None if query fails."""
        response = self._send_basic(address.controller, self.CMD["QUERY_DALI_EAN"], address.ecg_or_ecd())
        if response and len(response) == 6:
            ean = 0
            for byte in response:
                ean = (ean << 8) | byte
            return ean
        return None
    
    def query_dali_serial(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG or ECD) for its Serial Number. Returns an integer if successful, None if query fails."""
        response = self._send_basic(address.controller, self.CMD["QUERY_DALI_SERIAL"], address.ecg_or_ecd())
        if response and len(response) == 8:
            # Convert 8 bytes to decimal integer
            serial = 0
            for byte in response:
                serial = (serial << 8) | byte
            return serial
        return None
    
    def dali_custom_fade(self, address: ZenAddress, level: int, seconds: int) -> bool:
        """Fade a DALI address (ECG or group) to a level (0-254) with a custom fade time in seconds (0-65535). Returns True if successful, else False."""
        if not 0 <= level < Const.MAX_LEVEL:
            raise ValueError("Target level must be between 0 and 254")
        if not 0 <= seconds <= 65535:
            raise ValueError("Fade time must be between 0 and 65535 seconds")

        # Convert fade time to integer seconds and split into high/low bytes
        seconds_hi = (seconds >> 8) & 0xFF
        seconds_lo = seconds & 0xFF
        
        return self._send_basic(
            address.controller,
            self.CMD["DALI_CUSTOM_FADE"],
            address.ecg_or_group(),
            [level, seconds_hi, seconds_lo],
            return_type='ok'
        )
    
    def dali_go_to_last_active_level(self, address: ZenAddress) -> bool:
        """Command a DALI Address (ECG or group) to go to its "Last Active" level. Returns True if successful, else False."""
        return self._send_basic(address.controller, self.CMD["DALI_GO_TO_LAST_ACTIVE_LEVEL"], address.ecg_or_group(), return_type='ok')
    
    def query_dali_instance_label(self, instance: ZenInstance, generic_if_none: bool=False) -> Optional[str]:
        """Query the label for a DALI Instance. Returns a string, or None if not set. Optionally, returns a generic label if the instance label is not set."""
        label = self._send_basic(instance.address.controller, self.CMD["QUERY_DALI_INSTANCE_LABEL"], instance.address.ecd(), [0x00, 0x00, instance.number], return_type='str')
        if label is None and generic_if_none:
            label = instance.type.name.title().replace("_", " ")  + " " + str(instance.number)
        return label

    def change_profile_number(self, controller: ZenController, profile: int) -> bool:
        """Change the active profile number (0-65535). Returns True if successful, else False."""
        if not 0 <= profile <= 0xFFFF: raise ValueError("Profile number must be between 0 and 65535")
        profile_hi = (profile >> 8) & 0xFF
        profile_lo = profile & 0xFF
        return self._send_basic(controller, self.CMD["CHANGE_PROFILE_NUMBER"], 0x00, [0x00, profile_hi, profile_lo], return_type='ok')
    
    def return_to_scheduled_profile(self, controller: ZenController) -> bool:
        """Return to the scheduled profile. Returns True if successful, else False."""
        return self.change_profile_number(controller, 0xFFFF) # See docs page 91, 0xFFFF returns to scheduled profile

    def query_instance_groups(self, instance: ZenInstance) -> Optional[tuple[int, int, int]]: # TODO: replace Tuple with dict
        """Query the group targets associated with a DALI instance.
            
        Returns:
            Optional tuple containing:
            - int: Primary group number (0-15, or 255 if not configured)
            - int: First group number (0-15, or 255 if not configured) 
            - int: Second group number (0-15, or 255 if not configured)
            
            Returns None if query fails
            
        The Primary group typically represents where the physical device resides.
        A group number of 255 (0xFF) indicates that no group has been configured.
        """
        response = self._send_basic(
            instance.address.controller,
            self.CMD["QUERY_INSTANCE_GROUPS"], 
            instance.address.ecd(),
            [0x00, 0x00, instance.number],
            return_type='list'
        )
        if response and len(response) == 3:
            return (
                response[0] if response[0] != 0xFF else None,
                response[1] if response[1] != 0xFF else None,
                response[2] if response[2] != 0xFF else None
            )
        return None
    
    def query_dali_fitting_number(self, address: ZenAddress) -> Optional[str]:
        """Query a DALI address (ECG or ECD) for its fitting number. Returns the fitting number (e.g. '1.2') or a generic identifier if the address doesn't exist, or None if the query fails."""
        return self._send_basic(address.controller, self.CMD["QUERY_DALI_FITTING_NUMBER"], address.ecg_or_ecd(), return_type='str')
        
    def query_dali_instance_fitting_number(self, instance: ZenInstance) -> Optional[str]:
        """Query a DALI instance for its fitting number. Returns a string (e.g. '1.2.0') or None if query fails."""
        return self._send_basic(instance.address.controller, self.CMD["QUERY_DALI_INSTANCE_FITTING_NUMBER"], instance.address.ecd(), [0x00, 0x00, instance.number], return_type='str')
    
    def query_controller_label(self, controller: ZenController) -> Optional[str]:
        """Request the label for the controller. Returns the controller's label string, or None if query fails."""
        return self._send_basic(controller, self.CMD["QUERY_CONTROLLER_LABEL"], return_type='str')
    
    def query_controller_fitting_number(self, controller: ZenController) -> Optional[str]:
        """Request the fitting number string for the controller itself. Returns the controller's fitting number (e.g. '1'), or None if query fails."""
        return self._send_basic(controller, self.CMD["QUERY_CONTROLLER_FITTING_NUMBER"], return_type='str')

    def query_is_dali_ready(self, controller: ZenController) -> bool:
        """Query whether the DALI line is ready or has a fault. Returns True if DALI line is ready, False if there is a fault."""
        return self._send_basic(controller, self.CMD["QUERY_IS_DALI_READY"], return_type='ok')
    
    def query_controller_startup_complete(self, controller: ZenController) -> bool:
        """Query whether the controller has finished its startup sequence. Returns True if startup is complete, False if still in progress.

        The startup sequence performs DALI queries such as device type, current arc-level, GTIN, 
        serial number, etc. The more devices on a DALI line, the longer startup will take to complete.
        For a line with only a handful of devices, expect it to take approximately 1 minute.
        Waiting for the startup sequence to complete is particularly important if you wish to 
        perform queries about DALI.
        """
        return self._send_basic(controller, self.CMD["QUERY_CONTROLLER_STARTUP_COMPLETE"], return_type='ok')
    
    def override_dali_button_led_state(self, instance: ZenInstance, led_state: bool) -> bool:
        """Override the LED state for a DALI push button. State is True for LED on, False for LED off. Returns true if command succeeded, else False."""
        return self._send_basic(instance.address.controller,
                               self.CMD["OVERRIDE_DALI_BUTTON_LED_STATE"],
                               instance.address.ecd(),
                               [0x00, 0x02 if led_state else 0x01, instance.number],
                               return_type='ok')
    
    def query_last_known_dali_button_led_state(self, instance: ZenInstance) -> Optional[bool]:
        """Query the last known LED state for a DALI push button. Returns True if LED is on, False if LED is off, None if query failed
            
        Note: The "last known" LED state may not be the actual physical LED state.
        This only works for LED modes where the controller or TPI caller is managing
        the LED state. In many cases, the control device itself manages its own LED.
        """
        response = self._send_basic(instance.address.controller,
                                   self.CMD["QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE"],
                                   instance.address.ecd(),
                                   [0x00, 0x00, instance.number])
        if response and len(response) == 1:
            match response[0]:
                case 0x01: return False
                case 0x02: return True
        return None

    def dali_stop_fade(self, address: ZenAddress) -> bool:
        """Tell a DALI address (ECG or ECD) to stop running a fade. Returns True if command succeeded, else False.

        Caution: this literally stops the fade. It doesn't jump to the target level.

        Note: For custom fades started via DALI_CUSTOM_FADE, this can only stop
        fades that were started with the same target address. For example, you 
        cannot stop a custom fade on a single address if it was started as part
        of a group or broadcast fade.
        """
        return self._send_basic(address.controller, self.CMD["DALI_STOP_FADE"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def query_dali_colour_features(self, address: ZenAddress) -> Optional[dict]:
        """Query the colour features/capabilities of a DALI device.
        
        Args:
            address: ZenAddress
            
        Returns:
            Dictionary containing colour capabilities, or None if query failed:
            {
                'supports_xy': bool,          # Supports CIE 1931 XY coordinates
                'primary_count': int,         # Number of primaries (0-7)
                'rgbwaf_channels': int,      # Number of RGBWAF channels (0-7)
            }
        """
        response = self._send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR_FEATURES"], address.ecg())
        if response and len(response) == 1:
            features = response[0]
            return {
                'supports_xy': bool(features & 0x01),      # Bit 0
                'supports_tunable': bool(features & 0x02), # Bit 1
                'primary_count': (features & 0x1C) >> 2,   # Bits 2-4
                'rgbwaf_channels': (features & 0xE0) >> 5, # Bits 5-7
            }
        elif response is None:
            return {
                'supports_xy': False,
                'supports_tunable': False,
                'primary_count': 0,
                'rgbwaf_channels': 0,
            }
        return None
    
    def query_dali_colour_temp_limits(self, address: ZenAddress) -> Optional[dict]:
        """Query the colour temperature limits of a DALI device.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63)
            
        Returns:
            Dictionary containing colour temperature limits in Kelvin, or None if query failed:
            {
                'physical_warmest': int,  # Physical warmest temp limit (K)
                'physical_coolest': int,  # Physical coolest temp limit (K) 
                'soft_warmest': int,      # Configured warmest temp limit (K)
                'soft_coolest': int,      # Configured coolest temp limit (K)
                'step_value': int         # Step value (K)
            }
        """
        response = self._send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR_TEMP_LIMITS"], address.ecg())
        if response and len(response) == 10:
            return {
                'physical_warmest': (response[0] << 8) | response[1],
                'physical_coolest': (response[2] << 8) | response[3],
                'soft_warmest': (response[4] << 8) | response[5],
                'soft_coolest': (response[6] << 8) | response[7],
                'step_value': (response[8] << 8) | response[9]
            }
        return None
    
    def set_system_variable(self, controller: ZenController, variable: int, value: int) -> bool:
        """Set a system variable (0-147) value (-32768-32767) on the controller. Returns True if successful, else False."""
        if not 0 <= variable < Const.MAX_SYSVAR:
            raise ValueError(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {variable}")
        if not -32768 <= value <= 32767:
            raise ValueError(f"Value must be between -32768 and 32767, received {value}")
        bytes = value.to_bytes(length=2, byteorder="big", signed=True)
        return self._send_basic(controller, self.CMD["SET_SYSTEM_VARIABLE"], variable, [0x00, bytes[0], bytes[1]], return_type='ok')

        # If abs(value) is less than 32760, 
        #   If value has 2 decimal places, use magitude -2 (signed 0xfe)
        #   Else if value has 1 decimal place, use magitude -1 (signed 0xff)
        #   Else use magitude 0 (signed 0x00)
        # Else if abs(value) is less than 327600, use magitude 1 (signed 0x01)
        # Else if abs(value) is less than 3276000, use magitude 2 (signed 0x02)
    
    def query_system_variable(self, controller: ZenController, variable: int) -> Optional[int]:
        """Query the controller for the value of a system variable (0-147). Returns the variable's value (-32768-32767) if successful, else None."""
        if not 0 <= variable < Const.MAX_SYSVAR:
            raise ValueError(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {variable}")
        response = self._send_basic(controller, self.CMD["QUERY_SYSTEM_VARIABLE"], variable)
        if response and len(response) == 2:
            return int.from_bytes(response, byteorder="big", signed=True)
        else: # Value is unset
            return None
    
    def query_system_variable_name(self, controller: ZenController, variable: int) -> Optional[str]:
        """Query the name of a system variable (0-147). Returns the variable's name, or None if query fails."""
        if not 0 <= variable < Const.MAX_SYSVAR:
            raise ValueError(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {variable}")
        return self._send_basic(controller, self.CMD["QUERY_SYSTEM_VARIABLE_NAME"], variable, return_type='str')
