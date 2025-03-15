import time
import logging
from zen import ZenProtocol, ZenController as SuperZenController, ZenAddress, ZenInstance, ZenAddressType, ZenColour, ZenColourType, ZenInstanceType, ZenTimeoutError, ZenEventMask
from typing import Optional, Callable
from threading import Timer
class Const:

    MAX_SYSVAR = 148 # 0-147
    MAX_SCENE = 12 # 0-11
    
    DEFAULT_WARMEST_TEMP = 2700
    DEFAULT_COOLEST_TEMP = 6500
    
    RGB_CHANNELS = 3
    RGBW_CHANNELS = 4
    RGBWW_CHANNELS = 5

    LONG_PRESS_COUNT = 2

    DEFAULT_HOLD_TIME = 60

class ZenController:
    pass

class ZenProfile:
    pass

class ZenLight:
    pass

class ZenGroup:
    pass

class ZenButton:
    pass

class ZenMotionSensor:
    pass

class ZenSystemVariable:
    pass


CallbackOnConnect = Callable[[], None]
CallbackOnDisconnect = Callable[[], None]
CallbackProfileChange = Callable[[ZenProfile], None]
CallbackGroupChange = Callable[[ZenGroup, int], None]
CallbackLightChange = Callable[[ZenLight, int, ZenColour, int], None]
CallbackButtonPress = Callable[[ZenButton], None]
CallbackButtonLongPress = Callable[[ZenButton], None]
CallbackMotionEvent = Callable[[ZenMotionSensor, bool], None]
CallbackSystemVariableChange = Callable[[ZenSystemVariable, int, bool, bool], None]

class _callbacks:
    on_connect: Optional[CallbackOnConnect] = None
    on_disconnect: Optional[CallbackOnDisconnect] = None
    profile_change: Optional[CallbackProfileChange] = None
    group_change: Optional[CallbackGroupChange] = None
    light_change: Optional[CallbackLightChange] = None
    button_press: Optional[CallbackButtonPress] = None
    button_long_press: Optional[CallbackButtonLongPress] = None
    motion_event: Optional[CallbackMotionEvent] = None
    system_variable_change: Optional[CallbackSystemVariableChange] = None


