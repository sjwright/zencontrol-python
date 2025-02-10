import time
import logging
import inspect
from zen import ZenProtocol, ZenController as SuperZenController, ZenAddress, ZenInstance, ZenAddressType, ZenColour, ZenColourType, ZenInstanceType
from typing import Optional, List, Callable
from threading import Timer
class Const:

    # DALI limits
    MAX_ECG = 64
    MAX_ECD = 64
    MAX_INSTANCE = 32
    MAX_GROUP = 16
    MAX_SCENE = 12
    MAX_SYSVAR = 147
    MAX_LEVEL = 254 # 255 is mask value (i.e. no change)
    MIN_KELVIN = 1000
    MAX_KELVIN = 20000
    
    # Default color temperature limits
    DEFAULT_WARMEST_TEMP = 2700
    DEFAULT_COOLEST_TEMP = 6500
    
    # RGBWAF channel counts
    RGB_CHANNELS = 3
    RGBW_CHANNELS = 4
    RGBWW_CHANNELS = 5

class _callbacks:
    profile: Optional[Callable] = None
    group: Optional[Callable] = None
    light: Optional[Callable] = None
    button: Optional[Callable] = None
    motion: Optional[Callable] = None
    sysvar: Optional[Callable] = None

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

class ZenInterface:
    def __init__(self,
                 logger: logging.Logger=None,
                 narration: bool = False,
                 unicast: bool = False,
                 listen_ip: Optional[str] = None,
                 listen_port: Optional[int] = None
                 ):
        print("ZenInterface.__init__")
        self.protocol: ZenProtocol = ZenProtocol(logger=logger, narration=narration, unicast=unicast, listen_ip=listen_ip, listen_port=listen_port)
        self.controllers: List[ZenController] = []

    # ============================
    # Setup / Start / Stop
    # ============================

    def add_controller(self, name: str, label: str, host: str, port: int = 5108, mac: Optional[str] = None, filtering: bool = False) -> ZenController:
        controller = ZenController(protocol=self.protocol, name=name, label=label, host=host, port=port, mac=mac, filtering=filtering)
        self.controllers.append(controller)
        return controller
    
    def set_callbacks(self,
                      profile: Optional[Callable] = None,
                      group: Optional[Callable] = None,
                      light: Optional[Callable] = None,
                      button: Optional[Callable] = None,
                      motion: Optional[Callable] = None,
                      sysvar: Optional[Callable] = None
                      ) -> None:
        # Verify callback signatures match expected
        if profile:
            sig = inspect.signature(profile)
            if list(sig.parameters.keys()) != ['profile']:
                raise TypeError("profile callback must accept 'profile' parameter")
        if group:
            sig = inspect.signature(group)
            if list(sig.parameters.keys()) != ['group', 'scene']:
                raise TypeError("group callback must accept 'group' and 'scene' parameters")
        if light:
            sig = inspect.signature(light)
            if list(sig.parameters.keys()) != ['light', 'level', 'colour', 'scene']:
                raise TypeError("light callback must accept 'light', 'level', 'colour', and 'scene' parameters")
        if button:
            sig = inspect.signature(button)
            if list(sig.parameters.keys()) != ['button', 'held']:
                raise TypeError("button callback must accept 'button' and 'held' parameters")
        if motion:
            sig = inspect.signature(motion)
            if list(sig.parameters.keys()) != ['sensor', 'occupied']:
                raise TypeError("motion callback must accept 'sensor' and 'occupied' parameters")
        if sysvar:
            sig = inspect.signature(sysvar)
            if list(sig.parameters.keys()) != ['system_variable', 'value', 'changed', 'by_me']:
                raise TypeError("sysvar callback must accept 'system_variable', 'value', 'changed', and 'by_me' parameters")
        # Update callbacks
        _callbacks.profile = profile
        _callbacks.group = group
        _callbacks.light = light
        _callbacks.button = button
        _callbacks.motion = motion
        _callbacks.sysvar = sysvar

    def start(self) -> None:
        self.protocol.set_controllers(self.controllers)
        self.protocol.set_callbacks(
            button_press_callback = self.button_press_event,
            button_hold_callback = self.button_hold_event,
            absolute_input_callback = self.absolute_input_event,
            level_change_callback = self.level_change_event,
            group_level_change_callback = self.group_level_change_event,
            scene_change_callback = self.scene_change_event,
            is_occupied_callback = self.is_occupied_event,
            system_variable_change_callback = self.system_variable_change_event,
            colour_change_callback = self.colour_change_event,
            profile_change_callback = self.profile_change_event
        )
        self.protocol.start_event_monitoring()
    
    def stop(self) -> None:
        self.protocol.stop_event_monitoring()

    # ============================
    # ZenProtocol callbacks
    # ============================ 
        
    def button_press_event(self, instance: ZenInstance, payload: bytes) -> None:
        ZenButton(protocol=self.protocol, instance=instance)._event_received()

    def button_hold_event(self, instance: ZenInstance, payload: bytes) -> None:
        ZenButton(protocol=self.protocol, instance=instance)._event_received(held=True)

    def absolute_input_event(self, instance: ZenInstance, payload: bytes) -> None:
        pass

    def level_change_event(self, address: ZenAddress, arc_level: int, payload: bytes) -> None:
        ZenLight(protocol=self.protocol, address=address)._event_received(level=arc_level)

    def group_level_change_event(self, address: ZenAddress, arc_level: int, payload: bytes) -> None:
        ZenGroup(protocol=self.protocol, address=address)._event_received(level=arc_level)

    def scene_change_event(self, address: ZenAddress, scene: int, payload: bytes) -> None:
        if address.type == ZenAddressType.ECG:
            ZenLight(protocol=self.protocol, address=address)._event_received(scene=scene)
        elif address.type == ZenAddressType.GROUP:
            ZenGroup(protocol=self.protocol, address=address)._event_received(scene=scene)

    def is_occupied_event(self, instance: ZenInstance, payload: bytes) -> None:
        ZenMotionSensor(protocol=self.protocol, instance=instance)._event_received()
    
    def system_variable_change_event(self, controller: ZenController, target: int, value: int, payload: bytes) -> None:
        ZenSystemVariable(protocol=self.protocol, controller=controller, id=target)._event_received(value)

    def colour_change_event(self, address: ZenAddress, colour: bytes, payload: bytes) -> None:
        ZenLight(protocol=self.protocol, address=address)._event_received(colour=colour)

    def profile_change_event(self, controller: ZenController, profile: int, payload: bytes) -> None:
        controller._event_received(profile=profile)
    
    # ============================
    # Abstraction layer commands
    # ============================ 

    def get_profiles(self, controller: Optional[ZenController] = None) -> List[ZenProfile]:
        """Return a list of all profiles."""
        profiles = []
        controllers = [controller] if controller else self.controllers
        for controller in controllers:
            numbers = self.protocol.query_profile_numbers(controller=controller)
            for number in numbers:
                profile = ZenProfile(protocol=self.protocol, controller=controller, number=number)
                profiles.append(profile)
        return profiles

    def get_groups(self) -> List[ZenGroup]:
        """Return a list of all groups."""
        groups = []
        for controller in self.controllers:
            addresses = self.protocol.query_group_numbers(controller=controller)
            for address in addresses:
                group = ZenGroup(protocol=self.protocol, address=address)
                groups.append(group)
        return groups
    
    def get_lights(self) -> List[ZenLight]:
        """Return a list of all lights available."""
        lights = []
        for controller in self.controllers:
            addresses = self.protocol.query_control_gear_dali_addresses(controller=controller)
            for address in addresses:
                light = ZenLight(protocol=self.protocol, address=address)
                lights.append(light)
        return lights
    
    def get_buttons(self) -> List[ZenButton]:
        """Return a list of all buttons available."""
        buttons = []
        for controller in self.controllers:
            addresses = self.protocol.query_dali_addresses_with_instances(controller=controller)
            for address in addresses:
                instances = self.protocol.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.PUSH_BUTTON:
                        button = ZenButton(protocol=self.protocol, instance=instance)
                        buttons.append(button)
        return buttons
    
    def get_motion_sensors(self) -> List[ZenMotionSensor]:
        """Return a list of all motion sensors available."""
        motion_sensors = []
        for controller in self.controllers:
            addresses = self.protocol.query_dali_addresses_with_instances(controller=controller)
            for address in addresses:
                instances = self.protocol.query_instances_by_address(address=address)
                for instance in instances:
                    if instance.type == ZenInstanceType.OCCUPANCY_SENSOR:
                        motion_sensor = ZenMotionSensor(protocol=self.protocol, instance=instance)
                        motion_sensors.append(motion_sensor)
        return motion_sensors

    def get_system_variables(self, give_up_after: int = 10) -> List[ZenSystemVariable]:
        """Return a list of all system variables. Will give up searching after give_up_after sequential failures."""
        sysvars = []
        failed_attempts = 0
        for controller in self.controllers:
            for variable in range(Const.MAX_SYSVAR):
                label = self.protocol.query_system_variable_name(controller=controller, variable=variable)
                if label:
                    failed_attempts = 0
                    sysvar = ZenSystemVariable(protocol=self.protocol, controller=controller, id=variable)
                    sysvar.label = label
                    sysvars.append(sysvar)
                else:
                    failed_attempts += 1
                    if failed_attempts >= give_up_after:
                        break
        return sysvars

