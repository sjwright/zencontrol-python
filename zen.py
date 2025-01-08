import socket
import struct
import time
import logging
from typing import Optional, Tuple, List, Union, Dict, Self
from enum import Enum
from threading import Thread, Event
from colorama import Fore, Back, Style
from dataclasses import dataclass, field

class Const:
    MAGIC_BYTE = 0x04
    MAX_ECG = 64
    MAX_ECD = 64
    MAX_INSTANCE = 32
    MAX_GROUP = 16
    MAX_SCENE = 12
    MAX_SYSVAR = 48
    MIN_KELVIN = 1000
    MAX_KELVIN = 20000
    MAX_LEVEL = 254
    
@dataclass
class ZenController:
    name: str
    label: str
    mac: str
    host: str
    port: int = 5108
    mac_bytes: bytes = field(init=False)
    def __post_init__(self):
        self.mac_bytes = bytes.fromhex(self.mac.replace(':', ''))
        
class AddressType(Enum):
    BROADCAST = 0
    ECG = 1
    ECD = 2
    GROUP = 3

@dataclass
class ZenAddress:
    controller: ZenController
    type: AddressType
    number: int
    @classmethod
    def broadcast(cls, controller: ZenController) -> Self:
        return cls(controller=controller, type=AddressType.BROADCAST, number=255)
    def ecg(self) -> int:
        if self.type == AddressType.ECG: return self.number
        raise ValueError("Address is not a Control Gear")
    def ecg_or_group(self) -> int:
        if self.type == AddressType.ECG: return self.number
        if self.type == AddressType.GROUP: return self.number+64
        raise ValueError("Address is not a Control Gear or Group")
    def ecg_or_group_or_broadcast(self) -> int:
        if self.type == AddressType.ECG: return self.number
        if self.type == AddressType.GROUP: return self.number+64
        if self.type == AddressType.BROADCAST: return 255
        raise ValueError("Address is not a Control Gear, Group or Broadcast")
    def ecg_or_ecd(self) -> int:
        if self.type == AddressType.ECG: return self.number
        if self.type == AddressType.ECD: return self.number+64
        raise ValueError("Address is not a Control Gear or Control Device")
    def ecg_or_ecd_or_broadcast(self) -> int:
        if self.type == AddressType.ECG: return self.number
        if self.type == AddressType.ECD: return self.number+64
        if self.type == AddressType.BROADCAST: return 255
        raise ValueError("Address is not a Control Gear or Control Device")
    def ecd(self) -> int:
        if self.type == AddressType.ECD: return self.number+64
        raise ValueError("Address is not a Control Device")
    def group(self) -> int:
        if self.type == AddressType.GROUP: return self.number
        raise ValueError("Address is not a Group")
    def scene(self) -> int:
        if self.type == AddressType.SCENE: return self.number
        raise ValueError("Address is not a Scene")
    def __post_init__(self):
        match self.type:
            case AddressType.BROADCAST:
                self.number = 255
            case AddressType.ECG:
                if not 0 <= self.number < Const.MAX_ECG: raise ValueError("Control Gear address must be between 0 and 63")
            case AddressType.ECD:
                if not 0 <= self.number < Const.MAX_ECD: raise ValueError("Control Device address must be between 0 and 63")
            case AddressType.GROUP:
                if not 0 <= self.number < Const.MAX_GROUP: raise ValueError("Group number must be between 0 and 15")
            case AddressType.SCENE:
                if not 0 <= self.number < Const.MAX_SCENE: raise ValueError("Scene number must be between 0 and 11")
            case _:
                raise ValueError("Invalid address type")

class InstanceType(Enum):
    PUSH_BUTTON = 0
    ABSOLUTE_INPUT = 1
    OCCUPANCY_SENSOR = 2
    LIGHT_SENSOR = 3
    GENERAL_SENSOR = 4

@dataclass
class ZenInstance:
    address: ZenAddress
    type: InstanceType
    number: int
    active: bool
    error: bool
    def __post_init__(self):
        if not 0 <= self.number < Const.MAX_INSTANCE: raise ValueError("Instance number must be between 0 and 31")

class ZenColourType(Enum):
    XY = 0x10
    TC = 0x20
    RGBWAF = 0x80

@dataclass
class ZenColourGeneric:
    type: ZenColourType = field(init=False)
    level: int
    def __post_init__(self):
        if self.level is None: self.level = 255
        if not 0 <= self.level < Const.MAX_LEVEL: raise ValueError("Level must be between 0 and 254, or 255 for no level")

@dataclass
class ZenColourRGBWAF(ZenColourGeneric):
    r: int
    g: int
    b: int
    w: int
    a: int
    f: int
    def __post_init__(self):
        self.type = ZenColourType.RGBWAF
        if not 0 <= self.r <= 255: raise ValueError("R must be between 0 and 255")
        if not 0 <= self.g <= 255: raise ValueError("G must be between 0 and 255")
        if not 0 <= self.b <= 255: raise ValueError("B must be between 0 and 255")
        if not 0 <= self.w <= 255: raise ValueError("W must be between 0 and 255")
        if not 0 <= self.a <= 255: raise ValueError("A must be between 0 and 255")
        if not 0 <= self.f <= 255: raise ValueError("F must be between 0 and 255")
    def data(self) ->bytes:
        return struct.pack('BBBBBBBBB', self.level, 0x80, self.r, self.g, self.b, self.w, self.a, self.f)

@dataclass
class ZenColourXY(ZenColourGeneric):
    x: int
    y: int
    def __post_init__(self):
        self.type = ZenColourType.XY
        if not 0 <= self.x <= 65535: raise ValueError("X must be between 0 and 65535")
        if not 0 <= self.y <= 65535: raise ValueError("Y must be between 0 and 65535")
    def data(self) -> bytes:
        return struct.pack('>BBHH', self.level, 0x10, self.x, self.y)

@dataclass
class ZenColourTC(ZenColourGeneric):
    kelvin: int
    def __post_init__(self):
        if not Const.MIN_KELVIN <= self.kelvin <= Const.MAX_KELVIN: raise ValueError("Kelvin must be between 1000 and 20000")
        self.type = ZenColourType.TC
    def data(self) -> bytes:
        return struct.pack('>BBH', self.level, 0x20, self.kelvin)
        
@dataclass()
class ZenEventMask:
    button_press_event: bool = False
    button_hold_event: bool = False
    absolute_input_event: bool = False
    level_change_event: bool = False
    group_level_change_event: bool = False
    scene_change_event: bool = False
    is_occupied: bool = False
    is_unoccupied: bool = False
    colour_changed: bool = False
    profile_changed: bool = False
    @classmethod
    def all_events(cls):
        return cls(
            button_press_event = True,
            button_hold_event = True,
            absolute_input_event = True,
            level_change_event = True,
            group_level_change_event = True,
            scene_change_event = True,
            is_occupied = True,
            is_unoccupied = True,
            colour_changed = True,
            profile_changed = True
        )
    @classmethod
    def from_upper_lower(cls, upper: int, lower: int) -> Self:
        return cls.from_double_byte((upper << 8) | lower)
    @classmethod
    def from_double_byte(cls, event_mask: int) -> Self:
        return cls(
            button_press_event = (event_mask & (1 << 0)) != 0,
            button_hold_event = (event_mask & (1 << 1)) != 0,
            absolute_input_event = (event_mask & (1 << 2)) != 0,
            level_change_event = (event_mask & (1 << 3)) != 0,
            group_level_change_event = (event_mask & (1 << 4)) != 0,
            scene_change_event = (event_mask & (1 << 5)) != 0,
            is_occupied = (event_mask & (1 << 6)) != 0,
            is_unoccupied = (event_mask & (1 << 7)) != 0,
            colour_changed = (event_mask & (1 << 8)) != 0,
            profile_changed = (event_mask & (1 << 9)) != 0
        )
    def bitmask(self) -> int:
        event_mask = 0x00
        if self.button_press_event: event_mask |= (1 << 0)
        if self.button_hold_event: event_mask |= (1 << 1)
        if self.absolute_input_event: event_mask |= (1 << 2)
        if self.level_change_event: event_mask |= (1 << 3)
        if self.group_level_change_event: event_mask |= (1 << 4)
        if self.scene_change_event: event_mask |= (1 << 5)
        if self.is_occupied: event_mask |= (1 << 6)
        if self.is_unoccupied: event_mask |= (1 << 7)
        if self.colour_changed: event_mask |= (1 << 8)
        if self.profile_changed: event_mask |= (1 << 9)
        return event_mask
    def upper(self) -> int:
        return (self.bitmask() >> 8) & 0xFF  
    def lower(self) -> int:
        return self.bitmask() & 0xFF