class ZenInterface:
    def __init__(self,
                 logger: logging.Logger=None,
                 narration: bool = False,
                 unicast: bool = False,
                 listen_ip: Optional[str] = None,
                 listen_port: Optional[int] = None,
                 cache: dict = {}
                 ):
        self.protocol: ZenProtocol = ZenProtocol(logger=logger, narration=narration, unicast=unicast, listen_ip=listen_ip, listen_port=listen_port, cache=cache)
        self.controllers: list[ZenController] = []

    @property
    def cache(self) -> dict:
        return self.protocol.cache

    @property
    def on_connect(self) -> CallbackOnConnect | None:
        return _callbacks.on_connect
    @on_connect.setter
    def on_connect(self, func: CallbackOnConnect | None) -> None:
        _callbacks.on_connect = func

    @property
    def on_disconnect(self) -> CallbackOnDisconnect | None:
        return _callbacks.on_disconnect
    @on_disconnect.setter
    def on_disconnect(self, func: CallbackOnDisconnect | None) -> None:
        _callbacks.on_disconnect = func

    @property
    def profile_change(self) -> CallbackProfileChange | None:
        return _callbacks.profile_change
    @profile_change.setter
    def profile_change(self, func: CallbackProfileChange | None) -> None:
        _callbacks.profile_change = func

    @property
    def group_change(self) -> CallbackGroupChange | None:
        return _callbacks.group_change
    @group_change.setter
    def group_change(self, func: CallbackGroupChange | None) -> None:
        _callbacks.group_change = func

    @property
    def light_change(self) -> CallbackLightChange | None:
        return _callbacks.light_change
    @light_change.setter
    def light_change(self, func: CallbackLightChange | None) -> None:
        _callbacks.light_change = func

    @property
    def button_press(self) -> CallbackButtonPress | None:
        return _callbacks.button_press
    @button_press.setter
    def button_press(self, func: CallbackButtonPress | None) -> None:
        _callbacks.button_press = func
    
    @property
    def button_long_press(self) -> CallbackButtonLongPress | None:
        return _callbacks.button_long_press
    @button_long_press.setter
    def button_long_press(self, func: CallbackButtonLongPress | None) -> None:
        _callbacks.button_long_press = func
    
    @property
    def motion_event(self) -> CallbackMotionEvent | None:
        return _callbacks.motion_event
    @motion_event.setter
    def motion_event(self, func: CallbackMotionEvent | None) -> None:
        _callbacks.motion_event = func
    
    @property
    def system_variable_change(self) -> CallbackSystemVariableChange | None:
        return _callbacks.system_variable_change
    @system_variable_change.setter
    def system_variable_change(self, func: CallbackSystemVariableChange | None) -> None:
        _callbacks.system_variable_change = func

    # ============================
    # Setup / Start / Stop
    # ============================

    def add_controller(self, id: int, name: str, label: str, host: str, port: int = 5108, mac: Optional[str] = None, filtering: bool = False) -> ZenController:
        controller = ZenController(protocol=self.protocol, id=id, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering)
        self.controllers.append(controller)
        self.protocol.set_controllers(self.controllers)
        return controller

    def start(self) -> None:
        self.protocol.set_callbacks(
            button_press_callback = self.button_press_event,
            button_hold_callback = self.button_hold_event,
            absolute_input_callback = self.absolute_input_event,
            level_change_callback = self.level_change_event,
            group_level_change_callback = self.level_change_event,
            scene_change_callback = self.scene_change_event,
            is_occupied_callback = self.is_occupied_event,
            system_variable_change_callback = self.system_variable_change_event,
            colour_change_callback = self.colour_change_event,
            profile_change_callback = self.profile_change_event
        )
        self.protocol.start_event_monitoring()
        if callable(_callbacks.on_connect):
            _callbacks.on_connect()
    
    def stop(self) -> None:
        self.protocol.stop_event_monitoring()
        if callable(_callbacks.on_disconnect):
            _callbacks.on_disconnect()

    # ============================
    # ZenProtocol callbacks
    # ============================ 
        
    def button_press_event(self, instance: ZenInstance, payload: bytes) -> None:
        ZenButton(protocol=self.protocol, instance=instance)._event_received()

    def button_hold_event(self, instance: ZenInstance, payload: bytes) -> None:
        ZenButton(protocol=self.protocol, instance=instance)._event_received(held=True)

    def absolute_input_event(self, instance: ZenInstance, payload: bytes) -> None:
        pass

    def is_occupied_event(self, instance: ZenInstance, payload: bytes) -> None:
        ZenMotionSensor(protocol=self.protocol, instance=instance)._event_received()

    def level_change_event(self, address: ZenAddress, arc_level: int, payload: bytes) -> None:
        if address.type == ZenAddressType.ECG:
            # Delay the light event to allow group updates to arrive and propogate
            ecg = ZenLight(protocol=self.protocol, address=address)
            timer = Timer(0.2, ecg._event_received, kwargs={"level": arc_level})
            timer.start()
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(protocol=self.protocol, address=address)
            group._event_received(level=arc_level)
            for light in group.lights:
                light._event_received(level=arc_level, cascaded_from=group)

    def colour_change_event(self, address: ZenAddress, colour: bytes, payload: bytes) -> None:
        if address.type == ZenAddressType.ECG:
            # Delay the light event to allow group updates to arrive and propogate
            ecg = ZenLight(protocol=self.protocol, address=address)
            timer = Timer(0.2, ecg._event_received, kwargs={"colour": colour})
            timer.start()
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(protocol=self.protocol, address=address)
            group._event_received(colour=colour)
            for light in group.lights:
                light._event_received(colour=colour, cascaded_from=group)

    def scene_change_event(self, address: ZenAddress, scene: int, payload: bytes) -> None:
        print(f"Scene Change Event       - {address} scene {scene}")
        if address.type == ZenAddressType.ECG:
            # Delay the light event to allow group updates to arrive and propogate
            ecg = ZenLight(protocol=self.protocol, address=address)
            timer = Timer(0.1, ecg._event_received, kwargs={"scene": scene})
            timer.start()
        elif address.type == ZenAddressType.GROUP:
            group = ZenGroup(protocol=self.protocol, address=address)
            group._event_received(scene=scene)
            for light in group.lights:
                light._event_received(scene=scene, cascaded_from=group)
    
    def system_variable_change_event(self, controller: ZenController, target: int, value: int, payload: bytes) -> None:
        ZenSystemVariable(protocol=self.protocol, controller=controller, id=target)._event_received(value)

    def profile_change_event(self, controller: ZenController, profile: int, payload: bytes) -> None:
        controller._event_received(profile=profile)
    
    # ============================
    # Abstraction layer commands
    # ============================ 

    def get_profiles(self, controller: Optional[ZenController] = None) -> set[ZenProfile]:
        """Return a set of all profiles."""
        profiles = set()
        controllers = [controller] if controller else self.controllers
        for controller in controllers:
            numbers = self.protocol.query_profile_numbers(controller=controller)
            for number in numbers:
                profile = ZenProfile(protocol=self.protocol, controller=controller, number=number)
                profiles.add(profile)
        return profiles

    def get_groups(self) -> set[ZenGroup]:
        """Return a set of all groups."""
        groups = set()
        for controller in self.controllers:
            addresses = self.protocol.query_group_numbers(controller=controller)
            for address in addresses:
                group = ZenGroup(protocol=self.protocol, address=address)
                groups.add(group)
        return groups
    
    def get_lights(self) -> set[ZenLight]:
        """Return a set of all lights available."""
        lights = set()
        for controller in self.controllers:
            addresses = self.protocol.query_control_gear_dali_addresses(controller=controller)
            for address in addresses:
                light = ZenLight(protocol=self.protocol, address=address)
                lights.add(light)
        return lights
    
    def get_buttons(self) -> set[ZenButton]:
        """Return a set of all buttons available."""
        buttons = set()
        for controller in self.controllers:
            addresses = self.protocol.query_dali_addresses_with_instances(controller=controller)
            for address in addresses:
                instances = self.protocol.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.PUSH_BUTTON:
                        button = ZenButton(protocol=self.protocol, instance=instance)
                        buttons.add(button)
        return buttons
    
    def get_motion_sensors(self) -> set[ZenMotionSensor]:
        """Return a set of all motion sensors available."""
        motion_sensors = set()
        for controller in self.controllers:
            addresses = self.protocol.query_dali_addresses_with_instances(controller=controller)
            for address in addresses:
                instances = self.protocol.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.OCCUPANCY_SENSOR:
                        motion_sensor = ZenMotionSensor(protocol=self.protocol, instance=instance)
                        motion_sensors.add(motion_sensor)
        return motion_sensors

    def get_system_variables(self, give_up_after: int = 10) -> set[ZenSystemVariable]:
        """Return a set of all system variables. Variables must have a label. Searching will give_up_after [x] sequential IDs without a label."""
        sysvars = set()
        failed_attempts = 0
        for controller in self.controllers:
            for variable in range(Const.MAX_SYSVAR):
                label = self.protocol.query_system_variable_name(controller=controller, variable=variable)
                if label:
                    failed_attempts = 0
                    sysvar = ZenSystemVariable(protocol=self.protocol, controller=controller, id=variable)
                    sysvar.label = label
                    sysvars.add(sysvar)
                else:
                    failed_attempts += 1
                    if failed_attempts >= give_up_after:
                        break
        return sysvars