# ============================
# Abstraction layer classes
# ============================ 

class ZenController (SuperZenController):
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, name: str, label: str, host: str, port: int = 5108, mac: Optional[str] = None, filtering: bool = False):
        # Singleton based on controller name
        if name not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[name] = inst
            inst.protocol = protocol
            inst.name = name
            inst.label = label
            inst.host = host
            inst.port = port
            inst.mac = mac
            inst.filtering = filtering
            inst.version = None
            inst.mac_bytes = bytes.fromhex(inst.mac.replace(':', '')) # Convert MAC address to bytes once
            inst.reset()
            inst.interview()
        return cls._instances[name]
    def __repr__(self) -> str:
        return f"ZenController<{self.name}>"
    def reset(self) -> None:
        self.label: Optional[str] = None
        self.version: Optional[int] = None
        self.profile: Optional[ZenProfile] = None
        self.profiles: set[ZenProfile] = set()
        self.client_data: dict = {}
    def interview(self) -> bool:
        if self.label is None: self.label = self.protocol.query_controller_label(self)
        self.version = self.protocol.query_controller_version_number(self)
        current_profile = self.protocol.query_current_profile_number(self)
        self.profile = ZenProfile(protocol=self.protocol, controller=self, number=current_profile)
        return True
    def _event_received(self, profile: Optional[int] = None):
        if profile is not None:
            self.profile = ZenProfile(protocol=self.protocol, controller=self, number=profile)
            if callable(self.protocol.profile_callback):
                self.protocol.profile_callback(profile=self.profile)
    def get_sysvar(self, id: int) -> ZenSystemVariable:
        return ZenSystemVariable(protocol=self.protocol, controller=self, id=id)
    def is_controller_ready(self) -> bool:
        return self.protocol.query_controller_startup_complete(self)
    def is_dali_ready(self) -> bool:
        return self.protocol.query_is_dali_ready(self)
    def switch_to_profile(self, profile: ZenProfile|int|str) -> bool:
        if isinstance(profile, ZenProfile):
            print(f"Switching to profile {profile.number} ({profile.label})")
            return self.protocol.change_profile_number(self, profile.number)
        elif isinstance(profile, int):
            print(f"Switching to profile {profile}")
            return self.protocol.change_profile_number(self, profile)
        elif isinstance(profile, str):
            for p in self.profiles:
                if p.label == profile:
                    print(f"Switching to profile {p.number} ({p.label})")
                    return self.protocol.change_profile_number(self, p.number)
            return False
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
            "min_kelvin": None,
            "max_kelvin": None,
        }
        self.level: Optional[int] = None
        self.colour: Optional[ZenColour] = None
        self.current_scene: Optional[int] = None
        self.client_data: dict = {}
    def interview(self) -> bool:
        cgstatus = self.protocol.dali_query_control_gear_status(self.address)
        if cgstatus:
            self.label = self.protocol.query_dali_device_label(self.address, generic_if_none=True)
            self.serial = self.protocol.query_dali_serial(self.address)
            # Colour features
            cgtype = self.protocol.query_dali_colour_features(self.address)
            if cgtype.get("supports_tunable", False) is True:
                self.features["brightness"] = True
                self.features["temperature"] = True
                self.colour = self.protocol.query_dali_colour(self.address)
                colour_temp_limits = self.protocol.query_dali_colour_temp_limits(self.address)
                self.properties["min_kelvin"] = colour_temp_limits.get("soft_warmest", Const.DEFAULT_WARMEST_TEMP)
                self.properties["max_kelvin"] = colour_temp_limits.get("soft_coolest", Const.DEFAULT_COOLEST_TEMP)
            elif cgtype.get("rgbwaf_channels", 0) == Const.RGB_CHANNELS:
                self.features["brightness"] = True
                self.features["RGB"] = True
                self.colour = self.protocol.query_dali_colour(self.address)
            elif cgtype.get("rgbwaf_channels", 0) == Const.RGBW_CHANNELS:
                self.features["brightness"] = True
                self.features["RGBW"] = True
                self.colour = self.protocol.query_dali_colour(self.address)
            elif cgtype.get("rgbwaf_channels", 0) == Const.RGBWW_CHANNELS:
                self.features["brightness"] = True
                self.features["RGBWW"] = True
                self.colour = self.protocol.query_dali_colour(self.address)
            groups = self.protocol.query_group_membership_by_address(self.address)
            for group in groups:
                group = ZenGroup(protocol=self.protocol, address=group)
                group.lights.add(self) # Add to group's set of lights
                self.groups.add(group) # Add to light's set of groups
            # Sync light state
            # self.sync_from_controller()
            return True
        else:
            self._reset()
            return False
    def sync_from_controller(self) -> bool:
        self.level = self.protocol.dali_query_level(self.address)
        last_scene = self.protocol.dali_query_last_scene(self.address)
        last_scene_is_current = self.protocol.dali_query_last_scene_is_current(self.address)
        self.current_scene = last_scene if last_scene_is_current else None
        if self.features["temperature"] or self.features["RGB"] or self.features["RGBW"] or self.features["RGBWW"]:
            self.colour = self.protocol.query_dali_colour(self.address)
        # Callback
        self._event_received(level=self.level, colour=self.colour, scene=self.current_scene)
    def _event_received(self, level: int = 255, colour: Optional[ZenColour] = None, scene: Optional[int] = None):
        # Called by ZenProtocol when a query command is issued or an event is received
        level_changed = False
        colour_changed = False
        scene_changed = False
        if level != 255 and level != self.level:
            self.level = level
            level_changed = True
        if colour and colour != self.colour:
            self.colour = colour
            colour_changed = True
        if scene:
            self.current_scene = scene
            scene_changed = True
        if type(self) is ZenLight:
            if level_changed or colour_changed or scene_changed:
                if callable(_callbacks.light):
                    _callbacks.light(light=self,
                                    level=self.level if level_changed else None,
                                    colour=self.colour if colour_changed else None,
                                    scene=self.current_scene if scene_changed else None)
        if type(self) is ZenGroup:
            if scene_changed:
                if callable(_callbacks.group):
                    _callbacks.group(group=self,
                                         scene=self.current_scene if scene_changed else None)
    def supports_colour(self, colour: ZenColourType|ZenColour) -> bool:
        # colour_type = colour if type(colour) == ZenColourType else colour.type
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
    def on(self, fade: bool = True):
        if not fade: self.protocol.dali_enable_dapc_sequence(self.address)
        return self.protocol.dali_go_to_last_active_level(self.address)
    def off(self, fade: bool = True):
        if fade: return self.protocol.dali_arc_level(self.address, 0)
        else: return self.protocol.dali_off(self.address)
    def set_scene(self, scene: int, fade: bool = True):
        if not fade: self.protocol.dali_enable_dapc_sequence(self.address)
        return self.protocol.dali_scene(self.address, scene)
    def set(self, level: int = 255, colour: Optional[ZenColour] = None, fade: bool = True):
        if (self.supports_colour(colour)):
            if not fade: self.protocol.dali_enable_dapc_sequence(self.address)
            return self.protocol.dali_colour(self.address, colour, level)
        if level:
            if fade:
                return self.protocol.dali_arc_level(self.address, level)
            else:
                return self.protocol.dali_custom_fade(self.address, level, 0)
    def dali_inhibit(self, inhibit: bool = True):
        return self.protocol.dali_inhibit(self.address, inhibit)
    def dali_on_step_up(self):
        return self.protocol.dali_on_step_up(self.address)
    def dali_step_down_off(self):
        return self.protocol.dali_step_down_off(self.address)
    def dali_up(self):
        return self.protocol.dali_up(self.address)
    def dali_down(self):
        return self.protocol.dali_down(self.address)
    def dali_recall_max(self):
        return self.protocol.dali_recall_max(self.address)
    def dali_recall_min(self):
        return self.protocol.dali_recall_min(self.address)
    def dali_go_to_last_active_level(self):
        return self.protocol.dali_go_to_last_active_level(self.address)
    def dali_off(self):
        return self.protocol.dali_off(self.address)
    def dali_enable_dapc_sequence(self):
        return self.protocol.dali_enable_dapc_sequence(self.address)
    def dali_custom_fade(self, level: int, duration: int):
        return self.protocol.dali_custom_fade(self.address, level, duration)
    def dali_stop_fade(self):
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
    def _reset(self):
        self.label: Optional[str] = None
        self.level: Optional[int] = None
        self.colour: Optional[ZenColour] = None
        self.scenes: list[dict] = []
        self.current_scene: Optional[int] = None
        self.client_data: dict = {}
    def interview(self) -> bool:
        self.label = self.protocol.query_group_label(self.address, generic_if_none=True)
        self.scenes = []
        scene_numbers = self.protocol.query_scene_numbers_for_group(self.address)
        for scene_number in scene_numbers:
            scene_label = self.protocol.query_scene_label_for_group(self.address, scene_number, generic_if_none=True)
            self.scenes.append({
                "number": scene_number,
                "label": scene_label,
            })
        return True
    def supports_colour(self, colour: ZenColourType|ZenColour) -> bool:
        return True
    # -----------------------------------------------------------------------------------------
    # REMINDER: None of the following methods should update the internal object state directly.
    #   These methods send commands to the controller. The controller sends events back.
    #   The events update the internal state.
    # -----------------------------------------------------------------------------------------
    def set_scene(self, scene: int|str, fade: bool = True):
        if type(scene) == str:
            scene = next((s["number"] for s in self.scenes if s["label"] == scene), None)
        return super().set_scene(scene, fade)


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
        self.client_data: dict = {}
    def interview(self) -> bool:
        inst = self.instance
        addr = inst.address
        if addr.label is None: addr.label = self.protocol.query_dali_device_label(addr, generic_if_none=True)
        if addr.serial is None: addr.serial = self.protocol.query_dali_serial(addr)
        self.label = addr.label
        self.serial = addr.serial
        self.instance_label = self.protocol.query_dali_instance_label(inst, generic_if_none=True)
        return True
    def _event_received(self, held: bool = False):
        if callable(_callbacks.button):
            _callbacks.button(button=self, held=held)


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
        self.hold_time: int = 60
        self.hold_timer: Optional[Timer] = None
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
        occupancy_timers = self.protocol.query_occupancy_instance_timers(inst)
        if occupancy_timers is not None:
            self.serial = self.protocol.query_dali_serial(addr)
            self.label = self.protocol.query_dali_device_label(addr, generic_if_none=True)
            self.instance_label = self.protocol.query_dali_instance_label(inst, generic_if_none=True)
            self.deadtime = occupancy_timers["deadtime"]
            self.last_detect = time.time() - occupancy_timers["last_detect"]
            self._occupied = None
            return True
        else:
            self._reset()
            return False
    def _event_received(self):
        self.occupied = True
    def timeout_callback(self):
        self.occupied = False
    @property
    def occupied(self) -> bool:
        return (time.time() - self.last_detect) < self.hold_time
    @occupied.setter 
    def occupied(self, new_value: bool):
        old_value = self._occupied or False
        # Cancel any hold time timer
        if self.hold_timer is not None:
            self.hold_timer.cancel()
            self.hold_timer = None
        # Start a new timer
        if new_value:
            # Update last detect time, begin a timer, and set occupied to True
            self.last_detect = time.time()
            self.hold_timer = Timer(self.hold_time, self.timeout_callback)
            self.hold_timer.start()
            self._occupied = True
            # If we're going from False to True
            if old_value is False:
                if callable(_callbacks.motion):
                    _callbacks.motion(sensor=self, occupied=True)
        else:
            self._occupied = False
            self.last_detect = None
            # If we're going from True to False
            if old_value is True:
                if callable(_callbacks.motion):
                    _callbacks.motion(sensor=self, occupied=False)