class ZenProtocol:

    # Define commands as a class dictionary
    CMD: Dict[str, int] = {
        # Rudimentarly tested
        "QUERY_CONTROLLER_VERSION_NUMBER": 0x1C,    # Query ZenController Version Number
        "QUERY_CONTROLLER_LABEL": 0x24,             # Query the label of the controller
        "QUERY_CONTROLLER_FITTING_NUMBER": 0x25,    # Query the fitting number of the controller itself
        "QUERY_CONTROLLER_STARTUP_COMPLETE": 0x27,  # Query whether controller startup is complete
        "QUERY_IS_DALI_READY": 0x26,                # Query whether DALI bus is ready (or has a fault)

        "ENABLE_TPI_EVENT_EMIT": 0x08,              # Enable or disable TPI Events
        "SET_SYSTEM_VARIABLE": 0x36,                # Set a system variable value
        "QUERY_SYSTEM_VARIABLE": 0x37,              # Query system variable

        "QUERY_CONTROL_GEAR_DALI_ADDRESSES": 0x1D,  # Query Control Gear present in database
        "QUERY_DALI_DEVICE_LABEL": 0x03,            # Query the label for a DALI ECD or ECG by address
        "DALI_QUERY_LEVEL": 0xAA,                   # Query the the level on a address
        "DALI_QUERY_CG_TYPE": 0xAC,                 # Query Control Gear type data on a address
        "QUERY_GROUP_MEMBERSHIP_BY_ADDRESS": 0x15,  # Query DALI Group membership by address
        "QUERY_SCENE_NUMBERS_BY_ADDRESS": 0x14,     # Query for DALI Scenes an address has levels for
        "QUERY_SCENE_LEVELS_BY_ADDRESS": 0x1E,      # Query Scene level values for a given address
        "QUERY_DALI_COLOUR_FEATURES": 0x35,         # Query the DALI colour features/capabilities
        "QUERY_DALI_COLOUR_TEMP_LIMITS": 0x38,      # Query Colour Temperature max/min + step in Kelvin
        "QUERY_DALI_EAN": 0xB8,                     # Query the DALI European Article Number at an address
        "QUERY_DALI_SERIAL": 0xB9,                  # Query the Serial Number at a address
        "QUERY_DALI_FITTING_NUMBER": 0x22,          # Query the fitting number for control gear/devices
        
        "QUERY_GROUP_NUMBERS": 0x09,                # Query the DALI Group numbers
        "QUERY_GROUP_LABEL": 0x01,                  # Query the label for a DALI Group by Group Number
        "QUERY_SCENE_NUMBERS_FOR_GROUP": 0x1A,      # Query Scene Numbers attributed to a group
        "QUERY_SCENE_LABEL_FOR_GROUP": 0x1B,        # Query Scene Labels attributed to a group scene
        "QUERY_GROUP_BY_NUMBER": 0x12,              # Query DALI Group information by Group Number
        
        "DALI_SCENE": 0xA1,                         # Call a DALI Scene on a address
        "DALI_QUERY_LAST_SCENE": 0xAD,              # Query Last heard DALI Scene
        "DALI_QUERY_LAST_SCENE_IS_CURRENT": 0xAE,   # Query if last heard Scene is current scene
        
        "QUERY_PROFILE_NUMBERS": 0x0B,              # Query all available Profile numbers
        "QUERY_PROFILE_LABEL": 0x04,                # Query the label for a controller profile
        "QUERY_CURRENT_PROFILE_NUMBER": 0x05,       # Query the current profile number
        "CHANGE_PROFILE_NUMBER": 0xC0,              # Request a Profile Change on the controller
        
        "QUERY_DALI_ADDRESSES_WITH_INSTANCES": 0x16, # Query DALI addresses that have instances
        "QUERY_INSTANCES_BY_ADDRESS": 0x0D,         # Query information of instances
        "QUERY_OPERATING_MODE_BY_ADDRESS": 0x28,    # Query the operating mode for a device
        "QUERY_DALI_INSTANCE_FITTING_NUMBER": 0x23, # Query the fitting number for an instance
        "QUERY_DALI_INSTANCE_LABEL": 0xB7,          # Query DALI Instance for its label
        "QUERY_INSTANCE_GROUPS": 0x21,              # Query group targets related to an instance
        
        "DALI_QUERY_CONTROL_GEAR_STATUS": 0xAB,     # Query status data on a address, group or broadcast
        
        "QUERY_DALI_COLOUR": 0x34,                  # Query the Colour information on a DALI target
        "DALI_COLOUR": 0x0E,                        # Set a DALI target to a colour
        
        "QUERY_OCCUPANCY_INSTANCE_TIMERS": 0x0C,    # Query an occupancy instance for its timer values
        
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

        "QUERY_TPI_EVENT_EMIT_STATE": 0x07,         # Query whether TPI Events are enabled or disabled
        "DALI_ADD_TPI_EVENT_FILTER": 0x31,          # Request that filters be added for DALI TPI Events
        "QUERY_DALI_TPI_EVENT_FILTERS": 0x32,       # Query DALI TPI Event filters on a address
        "DALI_CLEAR_TPI_EVENT_FILTERS": 0x33,       # Request that DALI TPI Event filters be cleared

        # Implemented but not tested
        "OVERRIDE_DALI_BUTTON_LED_STATE": 0x29,     # Override a button LED state
        "QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE": 0x30, # Query button last known button LED state

        "SET_TPI_EVENT_UNICAST_ADDRESS": 0x40,      # Set a TPI Events unicast address and port
        "QUERY_TPI_EVENT_UNICAST_ADDRESS": 0x41,    # Query TPI Events State, unicast address and port

        # Won't implement (because I can't test)
        "TRIGGER_SDDP_IDENTIFY": 0x06,              # Trigger a Control4 SDDP Identify
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

    ERROR_CODES: Dict[int, str] = {
        0x01: "ERROR_CHECKSUM",                     # Checksum Error
        0x02: "ERROR_SHORT_CIRCUIT",                # A short on the DALI line was detected
        0x03: "ERROR_RECEIVE_ERROR",                # Receive error
        0x04: "ERROR_UNKNOWN_CMD",                  # The command in the request is unrecognised
        0xB0: "ERROR_PAID_FEATURE",                 # The command requires a paid feature not purchased or enabled
        0xB1: "ERROR_INVALID_ARGS",                 # Invalid arguments
        0xB2: "ERROR_CMD_REFUSED",                  # The command couldn't be processed
        0xB3: "ERROR_QUEUE_FAILURE",                # A queue or buffer required to process the command is full or broken
        0xB4: "ERROR_RESPONSE_UNAVAIL",             # Some feature isn't available for some reason, refer to docs
        0xB5: "ERROR_OTHER_DALI_ERROR",             # The DALI related request couldn't be processed due to an error
        0xB6: "ERROR_MAX_LIMIT",                    # A resource limit was reached on the controller
        0xB7: "ERROR_UNEXPECTED_RESULT",            # An unexpected result occurred
        0xB8: "ERROR_UNKNOWN_TARGET"                # Device doesn't exist
    }

    EVENT_TYPES: Dict[int, str] = {
        0x00: "BUTTON_PRESS_EVENT",
        0x01: "BUTTON_HOLD_EVENT",
        0x02: "ABSOLUTE_INPUT_EVENT",
        0x03: "LEVEL_CHANGE_EVENT",
        0x04: "GROUP_LEVEL_CHANGE_EVENT",
        0x05: "SCENE_CHANGE_EVENT",
        0x06: "IS_OCCUPIED",
        0x07: "IS_UNOCCUPIED",
        0x08: "COLOUR_CHANGED",
        0x09: "PROFILE_CHANGED"
    }
    
    DALI_STATUS_MASKS: Dict[int, str] = {
        0x01: "DALI_STATUS_CG_FAILURE",             # Control Gear Failure
        0x02: "DALI_STATUS_LAMP_FAILURE",           # Lamp Failure
        0x04: "DALI_STATUS_LAMP_POWER_ON",          # Power On
        0x08: "DALI_STATUS_LIMIT_ERROR",            # Limit error (an Arc-level > Max or < Min requested)
        0x10: "DALI_STATUS_FADE_RUNNING",           # A fade is running on the light
        0x20: "DALI_STATUS_RESET",                  # Device has been reset
        0x40: "DALI_STATUS_MISSING_SHORT_ADDRESS",  # Device hasn't been assigned a short-address
        0x80: "DALI_STATUS_POWER_FAILURE"           # Power failure has occurred
    }

    INSTANCE_TYPE = {
        0x01: "PUSH_BUTTON",            # Push button - generates short/long press events
        0x02: "ABSOLUTE_INPUT",         # Absolute input (slider/dial) - generates integer values
        0x03: "OCCUPANCY_SENSOR",       # Occupancy/motion sensor - generates occupied/unoccupied events
        0x04: "LIGHT_SENSOR",           # Light sensor - events not currently forwarded
        0x06: "GENERAL_PURPOSE_SENSOR"  # General sensor (water flow, power etc) - events not currently forwarded
    }

    def __init__(self, controllers: List[ZenController], logger: logging.Logger=None, narration: bool = True, multicast_group: str = "239.255.90.67", multicast_port: int = 6969):
        self.controllers = controllers # List of controllers, used to match events to controllers and to include controller object in callbacks
        self.logger = logger
        self.narration = narration
        self.multicast_group = multicast_group
        self.multicast_port = multicast_port
        
        # Setup logging if none provided
        if not self.logger:
            self.logger = logging.getLogger('ZenProtocol')
            self.logger.setLevel(logging.INFO)

        # Command socket for sending/receiving direct commands
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.command_socket.settimeout(0.5)
        
        # Event monitoring setup
        self.event_socket = None
        self.event_thread = None
        self.stop_event = Event()
        
        # Setup event listeners
        self.button_press_callback = None
        self.button_hold_callback = None
        self.absolute_input_callback = None
        self.level_change_callback = None
        self.group_level_change_callback = None
        self.scene_change_callback = None
        self.is_occupied_callback = None
        self.is_unoccupied_callback = None
        self.colour_changed_callback = None
        self.profile_changed_callback = None

    def __del__(self):
        """Cleanup when object is destroyed"""
        self.stop_event_monitoring()
        self.command_socket.close()

    # ============================
    # PACKET SENDING
    # ============================

    @staticmethod
    def calculate_checksum(packet: List[int]) -> int:
        acc = 0x00
        for d in packet:
            acc = d ^ acc
        return acc

    def send_basic(self,
                   controller: ZenController,
                   command: int,
                   address: int = 0x00,
                   data: List[int] = [0x00, 0x00, 0x00], 
                   return_type: str = 'bytes') -> Optional[Union[bytes, str, List[int], int, bool]]:
        if len(data) > 3: 
            raise ValueError("data must be 0-3 bytes")
        data = data + [0x00] * (3 - len(data))  # Pad data to 3 bytes
        response_data, response_code = self.send_packet(controller, command, [address] + data)
        if response_data is None and response_code is None:
            return None
        match response_code:
            case 0xA0: # OK
                match return_type:
                    case 'ok':
                        return True
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
                        raise ValueError(f"Invalid return_type: {return_type}")
            case 0xA2: # NO_ANSWER
                match return_type:
                    case 'ok':
                        return False
            case 0xA3: # ERROR
                if response_data:
                    error_code = response_data[0]
                    error_label = self.ERROR_CODES.get(error_code, f"UNKNOWN_ERROR_CODE_{hex(error_code)}")
                    if self.narration: print(f"Command error code: {error_label}")
                else:
                    if self.narration: print("Command error (no error code)")
            case _:
                if self.narration: print(f"Unknown response code: {response_code}")
        return None
        
    def send_colour(self, controller: ZenController, command: int, address: int, colour: ZenColourGeneric) -> Optional[bool]:
        """Send a DALI colour command."""
        response_data, response_code = self.send_packet(controller, command, [address] + list(colour.data()))
        match response_code:
            case 0xA0: # OK
                return True
            case 0xA2: # NO_ANSWER
                return False
        return None

    def send_dynamic(self, controller: ZenController, command: int, data: List[int]) -> Optional[bytes]:
        # Calculate data length and prepend it to data
        response_data, response_code = self.send_packet(controller, command, [len(data)] + data)
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
                    error_code = response_data[0]
                    error_label = self.ERROR_CODES.get(error_code, f"UNKNOWN_ERROR_CODE_{hex(error_code)}")
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
    
    def send_packet(self, controller: ZenController, command: int, data: List[int]) -> Optional[Tuple[bytes, int]]:
        
        # Acquire lock to ensure serial execution
        if not hasattr(self, '_send_lock'):
            self._send_lock = False
            
        # Wait up to 1 second for lock
        start_time = time.time()
        while self._send_lock:
            if time.time() - start_time > 1.0:
                if self.narration: print("Timeout waiting for lock")
                return None, None
            time.sleep(0.01)
            
        self._send_lock = True
        
        try:
            # Maintain sequence counter
            self._sequence_counter = (self._sequence_counter + 1) % 256 if hasattr(self, '_sequence_counter') else 0
            
            # Construct packet with checksum
            packet = [Const.MAGIC_BYTE, self._sequence_counter, command] + data
            checksum = self.calculate_checksum(packet)
            complete_packet = bytes(packet + [checksum])
            
            try:
                self.logger.debug(f"UDP packet sent to {controller.host}:{controller.port}: [{', '.join(f'0x{b:02x}' for b in complete_packet)}]")
                self.command_socket.sendto(complete_packet, (controller.host, controller.port))
                response, addr = self.command_socket.recvfrom(1024)
                
                self.logger.debug(f"UDP response: [{', '.join(f'0x{b:02x}' for b in response)}]")
                if self.narration: print(Fore.MAGENTA + f"    SEND: [{', '.join(f'0x{b:02x}' for b in complete_packet)}]" + Fore.CYAN + f"     RECV: [{', '.join(f'0x{b:02x}' for b in response)}]" + Style.RESET_ALL)

                # Verify response format and sequence counter
                if len(response) < 4:  # Minimum valid response is 4 bytes
                    self.logger.debug(f"UDP response too short (len={len(response)})")
                    if self.narration: print(f"UDP response too short (len={len(response)})")
                    return None, None
                    
                response_type = response[0]
                sequence = response[1]
                data_length = response[2]
                
                # Verify sequence counter matches
                if sequence != self._sequence_counter:
                    self.logger.debug(f"UDP response sequence counter mismatch (expected {self._sequence_counter}, got {sequence})")
                    if self.narration: print(f"UDP response sequence counter mismatch (expected {self._sequence_counter}, got {sequence})")
                    return None, None
                    
                # Verify total packet length matches data_length
                expected_length = 4 + data_length  # type + seq + len + data + checksum
                if len(response) != expected_length:
                    self.logger.debug(f"UDP response length mismatch (expected {expected_length}, got {len(response)})")
                    if self.narration: print(f"UDP response length mismatch (expected {expected_length}, got {len(response)})")
                    return None, None
                
                # Return data bytes if present, otherwise None
                if data_length > 0:
                    return response[3:3+data_length], response_type
                return None, response_type
            except socket.timeout:
                self.logger.debug(f"UDP packet response not received in time")
                if self.narration: print("UDP packet response not received in time")
                return None, None
            except Exception as e:
                self.logger.debug(f"UDP packet error sending command: {e}")
                if self.narration: print(f"UDP packet error sending command: {e}")
                return None, None
                
        finally:
            # Always release lock when done
            self._send_lock = False

    # ============================
    # EVENTS
    # ============================

    def start_event_monitoring(self,
                            button_press_callback=None,
                            button_hold_callback=None,
                            absolute_input_callback=None,
                            level_change_callback=None, 
                            group_level_change_callback=None,
                            scene_change_callback=None,
                            is_occupied_callback=None,
                            is_unoccupied_callback=None,
                            colour_changed_callback=None,
                            profile_changed_callback=None
                            ):

        # Check if event monitoring is already running
        if self.event_thread and self.event_thread.is_alive():
            if self.narration: print("Event monitoring already running")
            return
            
        # Setup event listeners
        self.button_press_callback = button_press_callback
        self.button_hold_callback = button_hold_callback
        self.absolute_input_callback = absolute_input_callback
        self.level_change_callback = level_change_callback
        self.group_level_change_callback = group_level_change_callback
        self.scene_change_callback = scene_change_callback
        self.is_occupied_callback = is_occupied_callback
        self.is_unoccupied_callback = is_unoccupied_callback
        self.colour_changed_callback = colour_changed_callback
        self.profile_changed_callback = profile_changed_callback
        
        self.stop_event.clear()
        self.event_thread = Thread(target=self._event_listener)
        self.event_thread.daemon = True
        self.event_thread.start()
        
        # Enable multicast packets on the controllers
        for controller in self.controllers:
            self.enable_tpi_event_emit(controller)

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
            # Setup multicast socket
            self.event_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.event_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.event_socket.bind(('', self.multicast_port))
            
            group = socket.inet_aton(self.multicast_group)
            mreq = struct.pack('4sl', group, socket.INADDR_ANY)
            self.event_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            while not self.stop_event.is_set():
                data, ip_address = self.event_socket.recvfrom(1024)
                
                self.logger.debug(f"Received multicast from {ip_address}: [{', '.join(f'0x{b:02x}' for b in data)}]")
                if self.narration: print(Fore.MAGENTA + f"    MULTICAST FROM: {ip_address}" + Fore.CYAN + f"     RECV: [{', '.join(f'0x{b:02x}' for b in data)}]" + Style.RESET_ALL)
                
                # Drop packet if it doesn't match the expected structure
                if len(data) < 2 or data[0:2] != bytes([0x5a, 0x43]):
                    self.logger.debug(f"Received multicast invalid packet: {ip_address} - {', '.join(f'0x{b:02x}' for b in data)}")
                    if self.narration: print(f"Received multicast invalid packet: {ip_address} - {', '.join(f'0x{b:02x}' for b in data)}")
                    continue

                # Extract packet fields
                macbytes = bytes.fromhex(data[2:8].hex())
                mac_address = ':'.join(f'{b:02x}' for b in data[2:8])
                target = int.from_bytes(data[8:10], byteorder='big')
                event_id = data[10]
                event_name = self.EVENT_TYPES.get(event_id, f"UNKNOWN_TYPE_{hex(event_id)}")
                payload_len = data[11]
                payload = data[12:-1]
                received_checksum = data[-1]

                self.logger.debug(f" ... IP: {ip_address} - MAC: {mac_address} - EVENT: {event_name} - TARGET: {target} - PAYLOAD: {payload}")
                if self.narration: print(Fore.CYAN + Style.DIM + f"         IP: {ip_address} - MAC: {mac_address} - EVENT: {event_name} - TARGET: {target} - PAYLOAD: {payload}" + Style.RESET_ALL)
                
                # Find controller where macbytes matches mac_address
                controller = next((c for c in self.controllers if c.mac_bytes == macbytes), None)

                # If no controller found, skip event
                if not controller:
                    self.logger.debug(f"Multicast packet is from unknown controller")
                    if self.narration: print(f"Multicast packet is from unknown controller")
                    continue

                # Verify data length
                if len(payload) != payload_len:
                    self.logger.debug(f"Multicast packet has invalid payload length: {len(payload)} != {payload_len}")
                    if self.narration: print(f"Multicast packet has invalid payload length: {len(payload)} != {payload_len}")
                    continue
                
                # Verify checksum
                calculated_checksum = self.calculate_checksum(list(data[:-1]))
                if received_checksum != calculated_checksum:
                    self.logger.debug(f"Multicast packet has invalid checksum: {calculated_checksum} != {received_checksum}")
                    if self.narration: print(f"Multicast packet has invalid checksum: {calculated_checksum} != {received_checksum}")
                    continue
                
                # Create event data dictionary with core data
                event_data = {
                    'raw_payload': payload,
                    'mac_address': mac_address,
                    'ip_address': ip_address,
                }
                
                match event_id:
                    case 0x00: # BUTTON_PRESS_EVENT
                        # Target - Control Device DALI Address 59 (+64 for Control devices)
                        # ======= Data bytes =======
                        # 12 0x05 (Data) 1st byte - Instance number. Useful for identifying the exact button on a keypad.
                        if self.button_press_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECD, number=target)
                            instance = ZenInstance(address=address, type=InstanceType.PUSH_BUTTON, number=payload[0])
                            self.button_press_callback(instance=instance, event_data=event_data)
                    case 0x01: # BUTTON_HOLD_EVENT
                        if self.button_hold_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECD, number=target)
                            instance = ZenInstance(address=address, type=InstanceType.PUSH_BUTTON, number=payload[0])
                            self.button_hold_callback(instance=instance, event_data=event_data)
                    case 0x02: # ABSOLUTE_INPUT_EVENT
                        if self.absolute_input_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECD, number=target)
                            instance = ZenInstance(address=address, type=InstanceType.PUSH_BUTTON, number=payload[0])
                            self.absolute_input_callback(instance=instance, event_data=event_data)
                    case 0x03: # LEVEL_CHANGE_EVENT
                        if self.level_change_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECG, number=target)
                            self.level_change_callback(address=address, arc_level=payload[0], event_data=event_data)
                    case 0x04: # GROUP_LEVEL_CHANGE_EVENT
                        if self.group_level_change_callback:
                            address = ZenAddress(controller=controller, type=AddressType.GROUP, number=target)
                            self.group_level_change_callback(address=address, arc_level=payload[0], event_data=event_data)
                    case 0x05: # SCENE_CHANGE_EVENT
                        if self.scene_change_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECG if target < 64 else AddressType.GROUP, number=target)
                            self.scene_change_callback(address=address, scene=payload[0], event_data=event_data)
                    case 0x06: # IS_OCCUPIED
                        # ======= Data bytes =======
                        # 12 0x05 1st byte - Instance number. Useful for identifying the exact sensor
                        # 13 0x01 2nd byte - Unneeded data
                        if self.is_occupied_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECD, number=target)
                            instance = ZenInstance(address=address, type=InstanceType.OCCUPANCY_SENSOR, number=payload[0])
                            self.is_occupied_callback(instance=instance, event_data=event_data)
                    case 0x07: # IS_UNOCCUPIED
                        # ======= Data bytes =======
                        # 12 0x05 1st byte - Instance number. Useful for identifying the exact sensor
                        # 13 0x01 2nd byte - Unneeded data
                        if self.is_unoccupied_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECD, number=target)
                            instance = ZenInstance(address=address, type=InstanceType.OCCUPANCY_SENSOR, number=payload[0])
                            self.is_unoccupied_callback(instance=instance, event_data=event_data)
                    case 0x08: # COLOUR_CHANGED
                        # ======= RGBWAF colour mode data bytes =======
                        # 12 0x80 RGBWAF Colour Mode
                        # 13 0xFF R - Red Byte
                        # 14 0x00 G - Green Byte
                        # 15 0x00 B - Blue Byte
                        # 16 0x00 W - White Byte
                        # 17 0x00 A - Amber Byte
                        # 18 0x00 F - Freecolour Byte
                        # ======= Colour Temperature data bytes =======
                        # 12 0x20 Colour Temperature
                        # 13 0xFF Kelvin - Hi Byte
                        # 14 0x00 Kelvin - Lo Byte
                        # ======= CIE 1931 XY data bytes =======
                        # 12 0x10 CIE 1931 XY
                        # 13 0xFF X - Hi Byte
                        # 14 0x00 X - Lo Byte
                        # 15 0xFF Y - Hi Byte
                        # 16 0x00 Y - Lo Byte
                        if self.colour_changed_callback:
                            address = ZenAddress(controller=controller, type=AddressType.ECG if target < 64 else AddressType.GROUP, number=target)
                            self.colour_changed_callback(address=address, colour=payload, event_data=event_data)
                    case 0x09: # PROFILE_CHANGED
                        # ======= Data bytes =======
                        # 12 0x00 Profile Hi Byte
                        # 13 0x0F Profile Lo Byte
                        if self.profile_changed_callback:
                            payload_int = int.from_bytes(payload, byteorder='big')
                            self.profile_changed_callback(controller=controller, profile=payload_int, event_data=event_data)
                
        except Exception as e:
            if self.narration: print(f"Event listener error: {e}")
        finally:
            if self.event_socket:
                self.event_socket.close()

    # ============================
    # API COMMANDS
    # ============================

    def query_group_label(self, address: ZenAddress) -> Optional[str]:
        """Get the label for a DALI Group. Returns a string, or None if no label is set."""
        return self.send_basic(address.controller, self.CMD["QUERY_GROUP_LABEL"], address.group(), return_type='str')
    
    def query_dali_device_label(self, address: ZenAddress, generic_if_none: bool=False) -> Optional[str]:
        """Query the label for a DALI device (control gear or control device). Returns a string, or None if no label is set."""
        label = self.send_basic(address.controller, self.CMD["QUERY_DALI_DEVICE_LABEL"], address.ecg_or_ecd(), return_type='str')
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
        return self.send_basic(controller, self.CMD["QUERY_PROFILE_LABEL"], 0x00, [0x00, profile_upper, profile_lower], return_type='str')
    
    def query_current_profile_number(self, controller: ZenController) -> Optional[int]:
        """Get the current/active Profile number for a controller. Returns int, else None if query fails."""
        response = self.send_basic(controller, self.CMD["QUERY_CURRENT_PROFILE_NUMBER"])
        if response and len(response) >= 2: # Profile number is 2 bytes, combine them into a single integer. First byte is high byte, second is low byte
            return (response[0] << 8) | response[1]
        return None

    def query_tpi_event_emit_state(self, controller: ZenController) -> Optional[bool]: # TODO: Check this command for validity. This call also supposedly returns a value to indicate if event filtering is active
        """Get the current TPI Event multicast emitter state for a controller. Returns True if enabled, False if disabled, None if query fails."""
        response = self.send_basic(controller, self.CMD["QUERY_TPI_EVENT_EMIT_STATE"])
        if response and len(response) >= 1:
            return response[0] > 0
        return None
    
    def dali_add_tpi_event_filter(self, address: ZenAddress, filter: ZenEventMask = ZenEventMask.all_events(), instance_number: int = 0xFF) -> bool:
        """Add a DALI TPI event filter to stop specific events from being broadcast.
        
        Args:
            address: ZenAddress to add filter for (broadcast = set for all)
            filter: Event mask indicating which events to filter (all events by default)
            instance_number: Instance number for ECD filters
            
        Returns:
            True if filter was added successfully, False otherwise
        """
        return self.send_basic(address.controller,
                             self.CMD["DALI_ADD_TPI_EVENT_FILTER"],
                             address.ecg_or_ecd_or_broadcast(),
                             [instance_number, filter.upper(), filter.lower()],
                             return_type='bool')
    
    def dali_clear_tpi_event_filter(self, address: ZenAddress, filter: ZenEventMask = ZenEventMask.all_events(), instance_number: int = 0xFF) -> bool:
        """Clear DALI TPI event filters to allow specific events to be broadcast again.
        
        Args:
            address: ZenAddress to clear filter for (broadcast = set for all)
            filter: ZenEventMask indicating which events to stop filtering (all events by default)
            instance_number: Instance number for ECD filters (0xFF for ECG)
            
        Returns:
            True if filter was cleared successfully, False otherwise
        """
        return self.send_basic(address.controller,
                             self.CMD["DALI_CLEAR_TPI_EVENT_FILTERS"],
                             address.ecg_or_ecd_or_broadcast(),
                             [instance_number, filter.upper(), filter.lower()],
                             return_type='bool')

    
    def query_dali_tpi_event_filters(self, address: ZenAddress, instance_number: int = 0xFF, start_at: int = 0) -> List[Dict]:
        """Query active DALI TPI event filters for an address.
        
        Args:
            address: ZenAddress to query filters for (broadcast = set for all)
            instance_number: Instance number to query (0xFF for ECG, or specific instance for ECD)
            start_at: Result index to start at (for paging results)
            
        Returns:
            List of dictionaries containing filter info, or None if query fails:
            [{
                'address': int,           # Address number
                'instance': int,          # Instance number 
                'event_mask': int         # 16-bit event mask
            }]
        """
        response = self.send_basic(address.controller, 
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

    def enable_tpi_event_emit(self, controller: ZenController, enable: bool = True) -> bool:
        """Enable or disable TPI Event emission. Returns True if successful, else False."""
        return self.send_basic(controller, self.CMD["ENABLE_TPI_EVENT_EMIT"], 0x01 if enable else 0x00, return_type='bool')

    def set_tpi_event_unicast_address(self, controller: ZenController, ip_address: str, port: int):
        """Configure TPI Events for Unicast mode with specified IP and port.
        
        Args:
            controller: ZenController instance
            ip_address: Target IP address for Unicast events (e.g. "192.168.1.100")
            port: Target UDP port for Unicast events (0-65535)
            
        Returns:
            bool: True if successful, False if failed
            
        Raises:
            ValueError: If port is invalid or IP address format is invalid
        """
        if not 0 <= port <= 65535: raise ValueError("Port must be between 0 and 65535")
        
        # Split port into upper and lower bytes
        port_upper = (port >> 8) & 0xFF 
        port_lower = port & 0xFF
        
        # Convert IP string to bytes
        try:
            ip_bytes = [int(x) for x in ip_address.split('.')]
            if len(ip_bytes) != 4 or not all(0 <= x <= 255 for x in ip_bytes):
                raise ValueError
        except ValueError:
            raise ValueError("Invalid IP address format")
            
        # Construct data payload: [port_upper, port_lower, ip1, ip2, ip3, ip4]
        data = [port_upper, port_lower] + ip_bytes
        
        return self.send_dynamic(controller, self.CMD["SET_TPI_EVENT_UNICAST_ADDRESS"], data)

    def query_tpi_event_unicast_address(self, controller: ZenController) -> Optional[Tuple[bool, bool, int, str]]:
        """Query TPI Events state and unicast configuration.
        Sends a Basic frame to query the TPI Event emit state, Unicast Port and Unicast Address.
       
        Args:
            controller: ZenController instance
            
        Returns:
            Optional tuple containing:
            - bool: Whether TPI Events are enabled
            - bool: Whether Unicast mode is enabled  
            - int: Configured unicast port
            - str: Configured unicast IP address
            
            Returns None if query fails
        """
        response = self.send_basic(controller, self.CMD["QUERY_TPI_EVENT_UNICAST_ADDRESS"])
        if response and len(response) >= 7:
            flags = response[0]
            tpi_events_enabled = (flags & 0x01) > 0
            unicast_enabled = (flags & 0x40) > 0
            port = (response[1] << 8) | response[2]
            ip = f"{response[3]}.{response[4]}.{response[5]}.{response[6]}"
            return (tpi_events_enabled, unicast_enabled, port, ip)
        return None

    def query_group_numbers(self, controller: ZenController) -> List[ZenAddress]:
        """Query a controller for Group Numbers in use. Returns a list of ZenAddress group instances."""
        groups = self.send_basic(controller, self.CMD["QUERY_GROUP_NUMBERS"], return_type='list')
        zen_groups = []
        if groups is not None:
            groups.sort()
            for group in groups:
                zen_groups.append(ZenAddress(controller=controller, type=AddressType.GROUP, number=group))
        return zen_groups
        

    def query_dali_colour(self, address: ZenAddress) -> Optional[ZenColourGeneric]:
        """Query colour information from a DALI address.
        
        Args:
            address: ZenAddress instance
            
        Returns:
            Optional tuple containing:
            - int: Colour mode (see DALI Colour Frame spec for modes)
            - List[int]: Colour values based on mode (e.g. RGBWAF values)
            
            Returns None if query fails
        """
        response = self.send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR"], address.ecg())
        if response and len(response) >= 1:
            match response[0]:
                case 0x00: # RGBWAF
                    return ZenColourRGBWAF(level=255, r=response[1], g=response[2], b=response[3], w=response[4], a=response[5], f=response[6])
                case 0x01: # CIE 1931 XY
                    return ZenColourXY(level=255, x=response[1], y=response[2])
                case 0x02: # Colour Temperature
                    return ZenColourTC(level=255, kelvin=(response[1] << 8) | response[2])
        return None
    
    def query_profile_numbers(self, controller: ZenController) -> Optional[List[int]]:
        """Query a controller for a list of available Profile Numbers. Returns a list of profile numbers, or None if query fails."""
        response = self.send_basic(controller, self.CMD["QUERY_PROFILE_NUMBERS"])
        if response and len(response) >= 2:
            # Response contains pairs of bytes for each profile number
            profile_numbers = []
            for i in range(0, len(response), 2):
                if i + 1 < len(response):
                    profile_num = (response[i] << 8) | response[i+1]
                    profile_numbers.append(profile_num)
            return profile_numbers
        return None

    def query_occupancy_instance_timers(self, instance: ZenInstance) -> Optional[Tuple[int, int, int, int]]:
        """Query timer values for a DALI occupancy sensor instance.
        
        Args:
            instance: ZenInstance instance
            
        Returns:
            Optional tuple containing:
            - int: Deadtime in seconds
            - int: Hold time in seconds  
            - int: Report time in seconds
            - int: Seconds since last OCCUPIED status (max 255)
            
            Returns None if query fails
        """
        response = self.send_basic(instance.address.controller, self.CMD["QUERY_OCCUPANCY_INSTANCE_TIMERS"], instance.address.ecd(), [0x00, 0x00, instance.number])
        if response and len(response) >= 5:
            deadtime = response[0]
            hold = response[1] 
            report = response[2]
            last_detect = response[4]  # Only using low byte since high byte is never populat
            return (deadtime, hold, report, last_detect)
        return None

    def query_instances_by_address(self, address: ZenAddress) -> List[ZenInstance]:
        """Query a DALI address (ECD) for associated instances. Returns a list of ZenInstance, or an empty list if nothing found."""
        response = self.send_basic(address.controller, self.CMD["QUERY_INSTANCES_BY_ADDRESS"], address.ecd())
        if response and len(response) >= 4:
            instances = []
            # Process groups of 4 bytes for each instance
            for i in range(0, len(response), 4):
                if i + 3 < len(response):
                    instances.append(ZenInstance(
                        address=address,
                        number=response[i], # first byte
                        type=response[i+1], # second byte
                        active=bool(response[i+2] & 0x02), # third byte, second bit
                        error=bool(response[i+2] & 0x01), # third byte, first bit
                    ))
            return instances
        return []

    def query_operating_mode_by_address(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG or ECD) for its operating mode. Returns an int containing the operating mode value, or None if the query fails."""
        response = self.send_basic(address.controller, self.CMD["QUERY_OPERATING_MODE_BY_ADDRESS"], address.ecg_or_ecd())
        if response and len(response) == 1:
            return response[0]  # Operating mode is in first byte
        return None

    def dali_colour(self, address: ZenAddress, colour: ZenColourGeneric) -> bool:
        """Set a DALI address (ECG, group, broadcast) to a colour. Returns True if command succeeded, False otherwise."""
        return self.send_colour(address.controller, self.CMD["DALI_COLOUR"], address.ecg_or_group_or_broadcast(), colour=colour)

    def query_group_by_number(self, address: ZenAddress) -> Optional[Tuple[int, bool, int]]: # TODO: change to a dict or special class?
        """Query a DALI group for its occupancy status and level. Returns a tuple containing group number, occupancy status, and actual level."""
        response = self.send_basic(address.controller, self.CMD["QUERY_GROUP_BY_NUMBER"], address.group())
        if response and len(response) == 3:
            group_num = response[0]
            occupancy = bool(response[1])
            level = response[2]
            return (group_num, occupancy, level)
        return None

    def query_scene_numbers_by_address(self, address: ZenAddress) -> Optional[List[int]]:
        """Query a DALI address (ECG) for associated scenes. Returns a list of scene numbers where levels have been set."""
        return self.send_basic(address.controller, self.CMD["QUERY_SCENE_NUMBERS_BY_ADDRESS"], address.ecg(), return_type='list')

    def query_scene_levels_by_address(self, address: ZenAddress) -> Optional[List[int]]:
        """Query a DALI address (ECG) for its DALI scene levels. Returns a list of 16 scene level values (0-254, or None if not part of scene)."""
        response = self.send_basic(address.controller, self.CMD["QUERY_SCENE_LEVELS_BY_ADDRESS"], address.ecg(), return_type='list')
        if response:
            return [None if x == 255 else x for x in response]
        return None
    
    def query_group_membership_by_address(self, address: ZenAddress) -> List[ZenAddress]:
        """Query an address (ECG) for which DALI groups it belongs to. Returns a list of ZenAddress group instances."""
        response = self.send_basic(address.controller, self.CMD["QUERY_GROUP_MEMBERSHIP_BY_ADDRESS"], address.ecg())
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
                    type=AddressType.GROUP,
                    number=number
                ))
            return zen_groups
        return []

    def query_dali_addresses_with_instances(self, controller: ZenController, start_address: int=0) -> List[ZenAddress]: # TODO: automate iteration over start_address=0, start_address=60, etc.
        """Query for DALI addresses that have instances associated with them.
        
        Due to payload restrictions, this needs to be called multiple times with different
        start addresses to check all possible devices (e.g. start_address=0, then start_address=60)
        
        Args:
            controller: ZenController instance
            start_address: Starting DALI address to begin searching from (0-127)
            
        Returns:
            List of DALI addresses that have instances, or None if query fails
        """
        addresses = self.send_basic(controller, self.CMD["QUERY_DALI_ADDRESSES_WITH_INSTANCES"], 0, [0,0,start_address], return_type='list')
        if not addresses:
            return []
        zen_addresses = []
        for number in addresses:
            if number >= 64:  # Only process valid device addresses (64-127)
                zen_addresses.append(ZenAddress(
                    controller=controller,
                    type=AddressType.ECD,
                    number=number-64 # subtract 64 to get actual DALI device address
                ))
        return zen_addresses
    
    def query_scene_numbers_for_group(self, address: ZenAddress) -> List[int]:
        """Query which DALI scenes are associated with a given group number. Returns list of scene numbers."""
        response = self.send_basic(address.controller, self.CMD["QUERY_SCENE_NUMBERS_FOR_GROUP"], address.group())
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
    
    def query_scene_label_for_group(self, address: ZenAddress, scene: int) -> Optional[str]:
        """Query the label for a scene (0-11) and group number combination. Returns string, or None if no label is set."""
        if not 0 <= scene < Const.MAX_SCENE: raise ValueError("Scene must be between 0 and 11")
        return self.send_basic(address.controller, self.CMD["QUERY_SCENE_LABEL_FOR_GROUP"], address.group(), [scene], return_type='str')
    
    def query_controller_version_number(self, controller: ZenController) -> Optional[str]:
        """Query the controller's version number. Returns string, or None if query fails."""
        response = self.send_basic(controller, self.CMD["QUERY_CONTROLLER_VERSION_NUMBER"])
        if response and len(response) == 3:
            return f"{response[0]}.{response[1]}.{response[2]}"
        return None
    
    def query_control_gear_dali_addresses(self, controller: ZenController) -> List[ZenAddress]:
        """Query which DALI control gear addresses are present in the database. Returns a list of ZenAddress instances."""
        response = self.send_basic(controller, self.CMD["QUERY_CONTROL_GEAR_DALI_ADDRESSES"])
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
                                type=AddressType.ECG,
                                number=number
                            )
                        )
            return addresses
        return []
    
    def dali_inhibit(self, address: ZenAddress, time_seconds: int) -> bool:
        """Inhibit sensors from changing a DALI address (ECG or group or broadcast) for specified time in seconds (0-65535). Returns True if acknowledged, else False."""
        time_hi = (time_seconds >> 8) & 0xFF  # Convert time to 16-bit value
        time_lo = time_seconds & 0xFF
        return self.send_basic(address.controller, self.CMD["DALI_INHIBIT"], address.ecg_or_group_or_broadcast(), [0x00, time_hi, time_lo], return_type='ok')
    
    def dali_scene(self, address: ZenAddress, scene: int) -> bool:
        """Send RECALL SCENE (0-11) to an address (ECG or group or broadcast). Returns True if acknowledged, else False."""
        if not 0 <= scene < Const.MAX_SCENE: raise ValueError("Scene number must be between 0 and 11")
        return self.send_basic(address.controller, self.CMD["DALI_SCENE"], address.ecg_or_group_or_broadcast(), [0x00, 0x00, scene], return_type='ok')
    
    def dali_arc_level(self, address: ZenAddress, level: int) -> bool:
        """Send DIRECT ARC level (0-254) to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        if not 0 <= level < Const.MAX_LEVEL: raise ValueError("Level must be between 0 and 254")
        return self.send_basic(address.controller, self.CMD["DALI_ARC_LEVEL"], address.ecg_or_group_or_broadcast(), [0x00, 0x00, level], return_type='ok')
    
    def dali_on_step_up(self, address: ZenAddress) -> bool:
        """Send ON AND STEP UP to an address (ECG or group or broadcast). If a device is off, it will turn it on. If a device is on, it will step up. No fade."""
        return self.send_basic(address.controller, self.CMD["DALI_ON_STEP_UP"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_step_down_off(self, address: ZenAddress) -> bool:
        """Send STEP DOWN AND OFF to an address (ECG or group or broadcast). If a device is at min, it will turn off. If a device isn't yet at min, it will step down. No fade."""
        return self.send_basic(address.controller, self.CMD["DALI_STEP_DOWN_OFF"], address.ecg_or_group_or_broadcast(), return_type='ok')

    def dali_up(self, address: ZenAddress) -> bool:
        """Send DALI UP to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        return self.send_basic(address.controller, self.CMD["DALI_UP"], address.ecg_or_group_or_broadcast(), return_type='ok')

    def dali_down(self, address: ZenAddress) -> bool:
        """Send DALI DOWN to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        return self.send_basic(address.controller, self.CMD["DALI_DOWN"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_recall_max(self, address: ZenAddress) -> bool:
        """Send RECALL MAX to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return self.send_basic(address.controller, self.CMD["DALI_RECALL_MAX"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_recall_min(self, address: ZenAddress) -> bool:
        """Send RECALL MIN to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return self.send_basic(address.controller, self.CMD["DALI_RECALL_MIN"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_off(self, address: ZenAddress) -> bool:
        """Send OFF to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return self.send_basic(address.controller, self.CMD["DALI_OFF"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    def dali_query_level(self, address: ZenAddress) -> Optional[int]:
        """Query the Arc Level for a DALI address (ECG or group). Returns arc level as int, or None if mixed levels."""
        response = self.send_basic(address.controller, self.CMD["DALI_QUERY_LEVEL"], address.ecg_or_group(), return_type='int')
        if response == 255: return None # 255 indicates mixed levels
        return response
    
    def dali_query_control_gear_status(self, address: ZenAddress) -> Optional[List[str]]:
        """Query the Status for a DALI address (ECG).
                    
        Returns:
            Optional[List[str]]: List of active status flags based on DALI_STATUS_MASKS,
                               None if query fails
        """
        response = self.send_basic(address.controller, self.CMD["DALI_QUERY_CONTROL_GEAR_STATUS"], address.ecg())
        if response and len(response) == 1:
            # Extract status flags from response byte
            status_byte = response[0]
            active_flags = []
            for mask, description in self.DALI_STATUS_MASKS.items():
                if status_byte & mask:
                    active_flags.append(description)
            return active_flags
        return None
    
    def dali_query_cg_type(self, address: ZenAddress) -> Optional[List[int]]:
        """Query device type information for a DALI address (ECG).
            
        Returns:
            Optional[List[int]]: List of device type numbers that the control gear belongs to.
                                Returns empty list if device doesn't exist.
                                Returns None if query fails.
        """
        response = self.send_basic(address.controller, self.CMD["DALI_QUERY_CG_TYPE"], address.ecg())
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
        return self.send_basic(address.controller, self.CMD["DALI_QUERY_LAST_SCENE"], address.ecg_or_group_or_broadcast(), return_type='int')
    
    def dali_query_last_scene_is_current(self, address: ZenAddress) -> Optional[bool]:
        """Query if the last heard scene is the current active scene for a DALI address (ECG or group or broadcast).
        Returns True if still active, False if another command has been issued since, or None if query fails."""
        return self.send_basic(address.controller, self.CMD["DALI_QUERY_LAST_SCENE_IS_CURRENT"], address.ecg_or_group_or_broadcast(), return_type='bool')
    
    def dali_query_min_level(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG) for its minimum level (0-254). Returns the minimum level if successful, None if query fails."""
        return self.send_basic(address.controller, self.CMD["DALI_QUERY_MIN_LEVEL"], address.ecg(), return_type='int')

    def dali_query_max_level(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG) for its maximum level (0-254). Returns the maximum level if successful, None if query fails."""
        return self.send_basic(address.controller, self.CMD["DALI_QUERY_MAX_LEVEL"], address.ecg(), return_type='int')
    
    def dali_query_fade_running(self, address: ZenAddress) -> Optional[bool]:
        """Query a DALI address (ECG) if a fade is currently running. Returns True if a fade is currently running, False if not, None if query fails."""
        return self.send_basic(address.controller, self.CMD["DALI_QUERY_FADE_RUNNING"], address.ecg(), return_type='bool')
    
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
        return self.send_basic(address.controller, self.CMD["DALI_ENABLE_DAPC_SEQ"], address.ecg(), return_type='bool')
    
    def query_dali_ean(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG or ECD) for its European Article Number (EAN/GTIN). Returns an integer if successful, None if query fails."""
        response = self.send_basic(address.controller, self.CMD["QUERY_DALI_EAN"], address.ecg_or_ecd())
        if response and len(response) == 6:
            ean = 0
            for byte in response:
                ean = (ean << 8) | byte
            return ean
        return None
    
    def query_dali_serial(self, address: ZenAddress) -> Optional[int]:
        """Query a DALI address (ECG or ECD) for its Serial Number. Returns an integer if successful, None if query fails."""
        response = self.send_basic(address.controller, self.CMD["QUERY_DALI_SERIAL"], address.ecg_or_ecd())
        if response and len(response) == 8:
            # Convert 8 bytes to decimal integer
            serial = 0
            for byte in response:
                serial = (serial << 8) | byte
            return serial
        return None
    
    def dali_custom_fade(self, address: ZenAddress, target_level: int, fade_time_seconds: float) -> bool:
        """Fade a DALI address (ECG or group) to a level (0-254) with a custom fade time in seconds (0-65535). Returns True if successful, else False."""
        if not 0 <= target_level < Const.MAX_LEVEL:
            raise ValueError("Target level must be between 0 and 254")
        if not 0 <= fade_time_seconds <= 65535:
            raise ValueError("Fade time must be between 0 and 65535 seconds")

        # Convert fade time to integer seconds and split into high/low bytes
        seconds = int(fade_time_seconds)
        seconds_hi = (seconds >> 8) & 0xFF
        seconds_lo = seconds & 0xFF
        
        return self.send_basic(
            address.controller,
            self.CMD["DALI_CUSTOM_FADE"],
            address.ecg_or_group(),
            [target_level, seconds_hi, seconds_lo],
            return_type='ok'
        )
    
    def dali_go_to_last_active_level(self, address: ZenAddress) -> bool:
        """Command a DALI Address (ECG or group) to go to its "Last Active" level. Returns True if successful, else False."""
        return self.send_basic(address.controller, self.CMD["DALI_GO_TO_LAST_ACTIVE_LEVEL"], address.ecg_or_group(), return_type='ok')
    
    def query_dali_instance_label(self, instance: ZenInstance, generic_if_none: bool=False) -> Optional[str]:
        """Query the label for a DALI Instance. Returns a string, or None if not set. Optionally, returns a generic label if the instance label is not set."""
        label = self.send_basic(instance.address.controller, self.CMD["QUERY_DALI_INSTANCE_LABEL"], instance.address.ecd(), [0x00, 0x00, instance.number], return_type='str')
        if label is None and generic_if_none:
            instance_type = instance.type if isinstance(instance, ZenInstance) else 0
            label = instance.address.controller.label + " " + self.INSTANCE_TYPE.get(instance.type, "UNKNOWN").title().replace("_", " ")  + " " + str(instance.number)
        return label

    def change_profile_number(self, controller: ZenController, profile: int) -> bool:
        """Change the active profile number (0-65535). Returns True if successful, else False."""
        if not 0 <= profile <= 0xFFFF: raise ValueError("Profile number must be between 0 and 65535")
        profile_hi = (profile >> 8) & 0xFF
        profile_lo = profile & 0xFF
        return self.send_basic(controller, self.CMD["CHANGE_PROFILE_NUMBER"], 0x00, [0x00, profile_hi, profile_lo], return_type='ok')
    
    def query_instance_groups(self, instance: ZenInstance) -> Optional[Tuple[int, int, int]]:
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
        response = self.send_basic(
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
        return self.send_basic(address.controller, self.CMD["QUERY_DALI_FITTING_NUMBER"], address.ecg_or_ecd(), return_type='str')
        
    def query_dali_instance_fitting_number(self, instance: ZenInstance) -> Optional[str]:
        """Query a DALI instance for its fitting number. Returns a string (e.g. '1.2.0') or None if query fails."""
        return self.send_basic(instance.address.controller, self.CMD["QUERY_DALI_INSTANCE_FITTING_NUMBER"], instance.address.ecd(), [0x00, 0x00, instance.number], return_type='str')
    
    def query_controller_label(self, controller: ZenController) -> Optional[str]:
        """Request the label for the controller. Returns the controller's label string, or None if query fails."""
        return self.send_basic(controller, self.CMD["QUERY_CONTROLLER_LABEL"], return_type='str')
    
    def query_controller_fitting_number(self, controller: ZenController) -> Optional[str]:
        """Request the fitting number string for the controller itself. Returns the controller's fitting number (e.g. '1'), or None if query fails."""
        return self.send_basic(controller, self.CMD["QUERY_CONTROLLER_FITTING_NUMBER"], return_type='str')

    def query_is_dali_ready(self, controller: ZenController) -> bool:
        """Query whether the DALI line is ready or has a fault. Returns True if DALI line is ready, False if there is a fault."""
        return self.send_basic(controller, self.CMD["QUERY_IS_DALI_READY"], return_type='ok')
    
    def query_controller_startup_complete(self, controller: ZenController) -> bool:
        """Query whether the controller has finished its startup sequence. Returns True if startup is complete, False if still in progress.

        The startup sequence performs DALI queries such as device type, current arc-level, GTIN, 
        serial number, etc. The more devices on a DALI line, the longer startup will take to complete.
        For a line with only a handful of devices, expect it to take approximately 1 minute.
        Waiting for the startup sequence to complete is particularly important if you wish to 
        perform queries about DALI.
        """
        return self.send_basic(controller, self.CMD["QUERY_CONTROLLER_STARTUP_COMPLETE"], return_type='ok')
    
    def override_dali_button_led_state(self, instance: ZenInstance, led_state: bool) -> bool:
        """Override the LED state for a DALI push button. State is True for LED on, False for LED off. Returns true if command succeeded, else False."""
        return self.send_basic(instance.address.controller,
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
        response = self.send_basic(instance.address.controller,
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

        Note: For custom fades started via DALI_CUSTOM_FADE, this can only stop
        fades that were started with the same target address. For example, you 
        cannot stop a custom fade on a single address if it was started as part
        of a group or broadcast fade.
        """
        return self.send_basic(address.controller, self.CMD["DALI_STOP_FADE"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
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
        response = self.send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR_FEATURES"], address.ecg())
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
        response = self.send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR_TEMP_LIMITS"], address.ecg())
        if response and len(response) == 10:
            return {
                'physical_warmest': (response[0] << 8) | response[1],
                'physical_coolest': (response[2] << 8) | response[3],
                'soft_warmest': (response[4] << 8) | response[5],
                'soft_coolest': (response[6] << 8) | response[7],
                'step_value': (response[8] << 8) | response[9]
            }
        return None
    
    def set_system_variable(self, controller: ZenController, variable_number: int, value: int) -> bool:
        """Set a system variable (0-47) value (0-65535) on the controller. Returns True if successful, else False."""
        if not 0 <= variable_number < Const.MAX_SYSVAR:
            raise ValueError("Variable number must be between 0 and 47")
        if not 0 <= value <= 65535:
            raise ValueError("Value must be between 0 and 65535")
            
        # Split value into high/low bytes
        value_hi = (value >> 8) & 0xFF
        value_lo = value & 0xFF
        
        return self.send_basic(controller, self.CMD["SET_SYSTEM_VARIABLE"], variable_number, [0x00, value_hi, value_lo], return_type='ok')
    
    def query_system_variable(self, controller: ZenController, variable_number: int) -> Optional[int]:
        """Query the controller for the value of a system variable (0-47). Returns the variable's value (0-65535) if successful, else None."""
        if not 0 <= variable_number < Const.MAX_SYSVAR:
            raise ValueError("Variable number must be between 0 and 47")
            
        response = self.send_basic(controller, self.CMD["QUERY_SYSTEM_VARIABLE"], variable_number)
        if response and len(response) == 2:
            value = (response[0] << 8) | response[1]
            if value == 0xFFFF:
                return None
            return value
        return None

    # ============================
    # CONVENIENCE COMMANDS
    # ============================  
    
    def return_to_scheduled_profile(self, controller: ZenController) -> bool: # Use 0xFFFF for scheduled profile, see page 91
        return self.send_basic(controller, self.CMD["CHANGE_PROFILE_NUMBER"], 0x00, [0x00, 0xFF, 0xFF], return_type='ok')

    def disable_tpi_event_emit(self, controller: ZenController):
        """Disable TPI event emit."""
        self.enable_tpi_event_emit(controller, False)

    def dali_illuminate(self, address: ZenAddress, level: Optional[int] = None, kelvin: Optional[int] = None) -> bool:
        """Set a DALI address (ECG, group, broadcast) to a kelvin (None, or 1000-20000) and/or level (None or 0-254). Returns True if succeeded, else False."""
        if kelvin is not None:
            return self.send_colour(controller=address.controller,
                                    command=self.CMD["DALI_COLOUR"],
                                    address=address.ecg_or_group_or_broadcast(),
                                    colour=ZenColourTC(level=level if level is not None else 255, kelvin=kelvin)
                                    )
        elif level is not None:
            return self.dali_arc_level(address, level)
        else:
            raise ValueError("Either kelvin or arc_level must be provided")
    