# ============================
# Abstraction layer classes
# ============================ 

class ZenController(SuperZenController):
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, id: int, name: str, label: str, host: str, port: int = 5108, mac: Optional[str] = None, filtering: bool = False):
        # Singleton based on controller name
        if name not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[id] = inst
            inst.protocol = protocol
            inst.id = id
            inst.name = name
            inst.label = label
            inst.host = host
            inst.port = port
            inst.mac = mac
            inst.filtering = filtering
            inst.connected = False
            inst.mac_bytes = bytes.fromhex(inst.mac.replace(':', '')) # Convert MAC address to bytes once
            inst._reset()
        return cls._instances[id]
    def __repr__(self) -> str:
        return f"ZenController<{self.name}>"
    def _reset(self) -> None:
        self.label: Optional[str] = None
        self.version: Optional[int] = None
        self.profile: Optional[ZenProfile] = None
        self.profiles: set[ZenProfile] = set()
        self.lights: set[ZenLight] = set()
        self.groups: set[ZenGroup] = set()
        self.buttons: set[ZenButton] = set()
        self.motion_sensors: set[ZenMotionSensor] = set()
        self.sysvars: set[ZenSystemVariable] = set()
        self.client_data: dict = {}
    def interview(self) -> bool:
        if self.label is None: self.label = self.protocol.query_controller_label(self)
        self.version = self.protocol.query_controller_version_number(self)
        current_profile = self.protocol.query_current_profile_number(self)
        self.profile = ZenProfile(protocol=self.protocol, controller=self, number=current_profile)
        self.connected = True
        return True
    def _event_received(self, profile: Optional[int] = None):
        if profile is not None:
            self.profile = ZenProfile(protocol=self.protocol, controller=self, number=profile)
            if callable(_callbacks.profile_change):
                _callbacks.profile_change(profile=self.profile)
    def get_sysvar(self, id: int) -> ZenSystemVariable:
        return ZenSystemVariable(protocol=self.protocol, controller=self, id=id)
    def is_controller_ready(self) -> bool:
        return self.protocol.query_controller_startup_complete(self)
    def is_dali_ready(self) -> bool:
        return self.protocol.query_is_dali_ready(self)
    def switch_to_profile(self, profile: ZenProfile|int|str) -> bool:
        zp = None
        if isinstance(profile, ZenProfile):
            zp = profile
        elif isinstance(profile, str):
            for p in self.profiles:
                if p.label == profile: zp = p
        elif isinstance(profile, int):
            for p in self.profiles:
                if p.number == profile: zp = p
        if isinstance(zp, ZenProfile):
            print(f"Switching to profile {zp}")
            return self.protocol.change_profile_number(self, zp.number)
        else:
            return False
    def return_to_scheduled_profile(self) -> bool:
        return self.protocol.return_to_scheduled_profile(self)