class ZenSystemVariable:
    _instances = {}
    def __new__(cls, protocol: ZenProtocol, controller: ZenController, id: int, label: Optional[str] = None):
        # Singleton based on controller and id
        compound_id = f"{controller.name} {id}"
        if compound_id not in cls._instances:
            inst = super().__new__(cls)
            cls._instances[compound_id] = inst

            if not isinstance(protocol, ZenProtocol):
                print(f"Error: protocol must be of type ZenProtocol, not {type(protocol)}")
                return None
            inst.protocol = protocol
            inst.controller = controller
            inst.id = id
            inst._reset()
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
        if self.label is None:
            self.label = self.protocol.query_system_variable_name(self.controller, self.id)
        if self._value is None:
            self._value = self.protocol.query_system_variable(self.controller, self.id)
        return True
    def _event_received(self, new_value):
        changed = (new_value != self._value)
        by_me = (new_value == self._future_value)
        self._value = new_value
        self._future_value = None
        if changed:
            if callable(_callbacks.sysvar):
                _callbacks.sysvar(system_variable=self,
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
        self._future_value = new_value
        # If abs(value) is less than 32760, 
        #   If value has 2 decimal places, use magitude -2 (signed 0xfe)
        #   Else if value has 1 decimal place, use magitude -1 (signed 0xff)
        #   Else use magitude 0 (signed 0x00)
        # Else if abs(value) is less than 327600, use magitude 1 (signed 0x01)
        # Else if abs(value) is less than 3276000, use magitude 2 (signed 0x02)
        self.protocol.set_system_variable(self.controller, self.id, new_value)


