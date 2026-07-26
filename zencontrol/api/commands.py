import asyncio
import logging
import struct
import time
from datetime import datetime as dt
from typing import Any, Literal, Self, overload

from ..exceptions import ZenTimeoutError
from ..io.command import (
    ClientConst,
    Request,
    RequestType,
    Response,
    ResponseType,
    ZenClient,
)
from .models import (
    ControllerRef,
    ZenAddress,
    ZenColour,
    ZenController,
    ZenInstance,
)
from .event_decode import ZenEventMask
from .types import (
    Const,
    Transport,
    ZenAddressType,
    ZenErrorCode,
    ZenEventMode,
    ZenInstanceType,
)

# Magic import to avoid dependency on colorama
try:
    from colorama import Fore, Style
except ImportError:
    class _NoColor:
        def __getattr__(self, _name: str) -> str:
            return ""
    Fore = Style = _NoColor()  # type: ignore[assignment]


"""
===================================================================================
Command plane: TPI Advanced API over zen_io (no event session).
===================================================================================
"""


class ZenCommandClient:

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
        "QUERY_COLOUR_SCENE_MEMBERSHIP_BY_ADDR": 0x44, # Query a list of scenes with colour change data for an address
        "QUERY_COLOUR_SCENE_0_7_DATA_FOR_ADDR": 0x45, # Query the colour control data for scenes 0-7
        "QUERY_COLOUR_SCENE_8_11_DATA_FOR_ADDR": 0x46, # Query the colour control data for scenes 8-11

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
                 logger: logging.Logger | None = None,
                 print_traffic: bool = False) -> None:
        self.logger = logger or logging.getLogger('null')
        if logger is None:
            self.logger.addHandler(logging.NullHandler())
        self.print_traffic = print_traffic
        # Command-plane UDP clients keyed by controller name (not on the model).
        self._clients: dict[str, ZenClient] = {}

    async def __aenter__(self) -> Self:
        """Async context manager entry"""
        return self
    
    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit"""
        await self.aclose()

    async def aclose(self) -> None:
        """Close UDP command clients."""
        await self.close_all_clients()

    def client_for(self, controller: ControllerRef) -> ZenClient | None:
        """Return the command-plane UDP client for ``controller``, if any."""
        return self._clients.get(controller.name)

    def set_client(self, controller: ControllerRef, client: ZenClient | None) -> None:
        """Install or clear a command client without touching the model."""
        if client is None:
            self._clients.pop(controller.name, None)
        else:
            self._clients[controller.name] = client

    async def close_all_clients(self) -> None:
        """Close every owned command client. Idempotent."""
        for name in list(self._clients):
            client = self._clients.pop(name, None)
            if client is None:
                continue
            try:
                await client.close()
            except Exception:
                pass

    # ============================
    # PACKET SENDING
    # ============================

    @overload
    async def _send_basic(
        self,
        controller: ControllerRef,
        command: int,
        address: int = 0x00,
        data: list[int] | None = None,
        *,
        return_type: Literal["str"]
    ) -> str | None: ...

    @overload
    async def _send_basic(
        self,
        controller: ControllerRef,
        command: int,
        address: int = 0x00,
        data: list[int] | None = None,
        *,
        return_type: Literal["bytes"] = "bytes"
    ) -> bytes | None: ...

    @overload
    async def _send_basic(
        self,
        controller: ControllerRef,
        command: int,
        address: int = 0x00,
        data: list[int] | None = None,
        *,
        return_type: Literal["list"]
    ) -> list[int] | None: ...

    @overload
    async def _send_basic(
        self,
        controller: ControllerRef,
        command: int,
        address: int = 0x00,
        data: list[int] | None = None,
        *,
        return_type: Literal["int"]
    ) -> int | None: ...

    @overload
    async def _send_basic(
        self,
        controller: ControllerRef,
        command: int,
        address: int = 0x00,
        data: list[int] | None = None,
        *,
        return_type: Literal["bool", "ok"]
    ) -> bool | None: ...

    async def _send_basic(self,
                   controller: ControllerRef,
                   command: int,
                   address: int = 0x00,
                   data: list[int] | None = None,
                   return_type: str = 'bytes',
                   ) -> (bytes | str | list[int] | int | bool) | None:
        request: Request = Request(command=command, data=[address] + (data or []), request_type=RequestType.BASIC)
        response_data, response_code = await self._send_packet(controller, request)
        match response_code:
            case 0xA0: # OK
                match return_type:
                    case 'ok' | 'bool':
                        # PDF documents OK for several commands (e.g. filter add/clear).
                        # Treating OK as True for bool keeps clients aligned with hardware.
                        return True
                    case _:
                        raise ValueError(f"Invalid return_type '{return_type}' for response code {response_code}")
            case 0xA1: # ANSWER
                match return_type:
                    case 'bytes':
                        return response_data
                    case 'str':
                        if response_data is None:
                            return None
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
                    case 'ok':
                        return True
                    case _:
                        raise ValueError(f"Invalid return_type '{return_type}' for response code {response_code}")
            case 0xA2: # NO_ANSWER
                match return_type:
                    case 'ok':
                        return False
                    case _:
                        return None
            case 0xA3: # ERROR
                self._log_response_error(response_data)
            case 0xAE: # TIMEOUT
                self.logger.error("Command timed out")
                return None
            case 0xAF: # INVALID
                self.logger.error("Invalid response from controller")
                return None
            case _:
                self.logger.error(f"Unknown response code: {response_code}")
        return None

    def _log_response_error(self, response_data: bytes | None) -> None:
        """Log a TPI ERROR payload without raising (treat like soft failure / None)."""
        code: int | None = response_data[0] if response_data else None
        error_code = (
            ZenErrorCode(code)
            if code is not None and code in ZenErrorCode._value2member_map_
            else None
        )
        label = (
            error_code.name
            if error_code
            else (f"Unknown error code: {hex(code)}" if code is not None else "no error code")
        )
        self.logger.error(f"Command error code: {label}")

    async def _send_colour(self, controller: ControllerRef, command: int, address: int, colour: ZenColour, level: int = 255) -> bool | None:
        """Send a DALI colour command."""
        data = [address, level & 0xFF] + list(colour.command_payload())
        request: Request = Request(command=command, data=data, request_type=RequestType.DALI_COLOUR)
        response_data, response_code = await self._send_packet(controller, request)
        match response_code:
            case 0xA0: # OK
                return True
            case 0xA2: # NO_ANSWER
                return False
        return None

    async def _send_dynamic(self, controller: ControllerRef, command: int, data: list[int]) -> bytes | None:
        # Calculate data length and prepend it to data
        request: Request = Request(command=command, data=data, request_type=RequestType.DYNAMIC)
        response_data, response_code = await self._send_packet(controller, request)
        # Check response type
        match response_code:
            case 0xA0: # OK
                pass  # Request processed successfully
            case 0xA1: # ANSWER
                pass  # Answer is in data bytes
            case 0xA2: # NO_ANSWER
                if response_data is not None and len(response_data) > 0:
                    self.logger.error("No answer with code: %r", response_data)
                return None
            case 0xA3: # ERROR
                self._log_response_error(response_data)
                return None
            case 0xAF: # INVALID
                self.logger.error("Invalid response from controller")
                return None
            case _:
                self.logger.error(f"Unknown response type: {response_code}")
                return None
        if response_data:
            return response_data
        return None

    async def _send_packet(self, controller: ControllerRef, request: Request) -> tuple[bytes | None, int]:
        # Ensure client is properly initialized and not closed
        await self._ensure_client(controller)
        client = self._clients.get(controller.name)
        assert client is not None

        response: Response = await client.send_request_with_retries(
            request,
            retries=ClientConst.DEFAULT_RETRIES,
        )

        # Timeout?
        # Work out how many msec we waited for
        wait_time_ms = (time.time() - request.timestamp) * 1000
        if response.response_type == ResponseType.TIMEOUT:
            raw_sent = request.raw_sent or (response.request.raw_sent if response.request else None)
            raw_sent_str = f"[{' '.join(f'0x{b:02X}' for b in raw_sent)}]" if raw_sent else "[]"
            self.logger.error(f"UDP packet response from {controller.host}:{controller.port} not received after {wait_time_ms:.0f}ms, probably offline {raw_sent_str}")
            await self._invalidate_client(controller)
            controller.refresh_ip()
            raise ZenTimeoutError(f"No response from {controller.host}:{controller.port} after {wait_time_ms:.0f}ms")

        # ERROR / INVALID are soft failures: return payload + code so callers can
        # treat them like NO_ANSWER (None). Raising here breaks discovery/interview
        # for paid-feature and similar controller responses.

        # print_traffic
        req = response.request
        if self.print_traffic and req is not None and req.raw_sent and response.raw_rcvd:
            rtt_ms = (response.timestamp - req.timestamp) * 1000
            cmd_name = next((name for name, code in self.CMD.items() if code == req.command), f"0x{req.command:02X}")
            print(Fore.MAGENTA + f"REQUEST: [{' '.join(f'0x{b:02X}' for b in req.raw_sent)}]"
                + Style.RESET_ALL + Fore.GREEN + f"  {cmd_name}"
                + Style.RESET_ALL + Fore.CYAN + f"  RESPONSE: [{' '.join(f'0x{b:02X}' for b in response.raw_rcvd)}]"
                + Style.RESET_ALL + Fore.WHITE + Style.DIM + f"  RTT: {rtt_ms:.0f}ms".ljust(10)
                + Style.RESET_ALL)

        return response.data, response.response_type.value

    async def _ensure_client(self, controller: ControllerRef) -> None:
        """Create or replace the UDP client when missing or disconnected."""
        client = self._clients.get(controller.name)
        if client is not None and client.is_connected():
            return
        if client is not None:
            await self._invalidate_client(controller)
        self._clients[controller.name] = await ZenClient.create(
            (controller.ip, controller.port),
            logger=self.logger,
        )

    async def _invalidate_client(self, controller: ControllerRef) -> None:
        """Close and drop a stale client so the next send recreates it."""
        client = self._clients.pop(controller.name, None)
        if client is None:
            return
        try:
            await client.close()
        except Exception:
            pass

    # ============================
    # API COMMANDS
    # ============================

    async def query_group_label(self, address: ZenAddress) -> str | None:
        """Get the label for a DALI Group. Returns a string, or None if no label is set."""
        return await self._send_basic(address.controller, self.CMD["QUERY_GROUP_LABEL"], address.group(), return_type='str')
    
    async def query_dali_device_label(self, address: ZenAddress) -> str | None:
        """Query the label for a DALI device (control gear or control device). Returns a string, or None if no label is set."""
        return await self._send_basic(address.controller, self.CMD["QUERY_DALI_DEVICE_LABEL"], address.ecg_or_ecd(), return_type='str')
        
    async def query_profile_label(self, controller: ControllerRef, profile: int) -> str | None:
        """Get the label for a Profile number (0-65535). Returns a string if a label exists, else None."""
        # Profile numbers are 2 bytes long, so check valid range
        if not 0 <= profile <= 65535:
            raise ValueError("Profile number must be between 0 and 65535")
        # Split profile number into upper and lower bytes
        profile_upper = (profile >> 8) & 0xFF
        profile_lower = profile & 0xFF
        # Send request
        return await self._send_basic(controller, self.CMD["QUERY_PROFILE_LABEL"], 0x00, [0x00, profile_upper, profile_lower], return_type='str')
    
    async def query_current_profile_number(self, controller: ControllerRef) -> int | None:
        """Get the current/active Profile number for a controller. Returns int, else None if query fails."""
        response = await self._send_basic(controller, self.CMD["QUERY_CURRENT_PROFILE_NUMBER"])
        if response and len(response) >= 2: # Profile number is 2 bytes, combine them into a single integer. First byte is high byte, second is low byte
            return (response[0] << 8) | response[1]
        return None

    async def query_tpi_event_emit_state(self, controller: ControllerRef) -> bool | None:
        """Get the current TPI Event multicast emitter state for a controller. Returns True if enabled, False if disabled, None if query fails."""
        response = await self._send_basic(controller, self.CMD["QUERY_TPI_EVENT_EMIT_STATE"])
        if not response:
            return None
        return ZenEventMode.from_byte(response[0]).enabled
    
    async def dali_add_tpi_event_filter(self, address: ZenAddress|ZenInstance, filter: ZenEventMask | None = None) -> bool | None:
        """Stop specific events from an address/instance from being sent. Events in mask will be muted. Returns true if filter was added successfully."""
        if filter is None: filter = ZenEventMask.all_events()
        instance_number = 0xFF
        if isinstance(address, ZenInstance):
            instance: ZenInstance = address
            instance_number = instance.number
            address = instance.address
        return await self._send_basic(address.controller,
                             self.CMD["DALI_ADD_TPI_EVENT_FILTER"],
                             address.ecg_or_ecd_or_broadcast(),
                             [instance_number, filter.upper(), filter.lower()],
                             return_type='bool')
    
    async def dali_clear_tpi_event_filter(self, address: ZenAddress|ZenInstance, unfilter: ZenEventMask | None = None) -> bool | None:
        """Allow specific events from an address/instance to be sent again. Events in mask will be unmuted. Returns true if filter was cleared successfully."""
        if unfilter is None: unfilter = ZenEventMask.all_events()
        instance_number = 0xFF
        if isinstance(address, ZenInstance):
            instance: ZenInstance = address
            instance_number = instance.number
            address = instance.address
        return await self._send_basic(address.controller,
                             self.CMD["DALI_CLEAR_TPI_EVENT_FILTERS"],
                             address.ecg_or_ecd_or_broadcast(),
                             [instance_number, unfilter.upper(), unfilter.lower()],
                             return_type='bool')

    async def query_dali_tpi_event_filters(self, address: ZenAddress|ZenInstance) -> list[dict[str, Any]]:
        """Query active event filters for an address (or a specific instance). Returns a list of dictionaries containing filter info, or None if query fails."""
        instance_number = 0xFF
        if isinstance(address, ZenInstance):
            instance: ZenInstance = address
            instance_number = instance.number
            address = instance.address
        
        # As the data payload can only be up to 64 bytes and there are up to 64 event filters, it may be necessary to query several times.
        # If you have all 64 event filters active, you will receive results 0-14 in the first response.
        results = []
        start_at = 0
        while True:
        
            response = await self._send_basic(address.controller, 
                                    self.CMD["QUERY_DALI_TPI_EVENT_FILTERS"],
                                    address.ecg_or_ecd_or_broadcast(),
                                    [start_at, 0x00, instance_number])
            
            # Byte 0: TPI event modes active, ignored here.
            # modes_active = response[0]
                                    
            if response and len(response) >= 5:  # Need at least modes + one result

                # Starting from the second byte (1), process results in groups of 4 bytes
                for i in range(1, len(response)-3, 4):
                    result = {
                        'address': response[i],
                        'instance': response[i+1],
                        'event_mask': ZenEventMask.from_upper_lower(response[i+2], response[i+3])
                    }
                    results.append(result)
                
                page_results = (len(response) - 1) // 4
                if page_results < 15: # fewer than 15 results in this page — no more pages
                    break
            
            else:
                break # If there are no more results, stop querying
            
            # To complete the set, you would request 15, 30, 45, 60 as starting numbers or until you receive None (NO_ANSWER).
            start_at += 15
                
        return results

    async def tpi_event_emit(self, controller: ControllerRef, mode: ZenEventMode | None = None) -> bool:
        """Enable or disable TPI Event emission. Returns True if successful, else False."""
        if mode is None: mode = ZenEventMode(enabled=True, filtering=False, unicast=False, multicast=True)
        mask = mode.bitmask()
        # response = await self._send_basic(controller, self.CMD["ENABLE_TPI_EVENT_EMIT"], 0x00) # disable first to clear any existing state... I think this is a bug?
        response = await self._send_basic(controller, self.CMD["ENABLE_TPI_EVENT_EMIT"], mask)
        if response:
            if response[0] == mask:
                return True
        return False

    async def set_tpi_event_unicast_address(self, controller: ControllerRef, ipaddr: str | None = None, port: int | None = None) -> bytes | None:
        """Configure TPI Events for Unicast mode with IP and port as defined in the ZenController instance."""
        data = [0,0,0,0,0,0]
        if port is not None:
            # Valid port number
            if not 0 <= port <= 65535: raise ValueError("Port must be between 0 and 65535")

            # Split port into upper and lower bytes
            data[0] = (port >> 8) & 0xFF
            data[1] = port & 0xFF
            
            # Convert IP string to bytes
            if ipaddr is None:
                raise ValueError("IP address required when port is set")
            try:
                ip_bytes = [int(x) for x in ipaddr.split('.')]
                if len(ip_bytes) != 4 or not all(0 <= x <= 255 for x in ip_bytes):
                    raise ValueError
                data[2:6] = ip_bytes
            except ValueError:
                raise ValueError("Invalid IP address format") from None
        
        return await self._send_dynamic(controller, self.CMD["SET_TPI_EVENT_UNICAST_ADDRESS"], data)

    async def query_tpi_event_unicast_address(self, controller: ControllerRef) -> dict[str, Any] | None:
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
        response = await self._send_basic(controller, self.CMD["QUERY_TPI_EVENT_UNICAST_ADDRESS"])
        if response and len(response) >= 7:
            return {
                'mode': ZenEventMode.from_byte(response[0]),
                'port': (response[1] << 8) | response[2],
                'ip': f"{response[3]}.{response[4]}.{response[5]}.{response[6]}"
            }
        return None

    async def query_group_numbers(self, controller: ControllerRef) -> list[ZenAddress]:
        """Query a controller for groups."""
        groups = await self._send_basic(controller, self.CMD["QUERY_GROUP_NUMBERS"], return_type='list')
        zen_groups: list[ZenAddress] = []
        if groups is not None:
            groups.sort()
            for group in groups:
                zen_groups.append(
                    ZenAddress(controller=controller, type=ZenAddressType.GROUP, number=group)
                )
        return zen_groups

    async def query_dali_colour(self, address: ZenAddress) -> ZenColour | None:
        """Query colour information from a DALI address."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR"], address.ecg())
        if response is None:
            return None
        return ZenColour.from_bytes(response)
    
    async def query_profile_information(self, controller: ControllerRef) -> (tuple[dict[str, Any], dict[int, dict[str, bool | int | str]]]) | None:
        """Query a controller for profile information. Returns a tuple of two dicts, or None if query fails."""
        response = await self._send_basic(controller, self.CMD["QUERY_PROFILE_INFORMATION"])
        if not response or len(response) < 12:
            return None
        # Initial 12 bytes:
        # 0-1 0x00 Current Active Profile Number
        # 2-3 0x00 Last Scheduled Profile Number
        # 4-7 0x22334455 Last Overridden Profile UTC
        # 8-11 0x44556677 Last Scheduled Profile UTC
        unpacked = struct.unpack('>HHII', response[0:12])
        state: dict[str, Any] = {
            'current_active_profile': unpacked[0],
            'last_scheduled_profile': unpacked[1],
            'last_overridden_profile_utc': dt.fromtimestamp(unpacked[2]),
            'last_scheduled_profile_utc': dt.fromtimestamp(unpacked[3])
        }
        # Process profiles in groups of 3 bytes (2 bytes for profile number, 1 byte for profile behaviour)
        profiles: dict[int, dict[str, bool | int | str]] = {}
        for i in range(12, len(response), 3):
            profile_number = struct.unpack('>H', response[i:i+2])[0]
            profile_behaviour = response[i+2]
            # bit 0: disabled flag — 1 = disabled, 0 = enabled
            # bit 1-2: priority — 0 = scheduled, 1 = medium, 2 = high, 3 = emergency
            enabled = not bool(profile_behaviour & 0x01)
            priority = (profile_behaviour >> 1) & 0x03
            priority_label = ["Scheduled", "Medium", "High", "Emergency"][priority]
            profiles[profile_number] = {"enabled": enabled, "priority": priority, "priority_label": priority_label}
        # Return tuple of state and profiles
        return state, profiles
    
    async def query_profile_numbers(self, controller: ControllerRef) -> list[int] | None:
        """Query a controller for a list of available Profile Numbers. Returns a list of profile numbers, or None if query fails."""
        response = await self._send_basic(controller, self.CMD["QUERY_PROFILE_NUMBERS"])
        if response and len(response) >= 2:
            # Response contains pairs of bytes for each profile number
            profile_numbers = []
            for i in range(0, len(response), 2):
                if i + 1 < len(response):
                    profile_num = (response[i] << 8) | response[i+1]
                    profile_numbers.append(profile_num)
            return profile_numbers
        return None

    async def query_occupancy_instance_timers(self, instance: ZenInstance) -> dict[str, Any] | None:
        """Query timer values for a DALI occupancy sensor instance. Returns dict, or None if query fails.

        Returns:
            dict:
                - int: Deadtime in seconds (0-255)
                - int: Hold time in seconds (0-255)
                - int: Report time in seconds (0-255)
                - int: Seconds since last occupied status (0-65535)
        """
        response = await self._send_basic(instance.address.controller, self.CMD["QUERY_OCCUPANCY_INSTANCE_TIMERS"], instance.address.ecd(), [0x00, 0x00, instance.number])
        if response and len(response) >= 5:
            return {
                'deadtime': response[0],
                'hold': response[1],
                'report': response[2],
                'last_detect': (response[3] << 8) | response[4]
            }
        return None

    async def query_instances_by_address(self, address: ZenAddress) -> list[ZenInstance]:
        """Query a DALI address (ECD) for associated instances. Returns a list of ZenInstance, or an empty list if nothing found."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_INSTANCES_BY_ADDRESS"], address.ecd())
        if response and len(response) >= 4:
            instances: list[ZenInstance] = []
            # Process groups of 4 bytes for each instance
            for i in range(0, len(response), 4):
                if i + 3 < len(response):
                    instance_type = ZenInstanceType(response[i+1]) if response[i+1] in ZenInstanceType._value2member_map_ else None
                    if instance_type is None:
                        continue
                    instances.append(ZenInstance(
                        address=address,
                        number=response[i], # first byte
                        type=instance_type, # second byte
                        active=bool(response[i+2] & 0x02), # third byte, second bit
                        error=bool(response[i+2] & 0x01), # third byte, first bit
                    ))
            return instances
        return []

    async def query_operating_mode_by_address(self, address: ZenAddress) -> int | None:
        """Query a DALI address (ECG or ECD) for its operating mode. Returns an int containing the operating mode value, or None if the query fails."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_OPERATING_MODE_BY_ADDRESS"], address.ecg_or_ecd())
        if response and len(response) == 1:
            return response[0]  # Operating mode is in first byte
        return None

    async def dali_colour(self, address: ZenAddress, colour: ZenColour, level: int = 255) -> bool | None:
        """Set a DALI address (ECG, group, broadcast) to a colour. Returns True if command succeeded, False otherwise."""
        return await self._send_colour(address.controller, self.CMD["DALI_COLOUR"], address.ecg_or_group_or_broadcast(), colour, level)

    async def query_group_by_number(self, address: ZenAddress) -> tuple[int, bool, int] | None: # TODO: change to a dict or special class?
        """Query a DALI group for its occupancy status and level. Returns a tuple containing group number, occupancy status, and actual level."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_GROUP_BY_NUMBER"], address.group())
        if response and len(response) == 3:
            group_num = response[0]
            occupancy = bool(response[1])
            level = response[2]
            return (group_num, occupancy, level)
        return None

    async def query_scene_numbers_by_address(self, address: ZenAddress) -> list[int] | None:
        """Query a DALI address (ECG) for associated scenes. Returns a list of scene numbers where levels have been set."""
        return await self._send_basic(address.controller, self.CMD["QUERY_SCENE_NUMBERS_BY_ADDRESS"], address.ecg(), return_type='list')

    async def query_scene_levels_by_address(self, address: ZenAddress) -> list[int | None]:
        """Query scene levels for a DALI address (ECG).

        Zencontrol has 12 scenes (0–11). This TPI command still returns 16 bytes
        because it dumps the full DALI gear scene table (slots 0–15); values of
        255 mean “not in that scene.” Treat only indices 0–11 as Zencontrol
        scenes — slots 12–15 are unused padding, not extra product scenes.
        """
        response = await self._send_basic(address.controller, self.CMD["QUERY_SCENE_LEVELS_BY_ADDRESS"], address.ecg(), return_type='list')
        if response:
            return [None if x == 255 else x for x in response]
        return [None] * Const.MAX_SCENE
    
    async def query_colour_scene_membership_by_address(self, address: ZenAddress) -> list[int]:
        """Query a DALI address (ECG) for which scenes have colour change data. Returns a list of scene numbers."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_COLOUR_SCENE_MEMBERSHIP_BY_ADDR"], address.ecg(), return_type='list')
        if response:
            return response
        return []

    async def query_scene_colours_by_address(self, address: ZenAddress) -> list[ZenColour | None]:
        """Query colour scene data for a DALI address (ECG).

        Returns a list of length Const.MAX_SCENE (12): scenes 0–11 only.
        Colour TPI opcodes cover 0–7 and 8–11; there is no colour data for
        DALI gear slots 12–15.
        """
        # Create a list of 12 ZenColour instances
        output: list[ZenColour | None] = [None] * Const.MAX_SCENE
        # Queries
        response = await self._send_basic(address.controller, self.CMD["QUERY_COLOUR_SCENE_0_7_DATA_FOR_ADDR"], address.ecg())
        if response is None:
            return output
        response2 = await self._send_basic(address.controller, self.CMD["QUERY_COLOUR_SCENE_8_11_DATA_FOR_ADDR"], address.ecg())
        if response2 is None:
            return output
        response += response2
        # Combined result should always be exactly 7*12 = 84 bytes
        if len(response) != 84:
            print(f"Warning: QUERY_COLOUR_SCENE_***_DATA_FOR_ADDR returned {len(response)} bytes, expected 84")
            return output
        # Data is in 7 byte segments
        for i in range(0, Const.MAX_SCENE):
            offset = i*7
            output[i] = ZenColour.from_bytes(response[offset:offset+7])
        return output
            
    async def query_group_membership_by_address(self, address: ZenAddress) -> list[ZenAddress]:
        """Query an address (ECG) for which DALI groups it belongs to. Returns a list of ZenAddress group instances."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_GROUP_MEMBERSHIP_BY_ADDRESS"], address.ecg())
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
            zen_groups: list[ZenAddress] = []
            for number in groups:
                zen_groups.append(ZenAddress(
                    controller=address.controller,
                    type=ZenAddressType.GROUP,
                    number=number
                ))
            return zen_groups
        return []

    async def query_dali_addresses_with_instances(self, controller: ControllerRef, start_address: int = 0) -> list[ZenAddress]:  # TODO: automate iteration over start_address=0, start_address=60, etc.
        """Query for DALI addresses that have instances associated with them.
        
        Due to payload restrictions, this needs to be called multiple times with different
        start addresses to check all possible devices (e.g. start_address=0, then start_address=60)
        
        Args:
            controller: ZenController instance
            start_address: Starting DALI address to begin searching from (0-127)
            
        Returns:
            List of DALI addresses that have instances, or None if query fails
        """
        addresses = await self._send_basic(controller, self.CMD["QUERY_DALI_ADDRESSES_WITH_INSTANCES"], 0, [0,0,start_address], return_type='list')
        if not addresses:
            return []
        zen_addresses: list[ZenAddress] = []
        for number in addresses:
            if 64 <= number <= 127:  # Only process valid device addresses (64-127)
                zen_addresses.append(ZenAddress(
                    controller=controller,
                    type=ZenAddressType.ECD,
                    number=number-64 # subtract 64 to get actual DALI device address
                ))
        return zen_addresses
    
    async def query_scene_numbers_for_group(self, address: ZenAddress) -> list[int]:
        """Query which DALI scenes are associated with a given group number. Returns list of scene numbers."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_SCENE_NUMBERS_FOR_GROUP"], address.group())
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
    
    async def query_scene_label_for_group(self, address: ZenAddress, scene: int) -> str | None:
        """Query the label for a scene (0-11) and group number combination. Returns string, or None if no label is set."""
        if not 0 <= scene < Const.MAX_SCENE: raise ValueError("Scene must be between 0 and 11")
        return await self._send_basic(address.controller, self.CMD["QUERY_SCENE_LABEL_FOR_GROUP"], address.group(), [scene], return_type='str')
    
    async def query_scenes_for_group(self, address: ZenAddress) -> list[str | None]:
        """Compound command to query the labels for all scenes for a group. Returns list of scene labels, where None indicates no label is set."""
        scenes: list[str | None] = [None] * Const.MAX_SCENE
        numbers = await self.query_scene_numbers_for_group(address)
        if numbers:
            for scene in numbers:
                scenes[scene] = await self.query_scene_label_for_group(address, scene)
        return scenes
    
    async def query_controller_version_number(self, controller: ControllerRef) -> str | None:
        """Query the controller's version number. Returns string, or None if query fail s."""
        response = await self._send_basic(controller, self.CMD["QUERY_CONTROLLER_VERSION_NUMBER"])
        if response and len(response) == 3:
            return f"{response[0]}.{response[1]}.{response[2]}"
        return None
    
    async def query_control_gear_dali_addresses(self, controller: ControllerRef) -> list[ZenAddress]:
        """Query which DALI control gear addresses are present in the database. Returns a list of ZenAddress instances."""
        response = await self._send_basic(controller, self.CMD["QUERY_CONTROL_GEAR_DALI_ADDRESSES"])
        if response and len(response) == 8:  # 8 data bytes representing addresses 0-63
            addresses: list[ZenAddress] = []
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
    
    async def dali_inhibit(self, address: ZenAddress, time_seconds: int) -> bool | None:
        """Inhibit sensors from changing a DALI address (ECG or group or broadcast) for specified time in seconds (0-65535). Returns True if acknowledged, else False."""
        time_hi = (time_seconds >> 8) & 0xFF  # Convert time to 16-bit value
        time_lo = time_seconds & 0xFF
        return await self._send_basic(address.controller, self.CMD["DALI_INHIBIT"], address.ecg_or_group_or_broadcast(), [0x00, time_hi, time_lo], return_type='ok')
    
    async def dali_scene(self, address: ZenAddress, scene: int) -> bool | None:
        """Send RECALL SCENE (0-11) to an address (ECG or group or broadcast). Returns True if acknowledged, else False."""
        if not 0 <= scene < Const.MAX_SCENE: raise ValueError(f"Scene number must be between 0 and {Const.MAX_SCENE}, got {scene}")
        return await self._send_basic(address.controller, self.CMD["DALI_SCENE"], address.ecg_or_group_or_broadcast(), [0x00, 0x00, scene], return_type='ok')
    
    async def dali_arc_level(self, address: ZenAddress, level: int) -> bool | None:
        """Send DIRECT ARC level (0-254) to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        if not 0 <= level <= Const.MAX_LEVEL: raise ValueError(f"Level must be between 0 and {Const.MAX_LEVEL}, got {level}")
        return await self._send_basic(address.controller, self.CMD["DALI_ARC_LEVEL"], address.ecg_or_group_or_broadcast(), [0x00, 0x00, level], return_type='ok')
    
    async def dali_on_step_up(self, address: ZenAddress) -> bool | None:
        """Send ON AND STEP UP to an address (ECG or group or broadcast). If a device is off, it will turn it on. If a device is on, it will step up. No fade."""
        return await self._send_basic(address.controller, self.CMD["DALI_ON_STEP_UP"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    async def dali_step_down_off(self, address: ZenAddress) -> bool | None:
        """Send STEP DOWN AND OFF to an address (ECG or group or broadcast). If a device is at min, it will turn off. If a device isn't yet at min, it will step down. No fade."""
        return await self._send_basic(address.controller, self.CMD["DALI_STEP_DOWN_OFF"], address.ecg_or_group_or_broadcast(), return_type='ok')

    async def dali_up(self, address: ZenAddress) -> bool | None:
        """Send DALI UP to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        return await self._send_basic(address.controller, self.CMD["DALI_UP"], address.ecg_or_group_or_broadcast(), return_type='ok')

    async def dali_down(self, address: ZenAddress) -> bool | None:
        """Send DALI DOWN to an address (ECG or group or broadcast). Will fade to the new level. Returns True if acknowledged, else False."""
        return await self._send_basic(address.controller, self.CMD["DALI_DOWN"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    async def dali_recall_max(self, address: ZenAddress) -> bool | None:
        """Send RECALL MAX to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return await self._send_basic(address.controller, self.CMD["DALI_RECALL_MAX"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    async def dali_recall_min(self, address: ZenAddress) -> bool | None:
        """Send RECALL MIN to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return await self._send_basic(address.controller, self.CMD["DALI_RECALL_MIN"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    async def dali_off(self, address: ZenAddress) -> bool | None:
        """Send OFF to an address (ECG or group or broadcast). No fade. Returns True if acknowledged, else False."""
        return await self._send_basic(address.controller, self.CMD["DALI_OFF"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    async def dali_query_level(self, address: ZenAddress) -> int | None:
        """Query the Arc Level for a DALI address (ECG or group). Returns arc level as int, or None if mixed levels."""
        response = await self._send_basic(address.controller, self.CMD["DALI_QUERY_LEVEL"], address.ecg_or_group(), return_type='int')
        if response == 255: return None # 255 indicates mixed levels
        return response
    
    async def dali_query_control_gear_status(self, address: ZenAddress) -> dict[str, Any] | None:
        """Query the Status for a DALI address (ECG or group or broadcast). Returns a dictionary of status flags."""
        response = await self._send_basic(address.controller, self.CMD["DALI_QUERY_CONTROL_GEAR_STATUS"], address.ecg_or_group_or_broadcast())
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
    
    async def dali_query_cg_type(self, address: ZenAddress) -> list[int] | None:
        """Query device type information for a DALI address (ECG).
            
        Returns:
            list[int] | None: List of device type numbers that the control gear belongs to.
                                Returns empty list if device doesn't exist.
                                Returns None if query fails.
        """
        response = await self._send_basic(address.controller, self.CMD["DALI_QUERY_CG_TYPE"], address.ecg())
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
    
    async def dali_query_last_scene(self, address: ZenAddress) -> int | None:
        """Query the last heard Scene for a DALI address (ECG or group or broadcast). Returns scene number, or None if query fails.
            
        Note:
            Changes to a single DALI device done through group or broadcast scene commands
            also change the last heard scene for the individual device address. For example,
            if A10 is member of G0 and we send a scene command to G0, A10 will show the 
            same last heard scene as G0.
        """
        return await self._send_basic(address.controller, self.CMD["DALI_QUERY_LAST_SCENE"], address.ecg_or_group_or_broadcast(), return_type='int')
    
    async def dali_query_last_scene_is_current(self, address: ZenAddress) -> bool | None:
        """Query if the last heard scene is the current active scene for a DALI address (ECG or group or broadcast).
        Returns True if still active, False if another command has been issued since, or None if query fails."""
        return await self._send_basic(address.controller, self.CMD["DALI_QUERY_LAST_SCENE_IS_CURRENT"], address.ecg_or_group_or_broadcast(), return_type='bool')
    
    async def dali_query_min_level(self, address: ZenAddress) -> int | None:
        """Query a DALI address (ECG) for its minimum level (0-254). Returns the minimum level if successful, None if query fails."""
        return await self._send_basic(address.controller, self.CMD["DALI_QUERY_MIN_LEVEL"], address.ecg(), return_type='int')

    async def dali_query_max_level(self, address: ZenAddress) -> int | None:
        """Query a DALI address (ECG) for its maximum level (0-254). Returns the maximum level if successful, None if query fails."""
        return await self._send_basic(address.controller, self.CMD["DALI_QUERY_MAX_LEVEL"], address.ecg(), return_type='int')
    
    async def dali_query_fade_running(self, address: ZenAddress) -> bool | None:
        """Query a DALI address (ECG) if a fade is currently running. Returns True if a fade is currently running, False if not, None if query fails."""
        return await self._send_basic(address.controller, self.CMD["DALI_QUERY_FADE_RUNNING"], address.ecg(), return_type='bool')
    
    async def dali_enable_dapc_sequence(self, address: ZenAddress) -> bool | None:
        """Begin a DALI Direct Arc Power Control (DAPC) Sequence.
        
        DAPC allows overriding of the fade rate for immediate level setting. The sequence
        continues for 250ms. If no arc levels are received within 250ms, the sequence ends
        and normal fade rates resume.
        
        Args:
            address: ZenAddress instance (ECG address)
            
        Returns:
            bool | None: True if successful, False if failed, None if no response
        """
        return await self._send_basic(address.controller, self.CMD["DALI_ENABLE_DAPC_SEQ"], address.ecg(), return_type='bool')
    
    async def query_dali_ean(self, address: ZenAddress) -> int | None:
        """Query a DALI address (ECG or ECD) for its European Article Number (EAN/GTIN). Returns an integer if successful, None if query fails."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_DALI_EAN"], address.ecg_or_ecd())
        if response and len(response) == 6:
            ean = 0
            for byte in response:
                ean = (ean << 8) | byte
            return ean
        return None
    
    async def query_dali_serial(self, address: ZenAddress) -> int | None:
        """Query a DALI address (ECG or ECD) for its Serial Number. Returns an integer if successful, None if query fails."""
        response = await self._send_basic(address.controller, self.CMD["QUERY_DALI_SERIAL"], address.ecg_or_ecd())
        if response and len(response) == 8:
            # Convert 8 bytes to decimal integer
            serial = 0
            for byte in response:
                serial = (serial << 8) | byte
            return serial
        return None
    
    async def dali_custom_fade(self, address: ZenAddress, level: int, seconds: int) -> bool | None:
        """Fade a DALI address (ECG or group) to a level (0-254) with a custom fade time in seconds (0-65535). Returns True if successful, else False."""
        if not 0 <= level <= Const.MAX_LEVEL:
            raise ValueError(f"Target level must be between 0 and {Const.MAX_LEVEL}, got {level}")
        if not 0 <= seconds <= 65535:
            raise ValueError("Fade time must be between 0 and 65535 seconds")

        # Convert fade time to integer seconds and split into high/low bytes
        seconds_hi = (seconds >> 8) & 0xFF
        seconds_lo = seconds & 0xFF
        
        return await self._send_basic(
            address.controller,
            self.CMD["DALI_CUSTOM_FADE"],
            address.ecg_or_group(),
            [level, seconds_hi, seconds_lo],
            return_type='ok'
        )
    
    async def dali_go_to_last_active_level(self, address: ZenAddress) -> bool | None:
        """Command a DALI Address (ECG or group) to go to its "Last Active" level. Returns True if successful, else False."""
        return await self._send_basic(address.controller, self.CMD["DALI_GO_TO_LAST_ACTIVE_LEVEL"], address.ecg_or_group(), return_type='ok')
    
    async def query_dali_instance_label(self, instance: ZenInstance) -> str | None:
        """Query the label for a DALI Instance. Returns a string, or None if not set."""
        return await self._send_basic(instance.address.controller, self.CMD["QUERY_DALI_INSTANCE_LABEL"], instance.address.ecd(), [0x00, 0x00, instance.number], return_type='str')

    async def change_profile_number(self, controller: ControllerRef, profile: int) -> bool | None:
        """Change the active profile number (0-65535). Returns True if successful, else False."""
        if not 0 <= profile <= 0xFFFF: raise ValueError("Profile number must be between 0 and 65535")
        profile_hi = (profile >> 8) & 0xFF
        profile_lo = profile & 0xFF
        return await self._send_basic(controller, self.CMD["CHANGE_PROFILE_NUMBER"], 0x00, [0x00, profile_hi, profile_lo], return_type='ok')
    
    async def return_to_scheduled_profile(self, controller: ControllerRef) -> bool | None:
        """Return to the scheduled profile. Returns True if successful, else False."""
        return await self.change_profile_number(controller, 0xFFFF) # See docs page 91, 0xFFFF returns to scheduled profile

    async def query_instance_groups(self, instance: ZenInstance) -> tuple[int | None, int | None, int | None] | None: # TODO: replace Tuple with dict
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
        response = await self._send_basic(
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
    
    async def query_dali_fitting_number(self, address: ZenAddress) -> str | None:
        """Query a DALI address (ECG or ECD) for its fitting number. Returns the fitting number (e.g. '1.2') or a generic identifier if the address doesn't exist, or None if the query fails."""
        return await self._send_basic(address.controller, self.CMD["QUERY_DALI_FITTING_NUMBER"], address.ecg_or_ecd(), return_type='str')
        
    async def query_dali_instance_fitting_number(self, instance: ZenInstance) -> str | None:
        """Query a DALI instance for its fitting number. Returns a string (e.g. '1.2.0') or None if query fails."""
        return await self._send_basic(instance.address.controller, self.CMD["QUERY_DALI_INSTANCE_FITTING_NUMBER"], instance.address.ecd(), [0x00, 0x00, instance.number], return_type='str')
    
    async def query_controller_label(self, controller: ControllerRef) -> str | None:
        """Request the label for the controller. Returns the controller's label string, or None if query fails."""
        return await self._send_basic(controller, self.CMD["QUERY_CONTROLLER_LABEL"], return_type='str')
    
    async def query_controller_fitting_number(self, controller: ControllerRef) -> str | None:
        """Request the fitting number string for the controller itself. Returns the controller's fitting number (e.g. '1'), or None if query fails."""
        return await self._send_basic(controller, self.CMD["QUERY_CONTROLLER_FITTING_NUMBER"], return_type='str')

    async def query_is_dali_ready(self, controller: ControllerRef) -> bool | None:
        """Query whether the DALI line is ready or has a fault. Returns True if DALI line is ready, False if there is a fault."""
        return await self._send_basic(controller, self.CMD["QUERY_IS_DALI_READY"], return_type='ok')
    
    async def query_controller_startup_complete(self, controller: ControllerRef) -> bool | None:
        """Query whether the controller has finished its startup sequence. Returns True if startup is complete, False if still in progress, None if the query fails.

        The startup sequence performs DALI queries such as device type, current arc-level, GTIN, 
        serial number, etc. The more devices on a DALI line, the longer startup will take to complete.
        For a line with only a handful of devices, expect it to take approximately 1 minute.
        Waiting for the startup sequence to complete is particularly important if you wish to 
        perform queries about DALI.
        """
        return await self._send_basic(controller, self.CMD["QUERY_CONTROLLER_STARTUP_COMPLETE"], return_type='ok')
    
    async def override_dali_button_led_state(self, instance: ZenInstance, led_state: bool) -> bool | None:
        """Override the LED state for a DALI push button. State is True for LED on, False for LED off. Returns true if command succeeded, else False."""
        return await self._send_basic(instance.address.controller,
                               self.CMD["OVERRIDE_DALI_BUTTON_LED_STATE"],
                               instance.address.ecd(),
                               [0x00, 0x02 if led_state else 0x01, instance.number],
                               return_type='ok')
    
    async def query_last_known_dali_button_led_state(self, instance: ZenInstance) -> bool | None:
        """Query the last known LED state for a DALI push button. Returns True if LED is on, False if LED is off, None if query failed
            
        Note: The "last known" LED state may not be the actual physical LED state.
        This only works for LED modes where the controller or TPI caller is managing
        the LED state. In many cases, the control device itself manages its own LED.
        """
        response = await self._send_basic(instance.address.controller,
                                   self.CMD["QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE"],
                                   instance.address.ecd(),
                                   [0x00, 0x00, instance.number])
        if response and len(response) == 1:
            match response[0]:
                case 0x01: return False
                case 0x02: return True
        return None

    async def dali_stop_fade(self, address: ZenAddress) -> bool | None:
        """Tell a DALI address (ECG, ECD, broadcast) to stop running a fade. Returns True if command succeeded, else False.

        Caution: this literally stops the fade. It doesn't jump to the target level.

        Note: For custom fades started via DALI_CUSTOM_FADE, this can only stop
        fades that were started with the same target address. For example, you 
        cannot stop a custom fade on a single address if it was started as part
        of a group or broadcast fade.
        """
        return await self._send_basic(address.controller, self.CMD["DALI_STOP_FADE"], address.ecg_or_group_or_broadcast(), return_type='ok')
    
    async def query_dali_colour_features(self, address: ZenAddress) -> dict[str, Any] | None:
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
        response = await self._send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR_FEATURES"], address.ecg())
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
    
    async def query_dali_colour_temp_limits(self, address: ZenAddress) -> dict[str, Any] | None:
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
        response = await self._send_basic(address.controller, self.CMD["QUERY_DALI_COLOUR_TEMP_LIMITS"], address.ecg())
        if response and len(response) == 10:
            return {
                'physical_warmest': (response[0] << 8) | response[1],
                'physical_coolest': (response[2] << 8) | response[3],
                'soft_warmest': (response[4] << 8) | response[5],
                'soft_coolest': (response[6] << 8) | response[7],
                'step_value': (response[8] << 8) | response[9]
            }
        return None
    
    async def set_system_variable(self, controller: ControllerRef, variable: int, value: int) -> bool | None:
        """Set a system variable (0-147) value (-32768-32767) on the controller. Returns True if successful, else False."""
        if not 0 <= variable < Const.MAX_SYSVAR:
            raise ValueError(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {variable}")
        if not -32768 <= value <= 32767:
            raise ValueError(f"Value must be between -32768 and 32767, received {value}")
        bytes = value.to_bytes(length=2, byteorder="big", signed=True)
        return await self._send_basic(controller, self.CMD["SET_SYSTEM_VARIABLE"], variable, [0x00, bytes[0], bytes[1]], return_type='ok')

        # If abs(value) is less than 32760, 
        #   If value has 2 decimal places, use magitude -2 (signed 0xfe)
        #   Else if value has 1 decimal place, use magitude -1 (signed 0xff)
        #   Else use magitude 0 (signed 0x00)
        # Else if abs(value) is less than 327600, use magitude 1 (signed 0x01)
        # Else if abs(value) is less than 3276000, use magitude 2 (signed 0x02)
    
    async def query_system_variable(self, controller: ControllerRef, variable: int) -> int | None:
        """Query the controller for the value of a system variable (0-147). Returns the variable's value (-32768-32767) if successful, else None."""
        if not 0 <= variable < Const.MAX_SYSVAR:
            raise ValueError(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {variable}")
        response = await self._send_basic(controller, self.CMD["QUERY_SYSTEM_VARIABLE"], variable)
        if response and len(response) == 2:
            return int.from_bytes(response, byteorder="big", signed=True)
        else: # Value is unset
            return None
    
    async def query_system_variable_name(self, controller: ControllerRef, variable: int) -> str | None:
        """Query the name of a system variable (0-147). Returns the variable's name, or None if query fails."""
        if not 0 <= variable < Const.MAX_SYSVAR:
            raise ValueError(f"Variable number must be between 0 and {Const.MAX_SYSVAR}, received {variable}")
        return await self._send_basic(controller, self.CMD["QUERY_SYSTEM_VARIABLE_NAME"], variable, return_type='str')