class ZenProfile:
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, controller: ZenController, number: int):
        # Singleton based on controller and profile number
        compound_id = f"{controller.name} {number}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[compound_id] = inst
            inst.protocol = protocol
            inst.controller = controller
            inst.number = number
            inst._reset()
            inst.interview()
        return cls._instances[compound_id]
    def __repr__(self) -> str:
        return f"ZenProfile<{self.controller.name} profile {self.number}: {self.label}>"
    def _reset(self):
        self.label: Optional[str] = None
        self.client_data: dict = {}
    def interview(self) -> bool:
        self.label = self.protocol.query_profile_label(self.controller, self.number)
        # Add self to controller's set of profiles
        self.controller.profiles.add(self)
        return True
    def select(self) -> bool:
        return self.protocol.change_profile_number(self.controller, self.number)


class ZenLight:
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, address: ZenAddress):
        # Inherited classes should bypass ZenLight __new__
        if cls is not ZenLight:
            return super().__new__(cls)
        # Singleton based on controller and address
        compound_id = f"{address.controller.name} {address.number}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[compound_id] = inst
            inst.protocol = protocol
            inst.address = address
            inst._reset()
            inst.interview()
        return cls._instances[compound_id]
    def __repr__(self) -> str:
        return f"ZenLight<{self.address.controller.name} ecg {self.address.number}: {self.label}>"
    def _reset(self):
        self.label: Optional[str] = None
        self.serial: Optional[str] = None
        self.groups: set[ZenGroup] = set()
        self.features: dict[str, bool] = {
            "brightness": False,
            "temperature": False,
            "RGB": False,
            "RGBW": False,
            "RGBWW": False,
        }
        self.properties: dict[str, Optional[int]] = {
            "min_kelvin": Const.DEFAULT_WARMEST_TEMP,
            "max_kelvin": Const.DEFAULT_COOLEST_TEMP,
        }
        self._scene_labels: list[Optional[str]] = [None] * Const.MAX_SCENE # Scene labels (only used by ZenGroup)
        self._scene_levels: list[Optional[int]] = [None] * Const.MAX_SCENE # Scene levels (only used by ZenLight)
        self._scene_colours: list[Optional[ZenColour]] = [None] * Const.MAX_SCENE # Scene colours (only used by ZenLight)
        self.level: Optional[int] = None
        self.colour: Optional[ZenColour] = None
        self.scene: Optional[int] = None # Current scene number
        self.client_data: dict = {}
    def interview(self) -> bool:
        cgstatus = self.protocol.dali_query_control_gear_status(self.address)
        if cgstatus:
            self.label = self.protocol.query_dali_device_label(self.address, generic_if_none=True)
            self.serial = self.protocol.query_dali_serial(self.address)
            self.cgtype = self.protocol.dali_query_cg_type(self.address)
            
            # If cgtype contains 6, it supports brightness
            if 6 in self.cgtype:
                self.features["brightness"] = True
            
            # If cgtype contains 8, it supports some kind of colour
            if 8 in self.cgtype:
                cgtype = self.protocol.query_dali_colour_features(self.address)
                if cgtype.get("supports_tunable", False) is True:
                    self.features["brightness"] = True
                    self.features["temperature"] = True
                    colour_temp_limits = self.protocol.query_dali_colour_temp_limits(self.address)
                    self.properties["min_kelvin"] = colour_temp_limits.get("soft_warmest", Const.DEFAULT_WARMEST_TEMP)
                    self.properties["max_kelvin"] = colour_temp_limits.get("soft_coolest", Const.DEFAULT_COOLEST_TEMP)
                elif cgtype.get("rgbwaf_channels", 0) == Const.RGB_CHANNELS:
                    self.features["brightness"] = True
                    self.features["RGB"] = True
                elif cgtype.get("rgbwaf_channels", 0) == Const.RGBW_CHANNELS:
                    self.features["brightness"] = True
                    self.features["RGBW"] = True
                elif cgtype.get("rgbwaf_channels", 0) == Const.RGBWW_CHANNELS:
                    self.features["brightness"] = True
                    self.features["RGBWW"] = True
            
            # Scenes
            self._scene_levels = self.protocol.query_scene_levels_by_address(self.address)
            self._scene_colours = self.protocol.query_scene_colours_by_address(self.address)

            # Groups
            groups = self.protocol.query_group_membership_by_address(self.address)
            for group in groups:
                group = ZenGroup(protocol=self.protocol, address=group)
                group.lights.add(self) # Add to group's set of lights
                self.groups.add(group) # Add to light's set of groups
            
            # Add to controller's set of lights
            self.address.controller.lights.add(self)

            return True
        else:
            self._reset()
            return False
    def sync_from_controller(self) -> bool:
        print(f"Syncing light {self}")
        current_level = self.protocol.dali_query_level(self.address)
        current_colour = None
        current_scene = None
        if self.protocol.dali_query_last_scene_is_current(self.address):
            current_scene = self.protocol.dali_query_last_scene(self.address)
        if self.features["temperature"] or self.features["RGB"] or self.features["RGBW"] or self.features["RGBWW"]:
            current_colour = self.protocol.query_dali_colour(self.address)
        # Mimic an incoming event
        print(f"Syncing light {self} - level: {current_level}, colour: {current_colour}, scene: {current_scene}")
        self._event_received(level=current_level, colour=current_colour, scene=current_scene)
    def _event_received(self, level: int = 255, colour: Optional[ZenColour] = None, scene: Optional[int] = None, cascaded_from: Optional[ZenGroup] = None):
        # Called by ZenProtocol when a query command is issued or an event is received
        level_changed = False
        colour_changed = False
        scene_changed = False
        if scene is not None:
            self.scene = scene
            scene_changed = True
            scene_level = self._scene_levels[scene]
            scene_colour = self._scene_colours[scene]
            if scene_level is None:
                pass # The scene has no effect on this light's level
            elif self.level == scene_level:
                pass # The level didn't change
            else:
                self.level = scene_level
                level_changed = True
            if scene_colour is None:
                pass # The scene has no effect on this light's colour
            elif self.colour == scene_colour:
                pass # The colour didn't change
            else:
                self.colour = scene_colour
                colour_changed = True
            if type(self) is ZenGroup:
                # print(f"                              Group {self.address.number} changed to scene {self.scene}")
                pass
            elif type(self) is ZenLight:
                # For each group it's a member of, it must declare the same scene, else we declare it discoordinated
                # print(f"                              Light {self.address.number} changed to scene {self.scene}" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                for group in self.groups:
                    if group.scene != self.scene:
                        # print(f"                              Group {group.address.number} discoordinated after scene set" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                        group.declare_discoordination()
        else:
            if level is not None and level != 255 and level != self.level:
                self.level = level
                level_changed = True
                if self.scene is not None:
                    self.scene = None
                    scene_changed = True
            if colour is not None and colour != self.colour:
                self.colour = colour
                colour_changed = True
                if self.scene is not None:
                    self.scene = None
                    scene_changed = True
            # For each group it's a member of, it must declare the same levels, else we declare it discoordinated
            if type(self) is ZenGroup:
                # print(f"                              Group {self.address.number} changed to {self.level} {self.colour}")
                pass
            elif type(self) is ZenLight:
                # print(f"                              Light {self.address.number} changed to {self.level} {self.colour}" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                for group in self.groups:
                    if (level_changed and group.level != self.level) or (colour_changed and self.colour is not None and group.colour != self.colour):
                        # print(f"                              Group {group.address.number} discoordinated after level set" + f" cascaded from group {cascaded_from.address.number}" if cascaded_from else "")
                        # print(f"                                   {group.level}  {self.level}  {group.colour}  {self.colour}")
                        group.declare_discoordination()
        # Send callbacks to the application
        if type(self) is ZenGroup:
            if level_changed or colour_changed or scene_changed:
                if callable(_callbacks.group_change):
                    _callbacks.group_change(group=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.scene if scene_changed else None)
        elif type(self) is ZenLight:
            if level_changed or colour_changed or scene_changed:
                if callable(_callbacks.light_change):
                    _callbacks.light_change(light=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.scene if scene_changed else None)
    def supports_colour(self, colour: ZenColourType|ZenColour) -> bool:
        if type(colour) == ZenColour:
            colour_type = colour.type
        elif type(colour) == ZenColourType:
            colour_type = colour
        else:
            return False;
        if (colour_type == ZenColourType.TC and self.features["temperature"]) or \
            (colour_type == ZenColourType.RGBWAF and self.features["RGB"]) or \
            (colour_type == ZenColourType.RGBWAF and self.features["RGBW"]) or \
            (colour_type == ZenColourType.RGBWAF and self.features["RGBWW"]):
            return True
        return False
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    def on(self, fade: bool = True) -> bool:
        if not fade: self.protocol.dali_enable_dapc_sequence(self.address)
        return self.protocol.dali_go_to_last_active_level(self.address)
    def off(self, fade: bool = True) -> bool:
        if fade: return self.protocol.dali_arc_level(self.address, 0)
        else: return self.protocol.dali_off(self.address)
    def set_scene(self, scene: int|str|dict, fade: bool = True) -> bool:
        if type(scene) == str:
            scene = next((i for i, s in enumerate(self._scene_labels) if s == scene), False)
        if type(scene) == int:
            if not fade: self.protocol.dali_enable_dapc_sequence(self.address)
            return self.protocol.dali_scene(self.address, scene)
        return False
    def set(self, level: int = 255, colour: Optional[ZenColour] = None, fade: bool = True) -> bool:
        if (self.supports_colour(colour)):
            if not fade: self.protocol.dali_enable_dapc_sequence(self.address)
            return self.protocol.dali_colour(self.address, colour, level)
        if 0 <= level <= 254:
            if fade:
                return self.protocol.dali_arc_level(self.address, level)
            else:
                return self.protocol.dali_custom_fade(self.address, level, 0)
    def dali_inhibit(self, inhibit: bool = True) -> bool:
        return self.protocol.dali_inhibit(self.address, inhibit)
    def dali_on_step_up(self) -> bool:
        return self.protocol.dali_on_step_up(self.address)
    def dali_step_down_off(self) -> bool:
        return self.protocol.dali_step_down_off(self.address)
    def dali_up(self) -> bool:
        return self.protocol.dali_up(self.address)
    def dali_down(self) -> bool:
        return self.protocol.dali_down(self.address)
    def dali_recall_max(self) -> bool:
        return self.protocol.dali_recall_max(self.address)
    def dali_recall_min(self) -> bool:
        return self.protocol.dali_recall_min(self.address)
    def dali_go_to_last_active_level(self) -> bool:
        return self.protocol.dali_go_to_last_active_level(self.address)
    def dali_off(self) -> bool:
        return self.protocol.dali_off(self.address)
    def dali_enable_dapc_sequence(self) -> bool:
        return self.protocol.dali_enable_dapc_sequence(self.address)
    def dali_custom_fade(self, level: int, duration: int) -> bool:
        return self.protocol.dali_custom_fade(self.address, level, duration)
    def dali_stop_fade(self) -> bool:
        return self.protocol.dali_stop_fade(self.address)
        

