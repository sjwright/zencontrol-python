import socket
import struct
import time
from typing import Optional, Tuple, List, Union, Dict
from threading import Thread, Event
from colorama import Fore, Back, Style
from dataclasses import dataclass, field

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
        
        # Implemented but not tested
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
        "OVERRIDE_DALI_BUTTON_LED_STATE": 0x29,     # Override a button LED state
        "QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE": 0x30, # Query button last known button LED state
        "SET_TPI_EVENT_UNICAST_ADDRESS": 0x40,      # Set a TPI Events unicast address and port
        "QUERY_TPI_EVENT_UNICAST_ADDRESS": 0x41,    # Query TPI Events State, unicast address and port

        # Not yet implemented (will wait for a use case)
        "DALI_ADD_TPI_EVENT_FILTER": 0x31,          # Request that filters be added for DALI TPI Events
        "QUERY_DALI_TPI_EVENT_FILTERS": 0x32,       # Query DALI TPI Event filters on a address
        "DALI_CLEAR_TPI_EVENT_FILTERS": 0x33,       # Request that DALI TPI Event filters be cleared

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

    def __init__(self, controllers: List[ZenController], multicast_group: str = "239.255.90.67", multicast_port: int = 6969):
        self.controllers = controllers # List of controllers, used to match events to controllers and return controller object with callbacks
        self.multicast_group = multicast_group
        self.multicast_port = multicast_port
        
        # Command socket for sending/receiving direct commands
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.command_socket.settimeout(1.0)
        
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
    
    def valid_dali_address(self, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None, ecd: Optional[int]=None, broadcast: bool=False) -> int:
        # print(f"valid_dali_address: address={address}, gear={gear}, group={group}, ecd={ecd}, broadcast={broadcast}")
        if gear is not None:
            if not 0 <= gear <= 63: raise ValueError("Control Gear address must be between 0 and 63")
            return gear
        if group is not None:
            if not 0 <= group <= 15: raise ValueError("Group number must be between 0 and 15")
            return 64 + group
        if ecd is not None:
            if not 0 <= ecd <= 63: raise ValueError("Control Device address must be between 0 and 63")
            return 64 + ecd
        if address is not None:
            if not 0 <= address <= 79: raise ValueError("Address must be between 0 and 79")
            return address
        if broadcast: return 255
        raise ValueError("No valid DALI address provided")

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
                   control: int = 0x04,
                   return_type: str = 'bytes') -> Optional[Union[bytes, str, List[int], int, bool]]:
        if len(data) > 3: 
            raise ValueError("data must be 0-3 bytes")
        data = data + [0x00] * (3 - len(data))  # Pad data to 3 bytes
        response_data, response_code = self.send_packet(controller, command, [address] + data, control)
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
                            return response_data.decode('ascii') # bytes(response).decode().rstrip('\x00')
                        except UnicodeDecodeError:
                            return None
                    case 'list':
                        if response_data: return list(response_data)
                    case 'int':
                        if response_data and len(response_data) == 1: return int(response_data[0])
                    case 'bool':
                        if response_data and len(response_data) == 1: return bool(response_data[0])
                    case 'ok':
                        raise ValueError(f"type 'ok' should not return a value")
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
                    print(f"Command error code: {error_label}")
                else:
                    print("Command error (no error code)")
            case _:
                print(f"Unknown response code: {response_code}")
        return None
        
    def send_colour(self,
                   controller: ZenController,
                   command: int,
                   address: int,
                   arc_level: int,
                   colour_type: int,
                   colour_data: List[int],
                   control: int = 0x04
                   ) -> Optional[bytes]:
        """Send a DALI colour command.
        
        Args:
            command: Command byte (0x0E for DALI colour command)
            address: DALI address (0-63)
            arc_level: DALI arc level (0-254)
            colour_type: Colour type (0x10=XY, 0x20=TC, 0x80=RGBWAF)
            colour_data: List of colour values based on type:
                XY: [X_hi, X_lo, Y_hi, Y_lo]
                TC: [TC_hi, TC_lo]
                RGBWAF: [Red, Green, Blue, White, Amber, Free]
            control: Control byte (default 0x04)
            
        Returns:
            Optional[bytes]: Response data if successful, None otherwise
        """
        data = [address, arc_level, colour_type] + colour_data
        response_data, response_code = self.send_packet(controller, command, data, control)
        # Check response type
        match response_code:
            case 0xA0: # OK
                pass  # Request processed successfully
            case 0xA1: # ANSWER
                pass  # Answer is in data bytes
            case 0xA2: # NO_ANSWER
                if response_data > 0:
                    print(f"No answer with code: {response_data}")
                return None
            case 0xA3: # ERROR
                if response_data:
                    error_code = response_data[0]
                    error_label = self.ERROR_CODES.get(error_code, f"UNKNOWN_ERROR_CODE_{hex(error_code)}")
                    print(f"Command error code: {error_label}")
                else:
                    print("Command error (no error code)")
                return None
            case _:
                print(f"Unknown response type: {response_code}")
                return None
        if response_data:
            return response_data
        return None
        

    def send_dynamic(self, 
                    controller: ZenController,
                    command: int,
                    data: List[int],
                    control: int = 0x04
                    ) -> Optional[bytes]:
        # Calculate data length and prepend it to data
        response_data, response_code = self.send_packet(controller, command, [len(data)] + data, control)
        # Check response type
        match response_code:
            case 0xA0: # OK
                pass  # Request processed successfully
            case 0xA1: # ANSWER
                pass  # Answer is in data bytes
            case 0xA2: # NO_ANSWER
                if response_data > 0:
                    print(f"No answer with code: {response_data}")
                return None
            case 0xA3: # ERROR
                if response_data:
                    error_code = response_data[0]
                    error_label = self.ERROR_CODES.get(error_code, f"UNKNOWN_ERROR_CODE_{hex(error_code)}")
                    print(f"Command error code: {error_label}")
                else:
                    print("Command error (no error code)")
                return None
            case _:
                print(f"Unknown response type: {response_code}")
                return None
        if response_data:
            return response_data
        return None
    
    def send_packet(self,
                    controller: ZenController,
                    command: int,
                    data: List[int],
                    control: int = 0x04
                    ) -> Optional[Tuple[bytes, int]]:
        # Acquire lock to ensure serial execution
        if not hasattr(self, '_send_lock'):
            self._send_lock = False
            
        # Wait up to 2 seconds for lock
        start_time = time.time()
        while self._send_lock:
            if time.time() - start_time > 1.0:
                print("Timeout waiting for lock")
                return None
            time.sleep(0.01)
            
        self._send_lock = True
        
        try:
            # Maintain sequence counter
            self._sequence_counter = (self._sequence_counter + 1) % 256 if hasattr(self, '_sequence_counter') else 0
            
            # Construct packet with checksum
            packet = [control, self._sequence_counter, command] + data
            checksum = self.calculate_checksum(packet)
            complete_packet = bytes(packet + [checksum])
            
            try:
                self.command_socket.settimeout(2.0)  # Set 2 second timeout
                self.command_socket.sendto(complete_packet, (controller.host, controller.port))
                response, addr = self.command_socket.recvfrom(1024)
                
                if hasattr(self, 'debug') and self.debug:
                    print(Fore.MAGENTA
                          + f"    SEND: [{', '.join(f'0x{b:02x}' for b in complete_packet)}]"
                          + Fore.CYAN
                          + f"     RECV: [{', '.join(f'0x{b:02x}' for b in response)}]"
                          + Style.RESET_ALL)

                # Verify response format and sequence counter
                if len(response) < 4:  # Minimum valid response is 4 bytes
                    print("Response too short")
                    return None
                    
                response_type = response[0]
                sequence = response[1]
                data_length = response[2]
                
                # Verify sequence counter matches
                if sequence != self._sequence_counter:
                    print("Response sequence counter mismatch")
                    return None
                    
                # Verify total packet length matches data_length
                expected_length = 4 + data_length  # type + seq + len + data + checksum
                if len(response) != expected_length:
                    print(f"Invalid response length. Expected {expected_length}, got {len(response)}")
                    return None
                
                # Return data bytes if present, otherwise None
                if data_length > 0:
                    return response[3:3+data_length], response_type
                return None, response_type
            except socket.timeout:
                print("No response received in time")
                return None
            except Exception as e:
                print(f"Error sending command: {e}")
                return None
                
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
            print("Event monitoring already running")
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
                
                if hasattr(self, 'debug') and self.debug:
                    print(Fore.MAGENTA
                          + f"    MULTICAST FROM: {ip_address}"
                          + Fore.CYAN
                          + f"     RECV: [{', '.join(f'0x{b:02x}' for b in data)}]"
                          + Style.RESET_ALL)
                
                # Drop packet if it doesn't match the expected structure
                if len(data) < 2 or data[0:2] != bytes([0x5a, 0x43]):
                    print(f"Received multicast invalid packet: {ip_address} - {', '.join(f'0x{b:02x}' for b in data)}")
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

                if hasattr(self, 'debug') and self.debug:
                    print(Fore.CYAN
                          + Style.DIM
                          + f"         IP: {ip_address} - MAC: {mac_address} - EVENT: {event_name} - TARGET: {target} - PAYLOAD: {payload}" + Style.RESET_ALL)
                
                # Find controller where macbytes matches mac_address
                controller = next((c for c in self.controllers if c.mac_bytes == macbytes), None)

                # If no controller found, skip event
                if not controller:
                    print(f"Received multicast from unknown controller: {ip_address} - {', '.join(f'0x{b:02x}' for b in data)}")
                    continue

                # Verify data length
                if len(payload) != payload_len:
                    print(f"Invalid multicast payload length: {len(payload)} != {payload_len}")
                    continue
                
                # Verify checksum
                calculated_checksum = self.calculate_checksum(list(data[:-1]))
                if received_checksum != calculated_checksum:
                    # Drop packet if checksum is invalid
                    print(f"Invalid multicast checksum: {calculated_checksum} != {received_checksum}")
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
                            self.button_press_callback(controller=controller, address=target, instance=payload[0], event_data=event_data)
                    case 0x01: # BUTTON_HOLD_EVENT
                        if self.button_hold_callback:
                            self.button_hold_callback(controller=controller, address=target, instance=payload[0], event_data=event_data)
                    case 0x02: # ABSOLUTE_INPUT_EVENT
                        if self.absolute_input_callback:
                            self.absolute_input_callback(controller=controller, address=target, instance=payload[0], event_data=event_data)
                    case 0x03: # LEVEL_CHANGE_EVENT
                        if self.level_change_callback:
                            self.level_change_callback(controller=controller, gear=target, arc_level=payload[0], event_data=event_data)
                    case 0x04: # GROUP_LEVEL_CHANGE_EVENT
                        if self.group_level_change_callback:
                            self.group_level_change_callback(controller=controller, group=target, arc_level=payload[0], event_data=event_data)
                    case 0x05: # SCENE_CHANGE_EVENT
                        if self.scene_change_callback:
                            self.scene_change_callback(controller=controller, address=target, scene=payload[0], event_data=event_data)
                    case 0x06: # IS_OCCUPIED
                        # ======= Data bytes =======
                        # 12 0x05 1st byte - Instance number. Useful for identifying the exact sensor
                        # 13 0x01 2nd byte - Unneeded data
                        if self.is_occupied_callback:
                            self.is_occupied_callback(controller=controller, target=target, instance=payload[0], event_data=event_data)
                    case 0x07: # IS_UNOCCUPIED
                        # ======= Data bytes =======
                        # 12 0x05 1st byte - Instance number. Useful for identifying the exact sensor
                        # 13 0x01 2nd byte - Unneeded data
                        if self.is_unoccupied_callback:
                            self.is_unoccupied_callback(controller=controller, target=target, instance=payload[0], event_data=event_data)
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
                            self.colour_changed_callback(controller=controller, address=target, colour=payload, event_data=event_data)
                    case 0x09: # PROFILE_CHANGED
                        # ======= Data bytes =======
                        # 12 0x00 Profile Hi Byte
                        # 13 0x0F Profile Lo Byte
                        if self.profile_changed_callback:
                            payload_int = int.from_bytes(payload, byteorder='big')
                            self.profile_changed_callback(controller=controller, profile=payload_int, event_data=event_data)
                
        except Exception as e:
            print(f"Event listener error: {e}")
        finally:
            if self.event_socket:
                self.event_socket.close()

    # ============================
    # API COMMANDS
    # ============================

    def query_group_label(self, controller: ZenController, group: int) -> Optional[str]:
        """Get the label for a DALI Group.
        
        Args:
            controller: ZenController instance
            group: Group number (0-15)
            
        Returns:
            Optional[str]: The group label if one exists, None if no label is set
                Labels are limited to 64 bytes in length.
                
        Raises:
            ValueError: If group_number is not between 0-15
        """
        address = self.valid_dali_address(group=group)
        return self.send_basic(controller, self.CMD["QUERY_GROUP_LABEL"], address, return_type='str')
    
    def query_dali_device_label(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, ecd: Optional[int]=None) -> Optional[str]:
        """Query the label for a DALI device (control gear or control device).
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for control gear, 64-127 for control devices)
            gear: DALI gear address (0-63)
            ecd: DALI ECD address (0-63)
            
        Returns:
            Optional[str]: The device label if successful, None if the device has no label
            or if the query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, ecd=ecd)
        return self.send_basic(controller, self.CMD["QUERY_DALI_DEVICE_LABEL"], address, return_type='str')
        
    def query_profile_label(self, controller: ZenController, profile: int) -> Optional[str]:
        """Get the label for a Profile given a Profile number.
        
        Args:
            controller: ZenController instance
            profile (int): Profile number (0-65535)
            
        Returns:
            Optional[str]: The profile label if successful, None if no label exists
                or if query fails
        """
        # Profile numbers are 2 bytes long, so check valid range
        if not 0 <= profile <= 65535:
            raise ValueError("Profile number must be between 0 and 65535")
            
        # Split profile number into upper and lower bytes
        profile_upper = (profile >> 8) & 0xFF
        profile_lower = profile & 0xFF
        
        return self.send_basic(controller, self.CMD["QUERY_PROFILE_LABEL"], 0x00, [0x00, profile_upper, profile_lower], return_type='str')
    
    def query_current_profile_number(self, controller: ZenController) -> Optional[int]:
        """Get the current/active Profile number.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[int]: The current active profile number (0-65535) if successful,
                None if query fails
        """
        response = self.send_basic(controller, self.CMD["QUERY_CURRENT_PROFILE_NUMBER"])
        if response and len(response) >= 2:
            # Profile number is 2 bytes, combine them into a single integer
            # First byte is high byte, second is low byte
            return (response[0] << 8) | response[1]
        return None

    def query_tpi_event_emit_state(self, controller: ZenController) -> Optional[bool]:
        """Get the current TPI Event multicast emitter state.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[bool]: True if TPI Events are enabled, False if disabled, None if query fails.
                          Values > 1 indicate event filtering is active (see TPI Event Modes).
        """
        response = self.send_basic(controller, self.CMD["QUERY_TPI_EVENT_EMIT_STATE"])
        if response and len(response) >= 1:
            return response[0] > 0
        return None
    
    def enable_tpi_event_emit(self, controller: ZenController, enable: bool = True) -> bool:
        """Enable TPI Event emission.
        
        Enables the controller to emit TPI Events via multicast or unicast (if configured).
        Events can be filtered using add_tpi_event_filter() to prevent specific events 
        from being emitted.

        Args:
            controller: ZenController instance
            enable: True to enable event emission, False to disable
            
        Returns:
            bool: True if successful, False if failed
        """
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

    def query_group_numbers(self, controller: ZenController) -> Optional[List[int]]:
        """Query the list of DALI Group Numbers present on the controller.
        
        Sends a Basic frame to query the list of DALI Group Numbers. This includes groups that:
        - Have control gear members in the database
        - Are set up in the groups section on the cloud
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[List[int]]: List of group numbers (0-15), or None if query fails
        """
        return self.send_basic(controller, self.CMD["QUERY_GROUP_NUMBERS"], return_type='list')

    def query_dali_colour(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> Optional[Tuple[int, List[int]]]:
        """Query colour information from a DALI address.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-79)
            gear: DALI gear address (0-63)
            group: DALI group number (0-15)
            
        Returns:
            Optional tuple containing:
            - int: Colour mode (see DALI Colour Frame spec for modes)
            - List[int]: Colour values based on mode (e.g. RGBWAF values)
            
            Returns None if query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group)
        response = self.send_basic(controller, self.CMD["QUERY_DALI_COLOUR"], address)
        if response and len(response) >= 1:
            colour_mode = response[0]
            colour_values = list(response[1:])
            return (colour_mode, colour_values)
        return None
    
    def query_profile_numbers(self, controller: ZenController) -> Optional[List[int]]:
        """Query the list of Profile Numbers available on the controller.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional list of profile numbers (each profile number is 16-bit).
            Returns None if query fails.
        """
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

    def query_occupancy_instance_timers(self, controller: ZenController, instance: int, ecd: Optional[int]=None) -> Optional[Tuple[int, int, int, int]]:
        """Query timer values for a DALI occupancy sensor instance.
        
        Args:
            controller: ZenController instance
            instance: Instance number of the occupancy sensor (0-31)
            ecd: DALI ECD address (0-63)
            
        Returns:
            Optional tuple containing:
            - int: Deadtime in seconds
            - int: Hold time in seconds  
            - int: Report time in seconds
            - int: Seconds since last OCCUPIED status (max 255)
            
            Returns None if query fails
        """
        address = self.valid_dali_address(ecd=ecd)
        response = self.send_basic(controller, self.CMD["QUERY_OCCUPANCY_INSTANCE_TIMERS"], address, [0x00, 0x00, instance])
        if response and len(response) >= 5:
            deadtime = response[0]
            hold = response[1] 
            report = response[2]
            last_detect = response[4]  # Only using low byte since high byte is never populated
            return (deadtime, hold, report, last_detect)
        return None


    INSTANCE_TYPE = {
        0x01: "PUSH_BUTTON",            # Push button - generates short/long press events
        0x02: "ABSOLUTE_INPUT",         # Absolute input (slider/dial) - generates integer values
        0x03: "OCCUPANCY_SENSOR",       # Occupancy/motion sensor - generates occupied/unoccupied events
        0x04: "LIGHT_SENSOR",           # Light sensor - events not currently forwarded
        0x06: "GENERAL_PURPOSE_SENSOR"  # General sensor (water flow, power etc) - events not currently forwarded
    }

    def query_instances_by_address(self, controller: ZenController, ecd: int) -> Optional[List[Tuple[int, int, int, int]]]:
        """Query instances associated with a DALI address.
        
        Args:
            controller: ZenController instance
            ecd: DALI address to query (0-127)
            
        Returns:
            Optional list of tuples containing instance metadata:
            - int: Instance number (0-31)
            - int: Instance type 
            - int: Status bits
            - int: State bits
            
            Returns None if query fails or no instances found
        """
        address = self.valid_dali_address(ecd=ecd)
        response = self.send_basic(controller, self.CMD["QUERY_INSTANCES_BY_ADDRESS"], address)
        if response and len(response) >= 4:
            instances = []
            # Process groups of 4 bytes for each instance
            for i in range(0, len(response), 4):
                if i + 3 < len(response):
                    instance_num = response[i]
                    instance_type = self.INSTANCE_TYPE.get(response[i+1], "UNKNOWN")
                    status_bits = response[i+2]
                    state_bits = response[i+3]
                    is_selected = bool(state_bits & 0x01)
                    is_disabled = bool(state_bits & 0x02)
                    no_targets = bool(state_bits & 0x04)
                    is_soft_disabled = bool(state_bits & 0x08)
                    has_sysvar_targets = bool(state_bits & 0x10)
                    has_db_ops = bool(state_bits & 0x20)
                    instances.append((instance_num, instance_type, status_bits, state_bits))
            return instances
        return None

    def query_operating_mode_by_address(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, ecd: Optional[int]=None) -> Optional[int]:
        """Query the operating mode for a DALI device at the given address.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-127)
            gear: DALI gear address (0-63)
            ecd: DALI ECD address (0-63)
            
        Returns:
            Optional int containing the operating mode value.
            Returns None if query fails or device not found.
        """
        address = self.valid_dali_address(address=address, gear=gear, ecd=ecd)
        response = self.send_basic(controller, self.CMD["QUERY_OPERATING_MODE_BY_ADDRESS"], address)
        if response and len(response) == 1:
            return response[0]  # Operating mode is in first byte
        return None

    def dali_colour(self, controller: ZenController, arc_level: int, colour_type: int, colour_values: List[int], address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send a DALI Colour command to set colour values on a DALI device.
        
        Args:
            controller: ZenController instance
            arc_level: Arc level (0-254, 254=MASK)
            colour_type: Colour type (0=RGBWAF, 1=XY)
            colour_values: List of colour values based on type:
                         - For RGBWAF: [red, green, blue, white, amber, free] (0-255 each)
                         - For XY: [x, y] (0-65535 each)
            address: DALI address (0-127)
            gear: DALI gear address (0-63)
            group: DALI group number (0-15)
                         
        Returns:
            bool: True if successful, False if failed
            
        Raises:
            ValueError: If parameters are invalid
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group)
        if not 0 <= arc_level <= 254: raise ValueError("Arc level must be between 0 and 254")
        if not 0 <= colour_type <= 1: raise ValueError("Colour type must be 0 (RGBWAF) or 1 (XY)")
            
        # Validate colour values based on type
        if colour_type == 0:  # RGBWAF
            if len(colour_values) != 6:
                raise ValueError("RGBWAF requires 6 colour values")
            if not all(0 <= x <= 255 for x in colour_values):
                raise ValueError("RGBWAF values must be between 0 and 255")
                
            data = [arc_level, colour_type] + colour_values
                
        else:  # XY
            if len(colour_values) != 2:
                raise ValueError("XY requires 2 colour values")
            if not all(0 <= x <= 65535 for x in colour_values):
                raise ValueError("XY values must be between 0 and 65535")
                
            # Convert XY values to bytes
            data = [arc_level, colour_type]
            for value in colour_values:
                data.append((value >> 8) & 0xFF)  # High byte
                data.append(value & 0xFF)         # Low byte
                
        return self.send_colour(controller, self.CMD["DALI_COLOUR"], address, arc_level, colour_type, colour_values)

    def query_group_by_number(self, controller: ZenController, group: int) -> Optional[Tuple[int, bool, int]]:
        """Query information for a DALI group by its number (0-15)
            
        Args:
            controller: ZenController instance
            group: Group number (0-15)
            
        Returns:
            Optional tuple containing:
            - int: Group number (0-15)
            - bool: Group occupancy status 
            - int: Group actual level (0-254)
            
            Returns None if query fails or group not found
        """
        response = self.send_basic(controller, self.CMD["QUERY_GROUP_BY_NUMBER"], group)
        if response and len(response) == 3:
            group_num = response[0]
            occupancy = bool(response[1])
            level = response[2]
            return (group_num, occupancy, level)
        return None

    def query_scene_numbers_by_address(self, controller: ZenController, gear: int) -> Optional[List[int]]:
        """Query DALI Scene numbers associated with a DALI address.

        Gets the list of DALI scene numbers that have a level value set (<255) for the given device.

        Args:
            controller: ZenController instance
            gear: DALI gear address (0-63)

        Returns:
            Optional[List[int]]: List of scene numbers (0-15) that have levels set for this device.
                               Returns None if query fails or device has no scenes configured.
        """
        address = self.valid_dali_address(gear=gear)
        return self.send_basic(controller, self.CMD["QUERY_SCENE_NUMBERS_BY_ADDRESS"], address, return_type='list')

    def query_scene_levels_by_address(self, controller: ZenController, gear: int) -> Optional[List[int]]:
        """Query DALI scene level values associated with a DALI address.

        Gets all 16 scene level values (0-15) configured for the given control gear.
        A level value of 0xFF (255) indicates the control gear is not part of that scene.

        Args:
            controller: ZenController instance
            gear: DALI gear address (0-63)

        Returns:
            Optional[List[int]]: List of 16 scene level values (0-254), with None for scenes 
                               that the control gear is not part of (level=255).
                               Returns None if query fails.
        """
        address = self.valid_dali_address(gear=gear)
        list = self.send_basic(controller, self.CMD["QUERY_SCENE_LEVELS_BY_ADDRESS"], address, return_type='list')
        if list:
            return [None if x == 255 else x for x in list]
        return None
    
    def query_group_membership_by_address(self, controller: ZenController, gear: int) -> Optional[List[int]]:
        """Query which DALI groups a device at the given address belongs to.
        
        Args:
            controller: ZenController instance
            gear: DALI gear address (0-63)
            
        Returns:
            Optional[List[int]]: List of group numbers (0-15) that the device belongs to,
                               sorted in ascending order. Returns None if query fails.
        """
        address = self.valid_dali_address(gear=gear)
        response = self.send_basic(controller, self.CMD["QUERY_GROUP_MEMBERSHIP_BY_ADDRESS"], address)
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
            return sorted(groups)
        return None
    
    def query_dali_addresses_with_instances(self, controller: ZenController, start_address: int) -> Optional[List[int]]:
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
        return [addr - 64 for addr in addresses] # subtract 64 to get actual DALI device addresses
    
    def query_scene_numbers_for_group(self, controller: ZenController, group: int) -> Optional[List[int]]:
        """Query which DALI scenes are associated with a given group number.
        This only returns scenes that have been configured via the cloud platform.
        
        Args:
            controller: ZenController instance
            group: Group number (0-15)
            
        Returns:
            Optional[List[int]]: List of scene numbers (0-15) that the group has configured,
                               sorted in ascending order. Returns None if query fails.
        """
        if not 0 <= group <= 15: raise ValueError("Group must be between 0 and 15")
        response = self.send_basic(controller, self.CMD["QUERY_SCENE_NUMBERS_FOR_GROUP"], group)
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
        return None
    
    def query_scene_label_for_group(self, controller: ZenController, scene: int, group: int) -> Optional[str]:
        """Query the label for a scene and group number combination.
        
        The scene must be configured via the cloud platform. If the scene doesn't exist,
        will receive no answer.
        
        Args:
            controller: ZenController instance
            scene: Scene number (0-11)
            group: Group number (0-15)
            
        Returns:
            Optional[str]: The scene label string if found, None if not found or query fails
        """
        if not 0 <= group <= 15: raise ValueError("Group must be between 0 and 15")
        if not 0 <= scene <= 11: raise ValueError("Scene must be between 0 and 11")
        return self.send_basic(controller, self.CMD["QUERY_SCENE_LABEL_FOR_GROUP"], group, [scene], return_type='str')
    
    def query_controller_version_number(self, controller: ZenController) -> Optional[str]:
        """Query the controller's version number.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[str]: The controller's version number as a string, or None if query fails
        """
        response = self.send_basic(controller, self.CMD["QUERY_CONTROLLER_VERSION_NUMBER"])
        if response and len(response) == 3:
            return f"{response[0]}.{response[1]}.{response[2]}"
        return None
    
    def query_control_gear_dali_addresses(self, controller: ZenController) -> Optional[List[int]]:
        """Query which DALI control gear addresses are present in the database.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[List[int]]: List of control gear addresses (0-63) that are present in the 
                               database, sorted in ascending order. Returns None if query fails.
        """
        response = self.send_basic(controller, self.CMD["QUERY_CONTROL_GEAR_DALI_ADDRESSES"])
        if response and len(response) == 8:
            addresses = []
            # Process each byte which represents 8 addresses
            for byte_index, byte_value in enumerate(response):
                # Check each bit in the byte
                for bit_index in range(8):
                    if byte_value & (1 << bit_index):
                        # Calculate actual address from byte and bit position
                        address = byte_index * 8 + bit_index
                        addresses.append(address)
            return sorted(addresses)
        return None
    
    def dali_inhibit(self, controller: ZenController, time_seconds: int, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Inhibit sensors from changing a DALI address for specified time in seconds.
        
        Args:
            controller: ZenController instance
            time_seconds: Duration in seconds to inhibit sensor control (0-65535)
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        time_hi = (time_seconds >> 8) & 0xFF  # Convert time to 16-bit value
        time_lo = time_seconds & 0xFF
        return self.send_basic(controller, self.CMD["DALI_INHIBIT"], address, [0x00, time_hi, time_lo], return_type='ok')
    
    def dali_scene(self, controller: ZenController, scene: int, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send RECALL SCENE to an address.
        
        Args:
            controller: ZenController instance
            scene: Scene number to call (0-11)
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_SCENE"], address, [0x00, 0x00, scene], return_type='ok')
    

    def dali_arc_level(self, controller: ZenController, level: int, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send DIRECT ARC to an address. Will fade to the new level.
        
        Args:
            controller: ZenController instance 
            level: Light level (0-254)
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        if not 0 <= level <= 254:
            raise ValueError("Level must be between 0 and 254")
        return self.send_basic(controller, self.CMD["DALI_ARC_LEVEL"], address, [0x00, 0x00, level], return_type='ok')
    
    def dali_on_step_up(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send ON AND STEP UP to an address. No fade.
        If a device is off, it will turn it on. If a device is on, it will step up.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_ON_STEP_UP"], address, return_type='ok')
    
    def dali_step_down_off(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send STEP DOWN AND OFF to an address. No fade.
        If a device is at min, it will turn off. If a device isn't yet at min, it will step down.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_STEP_DOWN_OFF"], address, return_type='ok')

    def dali_up(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send DALI UP to an address. Will fade to the new level.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_UP"], address, return_type='ok')

    def dali_down(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send DALI DOWN to an address. Will fade to the new level.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_DOWN"], address, return_type='ok')
    
    def dali_recall_max(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send RECALL MAX to an address. No fade.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_RECALL_MAX"], address, return_type='ok')
    
    def dali_recall_min(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send RECALL MIN to an address. No fade.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_RECALL_MIN"], address, return_type='ok')
    
    def dali_off(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Send OFF to an address. No fade.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            bool: True if command was acknowledged, False if it failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_OFF"], address, return_type='ok')
    
    def dali_query_level(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> Optional[int]:
        """Query the Arc Level for a DALI address.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-79 for groups, 255 for broadcast)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            Optional[int]: The DALI level (0-254) if successful, None if mixed levels (255).
                         Returns 0 if address doesn't exist in database or group has no devices.
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        response = self.send_basic(controller, self.CMD["DALI_QUERY_LEVEL"], address, return_type='int')
        if response == 255: return None
        return response
    
    def dali_query_control_gear_status(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> Optional[List[str]]:
        """Query the Status for a DALI control gear address.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63 for gear, 64-80 for groups?!? 81 for broadcast?!?) TODO: check how this works
            gear: Gear number (0-63)
            group: Group number (0-15)
                    
        Returns:
            Optional[List[str]]: List of active status flags based on DALI_STATUS_MASKS,
                               None if query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        response = self.send_basic(controller, self.CMD["DALI_QUERY_CONTROL_GEAR_STATUS"], address)
        if response and len(response) == 1:
            # Extract status flags from response byte
            status_byte = response[0]
            active_flags = []
            for mask, description in self.DALI_STATUS_MASKS.items():
                if status_byte & mask:
                    active_flags.append(description)
            return active_flags
        return None
    
    def dali_query_cg_type(self, controller: ZenController, gear: int) -> Optional[List[int]]:
        """Query control gear device type information for a DALI address.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63). Does not work for groups or broadcast.
            
        Returns:
            Optional[List[int]]: List of device type numbers that the control gear belongs to.
                                Returns empty list if device doesn't exist.
                                Returns None if query fails.
        """
        if not 0 <= gear <= 63: raise ValueError("Address must be between 0 and 63")
        response = self.send_basic(controller, self.CMD["DALI_QUERY_CG_TYPE"], gear)
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
    
    def dali_query_last_scene(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> Optional[int]:
        """Query the last heard Scene for a DALI address.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            Optional[int]: Last heard scene number (0-15), None if query fails
            
        Note:
            Changes to a single DALI device done through group or broadcast scene commands
            also change the last heard scene for the individual device address. For example,
            if A10 is member of G0 and we send a scene command to G0, A10 will show the 
            same last heard scene as G0.
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_QUERY_LAST_SCENE"], address, return_type='int')
    
    def dali_query_last_scene_is_current(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> Optional[bool]:
        """Query if the last heard scene is the current active scene for a DALI address.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-63)
            gear: Gear number (0-63)
            group: Group number (0-15)
            
        Returns:
            Optional[bool]: True if the last heard scene is currently active,
                False if an Arc Level or other command was issued after the last Scene command,
                None if query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group, broadcast=True)
        return self.send_basic(controller, self.CMD["DALI_QUERY_LAST_SCENE_IS_CURRENT"], address, return_type='bool')
    
    def dali_query_min_level(self, controller: ZenController, gear: int) -> Optional[int]:
        """Query the minimum level for a DALI address.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63)
            
        Returns:
            Optional[int]: The minimum level (0-254) if successful, None if query fails
        """
        address = self.valid_dali_address(gear=gear)
        return self.send_basic(controller, self.CMD["DALI_QUERY_MIN_LEVEL"], address, return_type='int')

    def dali_query_max_level(self, controller: ZenController, gear: int) -> Optional[int]:
        """Query the maximum level for a DALI address.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63)
            
        Returns:
            Optional[int]: The maximum level (0-254) if successful, None if query fails
        """
        address = self.valid_dali_address(gear=gear)
        return self.send_basic(controller, self.CMD["DALI_QUERY_MAX_LEVEL"], address, return_type='int')
    
    def dali_query_fade_running(self, controller: ZenController, gear: int) -> Optional[bool]:
        """Query if a fade is currently running for a DALI address.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63)
            
        Returns:
            Optional[bool]: True if a fade is currently running, False if not,
                None if query fails
        """
        address = self.valid_dali_address(gear=gear)
        return self.send_basic(controller, self.CMD["DALI_QUERY_FADE_RUNNING"], address, return_type='bool')
    
    def dali_enable_dapc_sequence(self, controller: ZenController, gear: int) -> Optional[bool]:
        """Begin a DALI Direct Arc Power Control (DAPC) Sequence.
        
        DAPC allows overriding of the fade rate for immediate level setting. The sequence
        continues for 250ms. If no arc levels are received within 250ms, the sequence ends
        and normal fade rates resume.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63)
            
        Returns:
            Optional[bool]: True if successful, False if failed, None if no response
        """
        address = self.valid_dali_address(gear=gear)
        return self.send_basic(controller, self.CMD["DALI_ENABLE_DAPC_SEQ"], address, return_type='bool')
    
    def query_dali_ean(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, ecd: Optional[int]=None) -> Optional[int]:
        """Query the European Article Number (EAN/GTIN) for a DALI device.
        
        For Control Gear addresses (0-63), use the normal address.
        For Control Devices, the address must be offset by +64 (64-127).
        
        Args:
            controller: ZenController instance
            address: DALI address (0-127)
            gear: DALI address (0-63)
            ecd: DALI address (0-63)
            
        Returns:
            Optional[int]: The EAN/GTIN as a decimal integer if successful,
                None if query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, ecd=ecd)    
        response = self.send_basic(controller, self.CMD["QUERY_DALI_EAN"], address)
        if response and len(response) == 6:
            # Convert 6 bytes to decimal integer
            ean = 0
            for byte in response:
                ean = (ean << 8) | byte
            return ean
        return None
    
    def query_dali_serial(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, ecd: Optional[int]=None) -> Optional[int]:
        """Query the Serial Number for a DALI device.
        
        For Control Gear addresses (0-63), use the normal address.
        For Control Devices, the address must be offset by +64 (64-127).
        
        Args:
            controller: ZenController instance
            address: DALI address (0-127)
            gear: DALI address (0-63)
            ecd: DALI address (0-63)
            
        Returns:
            Optional[int]: The 8-byte serial number as a decimal integer if successful,
                None if query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, ecd=ecd)
        response = self.send_basic(controller, self.CMD["QUERY_DALI_SERIAL"], address)
        if response and len(response) == 8:
            # Convert 8 bytes to decimal integer
            serial = 0
            for byte in response:
                serial = (serial << 8) | byte
            return serial
        return None
    
    def dali_custom_fade(self, controller: ZenController, target_level: int, fade_time_seconds: float, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Run a fade to a level on a DALI address with a custom fade time in seconds.
        
        Args:
            controller: ZenController instance
            target_level: Target arc level (0-254)
            fade_time_seconds: Fade duration in seconds (0-65535)
            address: DALI address (0-79)
            gear: DALI address (0-63)
            group: DALI address (0-15)
        
        Returns:
            bool: True if command succeeded, False if failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group)
        if not 0 <= target_level <= 254:
            raise ValueError("Target level must be between 0 and 254")
        if not 0 <= fade_time_seconds <= 65535:
            raise ValueError("Fade time must be between 0 and 65535 seconds")

        # Convert fade time to integer seconds and split into high/low bytes
        seconds = int(fade_time_seconds)
        seconds_hi = (seconds >> 8) & 0xFF
        seconds_lo = seconds & 0xFF
        
        return self.send_basic(
            controller,
            self.CMD["DALI_CUSTOM_FADE"],
            address,
            [target_level, seconds_hi, seconds_lo],
            return_type='ok'
        )
    
    def dali_go_to_last_active_level(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Command a DALI Address to go to its "Last Active" level.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-79)
            gear: DALI address (0-63)
            group: DALI address (0-15)
            
        Returns:
            bool: True if successful, False if failed
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group)
        return self.send_basic(controller, self.CMD["DALI_GO_TO_LAST_ACTIVE_LEVEL"], address, return_type='ok')
    
    def query_dali_instance_label(self, controller: ZenController, instance: int, ecd: int) -> Optional[str]:
        """Query the label for a DALI Instance given a Control Device and Instance Number.
        (Note: TPI expects ECD number between 64-127 but this implementation expects 0-63)
        
        Args:
            controller: ZenController instance
            instance: Instance number (0-31)
            ecd: Control Device address (0-63)
            
        Returns:
            Optional[str]: The instance label if successful, None if query fails
        """
        address = self.valid_dali_address(ecd=ecd)
        if not 0 <= instance <= 31: raise ValueError("Instance number must be between 0 and 31")
        return self.send_basic(controller, self.CMD["QUERY_DALI_INSTANCE_LABEL"], address, [0x00, 0x00, instance], return_type='str')

    def change_profile_number(self, controller: ZenController, profile: int) -> bool:
        """Change the active profile number.
        
        Args:
            profile_number: Profile number (0-65535)
            
        Returns:
            bool: True if successful, False if failed
        """
        if not 0 <= profile <= 0xFFFF: raise ValueError("Profile number must be between 0 and 65535")
        profile_hi = (profile >> 8) & 0xFF
        profile_lo = profile & 0xFF
        return self.send_basic(controller, self.CMD["CHANGE_PROFILE_NUMBER"], 0x00, [0x00, profile_hi, profile_lo], return_type='ok')
    
    def return_to_scheduled_profile(self, controller: ZenController) -> bool: # Use 0xFFFF for scheduled profile
        return self.send_basic(controller, self.CMD["CHANGE_PROFILE_NUMBER"], 0x00, [0x00, 0xFF, 0xFF], return_type='ok')
    
    def query_instance_groups(self, controller: ZenController, instance: int, ecd: int) -> Optional[Tuple[int, int, int]]:
        """Query the group targets associated with a DALI instance.
        
        Args:
            controller: ZenController instance
            instance: Instance number (0-31)
            ecd: Control Device address (0-63)
            
        Returns:
            Optional tuple containing:
            - int: Primary group number (0-15, or 255 if not configured)
            - int: First group number (0-15, or 255 if not configured) 
            - int: Second group number (0-15, or 255 if not configured)
            
            Returns None if query fails
            
        The Primary group typically represents where the physical device resides.
        A group number of 255 (0xFF) indicates that no group has been configured.
        """
        if not 0 <= ecd <= 63: raise ValueError("Control Device address must be between 0 and 63")
        if not 0 <= instance <= 31: raise ValueError("Instance number must be between 0 and 31")
            
        response = self.send_basic(
            controller,
            self.CMD["QUERY_INSTANCE_GROUPS"], 
            64+ecd,
            [0x00, 0x00, instance],
            return_type='list'
        )
        
        if response and len(response) >= 3:
            return (response[0], response[1], response[2])
        return None
    
    def query_dali_fitting_number(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, ecd: Optional[int]=None) -> Optional[str]:
        """Query the fitting number string (e.g. '1.2') for a DALI device.
        
        If device isn't named or doesn't exist, returns a default identifier of 
        'ZenController ID.Dali Address' for control gear and 'ZenController ID.Dali Address + 100' 
        for control devices.

        Args:
            controller: ZenController instance
            address: DALI device address (0-63 for control gear, 64-127 for control devices)
            gear: DALI device address (0-63 for control gear)
            ecd: DALI device address (0-63 for control devices)
            
        Returns:
            Optional string containing the fitting number (e.g. '1.2')
            Returns None if query fails
        """
        address = self.valid_dali_address(address=address, gear=gear, ecd=ecd)
        return self.send_basic(controller, self.CMD["QUERY_DALI_FITTING_NUMBER"], address, return_type='str')
        
    def query_dali_instance_fitting_number(self, controller: ZenController, instance: int, ecd: int) -> Optional[str]:
        """Query the fitting number string (e.g. '1.2.0') for a DALI instance.
        
        Args:
            controller: ZenController instance
            instance: Instance number (0-31)
            ecd: Control Device address (0-63)
            
        Returns:
            Optional string containing the fitting number (e.g. '1.2.0')
            Returns None if query fails
        """
        address = self.valid_dali_address(ecd=ecd)
        if not 0 <= instance <= 31: raise ValueError("Instance number must be between 0 and 31")
        return self.send_basic(controller, self.CMD["QUERY_DALI_INSTANCE_FITTING_NUMBER"], address, [0x00, 0x00, instance], return_type='str')
    
    def query_controller_label(self, controller: ZenController) -> Optional[str]:
        """Request the label for the controller.
        Sends a Basic frame to query the controller's label string.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[str]: The controller's label string, or None if query fails
        """
        return self.send_basic(controller, self.CMD["QUERY_CONTROLLER_LABEL"], return_type='str')
    
    def query_controller_fitting_number(self, controller: ZenController) -> Optional[str]:
        """Request the fitting number string for the controller itself.
        
        Args:
            controller: ZenController instance
            
        Returns:
            Optional[str]: The controller's fitting number (e.g. '1'), or None if query fails
        """
        return self.send_basic(controller, self.CMD["QUERY_CONTROLLER_FITTING_NUMBER"], return_type='str')

    def query_is_dali_ready(self, controller: ZenController) -> bool:
        """Query whether the DALI line is ready or has a fault.
        
        Args:
            controller: ZenController instance
            
        Returns:
            bool: True if DALI line is ready, False if there is a fault
        """
        return self.send_basic(controller, self.CMD["QUERY_IS_DALI_READY"], return_type='ok')
    
    def query_controller_startup_complete(self, controller: ZenController) -> bool:
        """Query whether the controller has finished its startup sequence.
        
        The startup sequence performs DALI queries such as device type, current arc-level, GTIN, 
        serial number, etc. The more devices on a DALI line, the longer startup will take to complete.
        For a line with only a handful of devices, expect it to take approximately 1 minute.
        Waiting for the startup sequence to complete is particularly important if you wish to 
        perform queries about DALI.
        
        Args:
            controller: ZenController instance
            
        Returns:
            bool: True if startup is complete, False if still in progress
        """
        return self.send_basic(controller, self.CMD["QUERY_CONTROLLER_STARTUP_COMPLETE"], return_type='ok')
    
    def override_dali_button_led_state(self, controller: ZenController, led_state: bool, instance: int, ecd: int) -> bool:
        """Override the LED state for a DALI push button.
        
        Args:
            controller: ZenController instance
            led_state: True for LED on, False for LED off
            instance: Instance number (0-31)
            ecd: Control Device address (0-63)
            
        Returns:
            bool: True if command succeeded, False otherwise
        """
        address = self.valid_dali_address(ecd=ecd)
        if not 0 <= instance <= 31: raise ValueError("Instance number must be between 0 and 31")
        return self.send_basic(controller, self.CMD["OVERRIDE_DALI_BUTTON_LED_STATE"], 64 + address, [0x00, 0x00, instance], return_type='ok')
    
    def query_last_known_dali_button_led_state(self, controller: ZenController, instance: int, ecd: int) -> Optional[bool]:
        """Query the last known LED state for a DALI push button.
        
        Args:
            controller: ZenController instance
            instance: Instance number (0-31)
            ecd: Control Device address (0-63)
            
        Returns:
            Optional bool: True if LED is on, False if LED is off, None if query failed
            
        Note: The "last known" LED state may not be the actual physical LED state.
        This only works for LED modes where the controller or TPI caller is managing
        the LED state. In many cases, the control device itself manages its own LED.
        """
        address = self.valid_dali_address(ecd=ecd)
        if not 0 <= instance <= 31: raise ValueError("Instance number must be between 0 and 31")
        response = self.send_basic(controller, self.CMD["QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE"], address, [0x00, 0x00, instance])
        
        if response and len(response) == 1:
            match response[0]:
                case 0x01:
                    return False
                case 0x02:
                    return True
        return None

    def dali_stop_fade(self, controller: ZenController, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Stop any running DALI fade on an address/group/broadcast.
        
        Args:
            controller: ZenController instance
            address: DALI address (0-79)
            gear: DALI address (0-63)
            group: DALI address (0-15)
            
        Returns:
            bool: True if command succeeded, False otherwise
            
        Note: For custom fades started via DALI_CUSTOM_FADE, this can only stop
        fades that were started with the same target address. For example, you 
        cannot stop a custom fade on a single address if it was started as part
        of a group or broadcast fade.
        """
        if not 0 <= address <= 80:
            raise ValueError("Address must be between 0 and 80")
            
        return self.send_basic(controller, self.CMD["DALI_STOP_FADE"], address, return_type='ok')
    
    def query_dali_colour_features(self, controller: ZenController, gear: int) -> Optional[dict]:
        """Query the colour features/capabilities of a DALI device.
        
        Args:
            controller: ZenController instance
            gear: DALI address (0-63)
            
        Returns:
            Dictionary containing colour capabilities, or None if query failed:
            {
                'supports_xy': bool,          # Supports CIE 1931 XY coordinates
                'primary_count': int,         # Number of primaries (0-7)
                'rgbwaf_channels': int,      # Number of RGBWAF channels (0-7)
            }
        """
        address = self.valid_dali_address(gear=gear)
        response = self.send_basic(controller, self.CMD["QUERY_DALI_COLOUR_FEATURES"], address)
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
    
    def query_dali_colour_temp_limits(self, controller: ZenController, gear: int) -> Optional[dict]:
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
        address = self.valid_dali_address(gear=gear)
        response = self.send_basic(controller, self.CMD["QUERY_DALI_COLOUR_TEMP_LIMITS"], address)
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
        """Set a system variable value on the controller.
        
        Args:
            controller: ZenController instance
            variable_number (int): Variable number (0-47) to set
            value (int): Value to set (0-65535)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not 0 <= variable_number <= 47:
            raise ValueError("Variable number must be between 0 and 47")
        if not 0 <= value <= 65535:
            raise ValueError("Value must be between 0 and 65535")
            
        # Split value into high/low bytes
        value_hi = (value >> 8) & 0xFF
        value_lo = value & 0xFF
        
        return self.send_basic(controller, self.CMD["SET_SYSTEM_VARIABLE"], variable_number, [0x00, value_hi, value_lo], return_type='ok')
    
    def query_system_variable(self, controller: ZenController, variable_number: int) -> Optional[int]:
        """Query a system variable value from the controller.
        
        Args:
            controller: ZenController instance
            variable_number (int): Variable number (0-47) to query
            
        Returns:
            Optional[int]: The variable value (0-65535) if successful, None if variable has no value
        """
        if not 0 <= variable_number <= 47:
            raise ValueError("Variable number must be between 0 and 47")
            
        response = self.send_basic(controller, self.CMD["QUERY_SYSTEM_VARIABLE"], variable_number)
        if response and len(response) == 2:
            value = (response[0] << 8) | response[1]
            if value == 0xFFFF:
                return None
            return value
        return None

    # ============================
    # CUSTOM COMMANDS
    # ============================  

    def disable_tpi_event_emit(self, controller: ZenController):
        """Disable TPI event emit.
        """
        self.enable_tpi_event_emit(controller, False)

    def dali_colour_tc(self, controller: ZenController, kelvin: int, arc_level: int=255, address: Optional[int]=None, gear: Optional[int]=None, group: Optional[int]=None) -> bool:
        """Set a DALI target to a colour temperature.
        
        Args:
            controller: ZenController instance
            kelvin: DALI colour temperature in Kelvin (1000-20000)
            arc_level: DALI arc level (0-255)
            address: DALI address (0-79)
            gear: DALI address (0-63)
            group: DALI address (0-15)
            
        Returns:
            bool: True if command succeeded, False otherwise
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group)
        if not 0 <= arc_level <= 255:
            raise ValueError("Arc level must be between 0 and 255") # 255 = no change
        if not 2000 <= kelvin <= 65535:
            raise ValueError("Kelvin must be between 0 and 65535")
        kelvin_hi = (kelvin >> 8) & 0xFF
        kelvin_lo = kelvin & 0xFF
        return self.send_colour(controller, self.CMD["DALI_COLOUR"], address, arc_level, 0x20, [kelvin_hi, kelvin_lo])

    def dali_illuminate(self,
                        controller: ZenController,
                        level: Optional[int] = None,
                        kelvin: Optional[int] = None,
                        address: Optional[int] = None,
                        gear: Optional[int] = None,
                        group: Optional[int] = None
                        ) -> bool:
        """Set a DALI target to a colour and/or level.
        
        Args:
            controller: ZenController instance
            level: DALI arc level (0-254, or None for no change)
            kelvin: colour temperature (1000-20000, or None for no change)
            address: DALI address (0-79)
            gear: DALI address (0-63)
            group: DALI address (0-15)
            
        Returns:
            bool: True if command succeeded, False otherwise
        """
        address = self.valid_dali_address(address=address, gear=gear, group=group)
        if kelvin is not None: 
            if not 1000 <= kelvin <= 20000:
                raise ValueError("Kelvin must be between 1000 and 20000")
            kelvin_hi = (kelvin >> 8) & 0xFF
            kelvin_lo = kelvin & 0xFF
            if level is not None and not 0 <= level <= 254:
                raise ValueError("Arc level must be between 0 and 254")
            return self.send_colour(controller, self.CMD["DALI_COLOUR"], address, level if level is not None else 255, 0x20, [kelvin_hi, kelvin_lo])
        elif level is not None:
            self.dali_arc_level(controller, level, address=address)
            if not 0 <= level <= 254:
                raise ValueError("Arc level must be between 0 and 254")
            return self.send_basic(controller, self.CMD["DALI_ARC_LEVEL"], address, [0x00, 0x00, level], return_type='ok')
        else:
            raise ValueError("Either kelvin or arc_level must be provided")
    