class ZenGroup(ZenLight):
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, address: ZenAddress):
        # Singleton based on controller and address
        compound_id = f"{address.controller.name} g{address.number}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls, protocol=protocol, address=address)
            cls._instances[compound_id] = inst
            inst.protocol = protocol
            inst.address = address
            inst.lights = set() # self.lights is managed by ZenLight
            inst._reset()
            inst.interview()
        return cls._instances[compound_id]
    def __repr__(self) -> str:
        return f"ZenGroup<{self.address.controller.name} group {self.address.number}: {self.label}>"
    def interview(self) -> bool:
        self.label = self.protocol.query_group_label(self.address, generic_if_none=True)
        self._scene_labels = self.protocol.query_scenes_for_group(self.address, generic_if_none=True)
        # Add to controller's set of groups
        self.address.controller.groups.add(self)
        return True
    def supports_colour(self, colour: ZenColourType|ZenColour) -> bool:
        # If at least one light in the group supports this colour, return True
        for light in self.lights:
            if light.supports_colour(colour):
                return True
        return False
    def get_scene_number_from_label(self, label: str) -> Optional[int]:
        # return list index of label in self._scene_labels
        return next((i for i, s in enumerate(self._scene_labels) if s == label), None)
    def get_scene_label_from_number(self, number: int) -> Optional[str]:
        # return label at index number in self._scene_labels
        return self._scene_labels[number]
    def get_scene_labels(self, exclude_none: bool = False) -> list[Optional[str]]:
        if exclude_none:
            return [label for label in self._scene_labels if label is not None]
        else:
            return self._scene_labels
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    def declare_discoordination(self):
        # Only do something if the group claims to be coordinated
        if self.level is None and self.colour is None and self.scene is None:
            return
        # This is called when members of the group are no longer in a uniform state
        self.level = None
        self.colour = None
        self.scene = None
        if callable(_callbacks.group_change):
            _callbacks.group_change(group=self,
                                    discoordinated=True)
    def contains_dimmable_lights(self) -> bool:
        # Is there at least one ZenLight in self.lights that supports dimming?
        for light in self.lights:
            if light.features["brightness"]:
                return True
        return False
    def contains_temperature_lights(self) -> bool:
        # Is there at least one ZenLight in self.lights that supports temperature?
        for light in self.lights:
            if light.features["temperature"]:
                return True
        return False

class ZenButton:
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, instance: ZenInstance):
        # Singleton based on controller, address, and instance number
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[compound_id] = inst
            inst.protocol = protocol
            inst.instance = instance
            inst._reset()
            inst.interview()
        return cls._instances[compound_id]
    def __repr__(self) -> str:
        return f"ZenButton<{self.instance.address.controller.name} ecd {self.instance.address.number} inst {self.instance.number}: {self.label} / {self.instance_label}>"
    def _reset(self):
        self.serial: Optional[str] = None
        self.label: Optional[str] = None
        self.instance_label: Optional[str] = None
        self.last_press_time: float = time.time()
        self.long_press_count: int = 0
        self.client_data: dict = {}
    def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = addr.controller
        if addr.label is None: addr.label = self.protocol.query_dali_device_label(addr, generic_if_none=True)
        if addr.serial is None: addr.serial = self.protocol.query_dali_serial(addr)
        self.label = addr.label
        self.serial = addr.serial
        self.instance_label = self.protocol.query_dali_instance_label(inst, generic_if_none=True)
        # Add to controller's set of buttons
        ctrl.buttons.add(self)
        return True
    def _event_received(self, held: bool = False):
        if not held:
            if callable(_callbacks.button_press):
                _callbacks.button_press(button=self)
        else:
            seconds_since_last_press = time.time() - self.last_press_time
            # if there's been less than 500 msec between the last hold message, increment the hold count
            if seconds_since_last_press < 0.5:
                self.long_press_count += 1
            else:
                self.long_press_count = 0
            self.last_press_time = time.time()
            # if the hold count is exactly Const.LONG_PRESS_COUNT, call the long press callback
            if self.long_press_count == Const.LONG_PRESS_COUNT:
                if callable(_callbacks.button_long_press):
                    _callbacks.button_long_press(button=self)



class ZenMotionSensor:
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, instance: ZenInstance):
        # Singleton based on controller, address, and instance number
        compound_id = f"{instance.address.controller.name} {instance.address.number} {instance.number}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[compound_id] = inst
            inst.protocol = protocol
            inst.instance = instance
            inst._reset()
            inst.interview()
        return cls._instances[compound_id]
    def __repr__(self) -> str:
        return f"ZenMotionSensor<{self.instance.address.controller.name} ecd {self.instance.address.number} inst {self.instance.number}: {self.label} / {self.instance_label}>"
    def _reset(self):
        self.hold_time: int = Const.DEFAULT_HOLD_TIME
        self.hold_expiry_timer: Optional[Timer] = None
        #
        self.serial: Optional[str] = None
        self.label: Optional[str] = None
        self.instance_label: Optional[str] = None
        self.deadtime: Optional[int] = None
        self.last_detect: Optional[float] = None
        self._occupied: Optional[bool] = None
        #
        self.client_data: dict = {}
    def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        ctrl = addr.controller
        occupancy_timers = self.protocol.query_occupancy_instance_timers(inst)
        if occupancy_timers is not None:
            self.serial = self.protocol.query_dali_serial(addr)
            self.label = self.protocol.query_dali_device_label(addr, generic_if_none=True)
            self.instance_label = self.protocol.query_dali_instance_label(inst, generic_if_none=True)
            self.deadtime = occupancy_timers["deadtime"]
            self.last_detect = time.time() - occupancy_timers["last_detect"]
            self._occupied = None
        else:
            self._reset()
            return False
        # Add to controller's set of motion sensors
        ctrl.motion_sensors.add(self)
        return True
    def _event_received(self):
        self.occupied = True
    def timeout_callback(self):
        self.occupied = False
    @property
    def occupied(self) -> bool:
        seconds_since_last_motion = time.time() - self.last_detect
        within_hold_time = seconds_since_last_motion < self.hold_time
        # if occupied but a hold timer isn't running, start one with the time remaining
        if within_hold_time and self.hold_expiry_timer is None:
            seconds_until_hold_time_expires = self.hold_time - seconds_since_last_motion
            self.hold_expiry_timer = Timer(seconds_until_hold_time_expires, self.timeout_callback)
            self.hold_expiry_timer.start()
        return within_hold_time
    @occupied.setter 
    def occupied(self, new_value: bool):
        old_value = self._occupied or False
        # Cancel any hold time timer
        if self.hold_expiry_timer is not None:
            self.hold_expiry_timer.cancel()
            self.hold_expiry_timer = None
        # Start a new timer
        if new_value:
            # Update last detect time, begin a timer, and set occupied to True
            self.last_detect = time.time()
            self.hold_expiry_timer = Timer(self.hold_time, self.timeout_callback)
            self.hold_expiry_timer.start()
            self._occupied = True
            # If we're going from False to True
            if old_value is False:
                if callable(_callbacks.motion_event):
                    _callbacks.motion_event(sensor=self, occupied=True)
        else:
            self._occupied = False
            self.last_detect = None
            # If we're going from True to False
            if old_value is True:
                if callable(_callbacks.motion_event):
                    _callbacks.motion_event(sensor=self, occupied=False)


class ZenSystemVariable:
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, controller: ZenController, id: int, value: Optional[int] = None, label: Optional[str] = None):
        # Singleton based on controller and id
        compound_id = f"{controller.name} {id}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[compound_id] = inst
            inst.protocol = protocol
            inst.controller = controller
            inst.id = id
            inst._reset()
            inst._value = value
            inst.label = label
            inst.interview()
        return cls._instances[compound_id]
    def __repr__(self) -> str:
        return f"ZenSystemVariable<{self.controller.name} sv {self.id}: {self.label}>"
    def _reset(self):
        self.label: Optional[str] = None
        self._value: Optional[int] = None
        self._future_value: Optional[int] = None
        self.client_data: dict = {}
    def interview(self) -> bool:
        ctrl = self.controller
        if self.label is None:
            self.label = self.protocol.query_system_variable_name(ctrl, self.id)
        if self._value is None:
            self._value = self.protocol.query_system_variable(ctrl, self.id)
        # Add to controller's set of system variables
        ctrl.sysvars.add(self)
        return True
    def _event_received(self, new_value):
        changed = (new_value != self._value)
        by_me = (new_value == self._future_value)
        self._value = new_value
        self._future_value = None
        if changed:
            if callable(_callbacks.system_variable_change):
                _callbacks.system_variable_change(system_variable=self,
                                  value=self._value,
                                  changed=changed,
                                  by_me=by_me)
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    @property
    def value(self):
        # If we don't know the value, request from the controller
        if self._value is None:
            self._value = self.protocol.query_system_variable(self.controller, self.id)
        return self._value
    @value.setter 
    def value(self, new_value):
        self._future_value = new_value # If we get this value back as an event, we'll know it's from us
        self.protocol.set_system_variable(self.controller, self.id, new_value)


