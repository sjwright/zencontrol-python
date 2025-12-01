import asyncio
import ipaddress
import time
import json
import yaml
import re
from typing import Optional, Any
import zencontrol
from zencontrol import ZenController, ZenProtocol, ZenClient, ZenColour, ZenColourType, ZenProfile, ZenLight, ZenGroup, ZenButton, ZenMotionSensor, ZenSystemVariable, ZenTimeoutError, ZenAddressType
import aiomqtt
from colorama import Fore, Back, Style
import logging
from logging.handlers import RotatingFileHandler
import math
import pickle
import traceback

class RateLimiter:
    """Rate limiter to control concurrent coroutine execution"""
    
    def __init__(self, max_concurrent: int = 5, delay_between_batches: float = 0.1):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delay_between_batches = delay_between_batches
        self.last_batch_time = 0
        
    async def execute(self, coro):
        """Execute a coroutine with rate limiting"""
        # Ensure minimum delay between batches
        current_time = time.time()
        time_since_last_batch = current_time - self.last_batch_time
        if time_since_last_batch < self.delay_between_batches:
            await asyncio.sleep(self.delay_between_batches - time_since_last_batch)
        
        async with self.semaphore:
            self.last_batch_time = time.time()
            return await coro
    
    async def execute_batch(self, coros, batch_size: int = None):
        """Execute multiple coroutines in controlled batches"""
        if batch_size is None:
            batch_size = self.semaphore._value  # Use semaphore limit as batch size
            
        results = []
        for i in range(0, len(coros), batch_size):
            batch = coros[i:i + batch_size]
            batch_results = await asyncio.gather(*[self.execute(coro) for coro in batch])
            results.extend(batch_results)
            
        return results

class Const:

    STARTUP_POLL_DELAY = 10

    # MQTT settings
    MQTT_RECONNECT_MIN_DELAY = 1
    MQTT_RECONNECT_MAX_DELAY = 10
    MQTT_SERVICE_PREFIX = "zencontrol-python"
    
    # Logging
    LOG_FILE = 'mqtt.log'
    DEBUG_FILE = 'mqtt.debug.log'
    LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
    LOG_BACKUP_COUNT = 5
    
    # Logarithmic constants

    # Based on curves seen in DALI documentation
    # LOG_A = 44.74
    # LOG_B = 37.77

    # Attempt at a better fit, less extreme
    LOG_A = -59.53
    LOG_B = 56.58

    # Default hold time for motion sensors, in seconds
    DEFAULT_HOLD_TIME = 15

    # Default long press time for buttons, in msec
    DEFAULT_LONG_PRESS_TIME = 1000

class ZenMQTTBridge:
    """Bridge between Zen lighting control system and MQTT/Home Assistant.
    
    Handles bidirectional communication between Zen lighting controllers and 
    Home Assistant via MQTT, including auto-discovery and state management.
    """
    
    # ================================
    #          INIT & RUN
    # ================================
    
    def __init__(self, config_path: str = "examples/config.yaml") -> None:
        self.config: dict[str, Any]
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        
        self.logger: logging.Logger
        self.discovery_prefix: str
        self.control: list[ZenController]
        self.zen: zencontrol.ZenControl
        self.mqttc: aiomqtt.Client
        self.setup_started: bool = False
        self.setup_complete: bool = False
        self.system_variables: list[ZenSystemVariable] = []
        self.control: list[ZenController] = []
        self.sv_config: list[dict] = []

        self.config_topics_to_delete: list[str] = [] # List of topics to delete after completing setup
        self.topic_object: dict[str, Any] = {} # Map of topics to objects
        
        # Rate limiter for controlling concurrent operations
        self.rate_limiter = RateLimiter(max_concurrent=5, delay_between_batches=0.1)

        self.global_config: dict[str, Any] = {
            "origin": {
                "name": "zencontrol-python",
                "sw": "0.0.0",
                "url": "https://github.com/sjwright/zencontrol-python"
            }
        }

    async def run(self) -> None:
        self.setup_config()
        self.setup_logging()
        self.logger.info("==================================== Starting ZenMQTTBridge ====================================")
        await self.setup_zen()
        await self.setup_mqtt()
        
        # Wait for Zen controllers to be ready
        for ctrl in self.control:
            print(f"Connecting to Zen controller {ctrl.name} on {ctrl.host}:{ctrl.port}...")
            self.logger.info(f"Connecting to Zen controller {ctrl.name} on {ctrl.host}:{ctrl.port}...")

            try:
                # ctrl.is_controller_ready() returns True when ready, False when starting, None when connection failed
                
                while not await ctrl.is_controller_ready():
                    print(f"Controller {ctrl.label} still starting up...")
                    await asyncio.sleep(Const.STARTUP_POLL_DELAY)
            
            except ZenTimeoutError as e:
                self.logger.fatal(f"Aborting - Zen controller {ctrl.name} cannot be reached.")
                return # Don't reach the run loop
                
            except Exception as e:
                self.logger.fatal(f"Aborting - Error connecting to Zen controller {ctrl.name}: {e}")
                return # Don't reach the run loop

            # It's ready, interview it.
            await ctrl.interview()
        
        # Start MQTT message handling task
        self.mqtt_task = asyncio.create_task(self._mqtt_message_handler())

        # Wait for all retained topics to arrive
        await asyncio.sleep(0.5)

        # Generate config topics
        self.setup_started = True
        await self.setup_profiles()
        await self.setup_lights()
        await self.setup_groups()
        await self.setup_buttons()
        await self.setup_motion_sensors()
        await self.setup_system_variables()
        await self.delete_retained_topics()
        self.setup_complete = True

        # Begin listening for zen events
        await self.zen.start()

        with open("examples/cache.pkl", "wb") as f:
            pickle.dump(self.zen.cache, f)
        
        clist = []
        for c in sorted(self.control, key=lambda x: x.id):
            c: ZenController
            glist = []
            llist = []
            for g in sorted(c.groups, key=lambda x: x.address.number):
                g: ZenGroup
                glist.append({
                    "id": g.address.number,
                    "label": g.label,
                })
            for l in sorted(c.lights, key=lambda x: x.address.number):
                l: ZenLight
                llist.append({
                    "ecg": l.address.number,
                    "label": l.label,
                })
            clist.append({
                "id": c.id,
                "name": c.name,
                "label": c.label,
                "groups": glist,
                "lights": llist
            })
        # Dump to yaml file
        with open("examples/dump.yaml", "w") as f:
            yaml.dump(clist, f, sort_keys=False)

        while True:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Clean shutdown of the bridge"""
        await self.zen.stop()
        if hasattr(self, 'mqtt_task'):
            self.mqtt_task.cancel()
            try:
                await self.mqtt_task
            except asyncio.CancelledError:
                pass
        # MQTT connection is automatically closed by the context manager


    # ================================
    #            CONFIG
    # ================================

    def setup_config(self) -> None:
        try:
            
            # Improved config validation
            required_sections = ['homeassistant', 'mqtt', 'zencontrol']
            missing = [s for s in required_sections if s not in self.config]
            if missing:
                raise ValueError(f"Missing required config sections: {', '.join(missing)}")
                
            # Validate MQTT config
            mqtt_required = ['host', 'port', 'user', 'password', 'keepalive']
            missing = [f for f in mqtt_required if f not in self.config['mqtt']]
            if missing:
                raise ValueError(f"Missing MQTT config fields: {', '.join(missing)}")
            
            # Validate Zencontrol config
            if not isinstance(self.config['zencontrol'], list):
                raise ValueError("zencontrol config must be a list")
            
            zencontrol_required = ['id', 'name', 'label', 'mac', 'host', 'port']
            for i, config in enumerate(self.config['zencontrol']):

                # Check for required fields
                missing = [f for f in zencontrol_required if f not in config]
                if missing:
                    raise ValueError(f"Missing Zencontrol config fields in entry {i}: {', '.join(missing)}")
                
                # Validate name format (alphanumeric only)
                name = config['name']
                if not re.match(r'^[A-Za-z0-9]+$', name):
                    raise ValueError(f"Invalid name format in Zencontrol config {i}: {name}. Use only letters and numbers.")
                
                # Validate MAC address format (xx:xx:xx:xx:xx:xx)
                mac = config['mac']
                if not re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', mac):
                    raise ValueError(f"Invalid MAC address format in Zencontrol config {i}: {mac}")
                
                # Validate IP address
                host = config['host']
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    raise ValueError(f"Invalid IP address in Zencontrol config {i}: {host}")
                
                # Validate port number (1-65535)
                port = config['port']
                if not isinstance(port, int) or port < 1 or port > 65535:
                    raise ValueError(f"Invalid port number in Zencontrol config {i}: {port}")
            
        except ValueError as e:
            self.logger.error(f"Failed to load config file: {e}")
            raise

    # ================================
    #             LOGGING
    # ================================

    def setup_logging(self) -> None:
        """Configure logging with both file and console handlers."""
        self.logger = logging.getLogger('ZenMQTTBridge')
        self.logger.setLevel(logging.DEBUG)

        # File handler
        file_handler = RotatingFileHandler(
            Const.LOG_FILE,
            maxBytes=Const.LOG_MAX_BYTES,
            backupCount=Const.LOG_BACKUP_COUNT
        )
        # Exclude debug messages
        file_handler.addFilter(lambda record: record.levelno != logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(fmt="%(asctime)s\t%(levelname)s\t%(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(file_handler)
        
        # Debug only handler
        debug_handler = logging.FileHandler(Const.DEBUG_FILE)
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(logging.Formatter(fmt="%(asctime)s\t%(levelname)s\t%(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(debug_handler)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s.%(msecs)03d %(levelname)s: %(message)s', datefmt='%H:%M:%S'
        ))
        self.logger.addHandler(console_handler)

    # ================================
    #              ZEN
    # ================================

    async def setup_zen(self) -> None:
        try:
            try:
                with open("examples/cache.pkl", "rb") as infile:
                    cache = pickle.load(infile)
            except FileNotFoundError:
                cache = {}
            self.zen: zencontrol.ZenControl = zencontrol.ZenControl(logger=self.logger, print_traffic=True, cache=cache)
            self.zen.on_connect = self._zen_on_connect
            self.zen.on_disconnect = self._zen_on_disconnect
            self.zen.profile_change = self._zen_profile_change
            self.zen.group_change = self._zen_group_change
            self.zen.light_change = self._zen_light_change
            self.zen.button_press = self._zen_button_press
            self.zen.button_long_press = self._zen_button_long_press
            self.zen.motion_event = self._zen_motion_event
            self.zen.system_variable_change = self._zen_system_variable_change
            for config in self.config['zencontrol']:
                ctrl = self.zen.add_controller(
                    id=config['id'],
                    name=config['name'],
                    label=config['label'],
                    host=config['host'],
                    port=config['port'],
                    mac=config['mac']
                )
                # Add to my own internal list
                self.control.append(ctrl)
                # Add system variables to list
                for sv in config.get('system_variables', []):
                    sv['controller'] = ctrl
                    self.sv_config.append(sv)
        except Exception as e:
            self.logger.error(f"Failed to initialize Zen: {e}")
            raise
    
    async def _zen_on_connect(self) -> None:
        self.logger.info("Connected to Zen controllers")

    async def _zen_on_disconnect(self) -> None:
        self.logger.info("Disconnected from Zen controllers")
    
    # ================================
    #        ZEN PUBLISHING
    # ================================
    
    def kelvin_to_mireds(self, kelvin: int) -> int:
        """Convert color temperature in Kelvin to Mireds (micro reciprocal degrees)"""
        if kelvin <= 0: return 0
        return round(1000000 / kelvin)

    def mireds_to_kelvin(self, mireds: int) -> int:
        """Convert Mireds (micro reciprocal degrees) to color temperature in Kelvin"""
        if mireds <= 0: return 0
        return round(1000000 / mireds)
    
    def arc_to_brightness(self, value):
        """Convert logarithmic DALI ARC value (0-254) to linear brightness (0-255)"""
        if value <= 0: return 0
        X = round(math.exp((value - Const.LOG_A) / Const.LOG_B))
        # print(f"arc_to_brightness({value}) = {X}")
        return X

    def brightness_to_arc(self, value):
        """Convert linear brightness (0-255) to logarithmic DALI ARC value (0-254)"""
        if value <= 0: return 0
        X = round(Const.LOG_A + Const.LOG_B * math.log(value))
        # print(f"brightness_to_arc({value}) = {X}")
        return X

    # ================================
    #              MQTT
    # ================================
    
    async def setup_mqtt(self) -> None:
        """Set up MQTT configuration. Connection is handled by the message handler."""
        try:
            self.discovery_prefix = self.config['homeassistant']['discovery_prefix']
            self.logger.info("MQTT configuration set up successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to set up MQTT configuration: {e}")
            raise

    async def _mqtt_message_handler(self) -> None:
        """Handle incoming MQTT messages with automatic reconnection per aiomqtt docs."""
        interval = Const.MQTT_RECONNECT_MIN_DELAY
        
        while True:
            try:
                # Create a new client for each connection attempt
                mqtt_config = self.config["mqtt"]
                
                # Create will messages for availability
                will_messages = []
                for ctrl in self.control:
                    will_messages.append(aiomqtt.Will(
                        topic=f"{Const.MQTT_SERVICE_PREFIX}/{ctrl.name}/availability",
                        payload="offline",
                        retain=True
                    ))
                
                # Create client with will messages
                if will_messages:
                    client = aiomqtt.Client(
                        hostname=mqtt_config["host"],
                        port=mqtt_config["port"],
                        username=mqtt_config["user"],
                        password=mqtt_config["password"],
                        keepalive=mqtt_config["keepalive"],
                        will=will_messages[0]  # aiomqtt only supports one will message
                    )
                else:
                    client = aiomqtt.Client(
                        hostname=mqtt_config["host"],
                        port=mqtt_config["port"],
                        username=mqtt_config["user"],
                        password=mqtt_config["password"],
                        keepalive=mqtt_config["keepalive"]
                    )
                
                # Use the client context manager for automatic connection handling
                async with client:
                    self.mqttc = client  # Store reference for publishing methods
                    
                    # Subscribe to topics
                    for ctrl in self.control:
                        await client.subscribe(f"{self.discovery_prefix}/light/{ctrl.name}/#")
                        await client.subscribe(f"{self.discovery_prefix}/binary_sensor/{ctrl.name}/#")
                        await client.subscribe(f"{self.discovery_prefix}/sensor/{ctrl.name}/#")
                        await client.subscribe(f"{self.discovery_prefix}/switch/{ctrl.name}/#")
                        await client.subscribe(f"{self.discovery_prefix}/event/{ctrl.name}/#")
                        await client.subscribe(f"{self.discovery_prefix}/select/{ctrl.name}/#")
                        await client.subscribe(f"{self.discovery_prefix}/device_automation/{ctrl.name}/#")
                        await client.publish(f"{Const.MQTT_SERVICE_PREFIX}/{ctrl.name}/availability", "online", retain=True)
                    
                    self.logger.info("Successfully connected to MQTT broker")
                    
                    # Process messages
                    async for message in client.messages:
                        await self._mqtt_on_message(message)
                        
            except asyncio.CancelledError:
                self.logger.info("MQTT message handler cancelled")
                break
            except aiomqtt.MqttError as e:
                self.logger.warning(f"MQTT connection lost: {e}")
                self.logger.info(f"Reconnecting in {interval} seconds...")
                await asyncio.sleep(interval)
                # Reset interval for next attempt
                interval = Const.MQTT_RECONNECT_MIN_DELAY
            except Exception as e:
                self.logger.error(f"Unexpected error in MQTT message handler: {e}")
                self.logger.info(f"Retrying in {interval} seconds...")
                await asyncio.sleep(interval)
                # Exponential backoff for unexpected errors
                interval = min(interval * 2, Const.MQTT_RECONNECT_MAX_DELAY)

    async def _mqtt_on_message(self, msg: aiomqtt.Message) -> None:
        """Handle incoming MQTT messages with improved error handling."""
        try:

            # Debug
            payload_str = msg.payload.decode('UTF-8') if msg.payload else ""
            topic_str = str(msg.topic)
            # self.logger.debug(f"MQTT received - {topic_str}: {payload_str}")
            # print(Fore.YELLOW + f"MQTT received - {topic_str}: " + Style.DIM + f"{payload_str}" + Style.RESET_ALL)
            
            # Get the last part of the topic
            command = topic_str.split('/')[-1]
            
            # Config commands are ignored
            if command == "config":
                # If we haven't started setup yet, it's a retained topic, so add to delete list
                if not self.setup_started:
                    self.config_topics_to_delete.append(topic_str)
                return
            
            # State commands are always ignored
            if command == "state":
                return
            
            # Only set commands from here onwards
            if command != "set":
                return
            
            # Get the base topic from the message
            base_topic = topic_str.rsplit('/', 1)[0]
            
            # Find the matching object in our map
            target_object = self.topic_object.get(base_topic)

            # If we don't have an object, ignore the message
            if not target_object:
                self.logger.debug(f"No matching object found for {base_topic}")
                return
            
            # If setup is not complete, ignore the message
            if not self.setup_complete:
                self.logger.debug(f"Setup not complete, ignoring message {msg.topic}")
                return
            
            # Convert payload to str
            payload = msg.payload.decode('UTF-8') if msg.payload else ""

            # Match on object type
            if isinstance(target_object, ZenController):
                await self._mqtt_profile_change(target_object, payload)
            elif isinstance(target_object, ZenGroup):
                if "/select/" in base_topic:
                    await self._mqtt_groupscene_change(target_object, payload)
                elif "/light/" in base_topic:
                    await self._mqtt_light_change(target_object, json.loads(payload))
            elif isinstance(target_object, ZenLight):
                await self._mqtt_light_change(target_object, json.loads(payload))
            elif isinstance(target_object, ZenSystemVariable):
                await self._mqtt_system_variable_change(target_object, payload)
            elif isinstance(target_object, ZenMotionSensor):
                return  # Read only
            else:
                print(f"Unknown object type found in self.topic_object: {type(target_object)}")
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON payload: {e}")

    # ================================
    #        MQTT PUBLISHING
    # ================================
    
    def _client_data_for_object(self, object: Any, component: str, attributes: dict = {}) -> dict:
        if isinstance(object, ZenController):
            ctrl = object
            serial = ctrl.mac
            mqtt_target = "profile"
        elif isinstance(object, ZenGroup):  # ZenGroup inherits from ZenLight, so needs to be before ZenLight
            group = object
            addr = group.address
            ctrl = addr.controller
            serial = ""
            mqtt_target = f"group{addr.number}"
        elif isinstance(object, ZenLight):
            light = object
            addr = light.address
            ctrl = addr.controller
            serial = light.serial
            mqtt_target = f"ecg{addr.number}"
        elif isinstance(object, ZenButton):
            button = object
            inst = button.instance
            addr = inst.address
            ctrl = addr.controller
            serial = button.serial
            mqtt_target = f"ecd{addr.number}_inst{inst.number}"
        elif isinstance(object, ZenMotionSensor):
            sensor = object
            inst = sensor.instance
            addr = inst.address
            ctrl = addr.controller
            serial = sensor.serial
            mqtt_target = f"ecd{addr.number}_inst{inst.number}"
        elif isinstance(object, ZenSystemVariable):
            sysvar = object
            ctrl = sysvar.controller
            serial = component
            mqtt_target = f"sv{sysvar.id}"
        else:
            raise ValueError(f"Unknown object type: {type(object)}")
        # Build default attributes - passed attributes can override default_entity_id
        default_attrs = {
            "component": component,
            "default_entity_id": f"{component}.{ctrl.name}_{mqtt_target}",
            "unique_id": f"{ctrl.name}_{mqtt_target}_{serial}",
            "device": {
                "manufacturer": "Zencontrol",
                "identifiers": f"zencontrol-{ctrl.name}",
                "sw_version": ctrl.version,
                "name": ctrl.label,
            },
            "availability_topic": f"{Const.MQTT_SERVICE_PREFIX}/{ctrl.name}/availability",
        }
        # Merge: attributes take precedence over default_attrs
        object.client_data[component] = object.client_data.get(component, {}) | {
            "component": component,
            "attributes": default_attrs | attributes,
            "mqtt_target": mqtt_target,
            "mqtt_topic": f"{self.discovery_prefix}/{component}/{ctrl.name}/{mqtt_target}",
        }
        return object.client_data[component]
    
    async def _publish_config(self, topic: str, config: dict, object: Any = None, retain: bool = True) -> None:
        if object:
            self.topic_object[topic] = object
        config_topic = f"{topic}/config"
        config_json = json.dumps(config)
        await self.mqttc.publish(config_topic, config_json, retain=retain)
        if config_topic in self.config_topics_to_delete: self.config_topics_to_delete.remove(config_topic)
        # self.logger.debug(f"MQTT sent - {topic}/config: {config_json}")
        # print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/config: " + Style.DIM + f"{config_json}" + Style.RESET_ALL)
    
    async def _publish_state(self, topic: str, state: str|dict|None, retain: bool = False) -> None:
        if isinstance(state, dict): state = json.dumps(state)
        await self.mqttc.publish(f"{topic}/state", state, retain=retain)
        
        if "/light/" in topic or "/group/" in topic:
            self.logger.debug(f"MQTT sent - {topic}/state: {state}")
        # self.logger.debug(f"MQTT sent - {topic}/state: {state}")
        # print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/state: " + Style.DIM + f"{state}" + Style.RESET_ALL)
    
    async def _publish_event(self, topic: str, event: str, retain: bool = False) -> None:
        await self.mqttc.publish(f"{topic}/event", event, retain=retain)
        # self.logger.debug(f"MQTT sent - {topic}/event: {event}")
        # print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/event: " + Style.DIM + f"{event}" + Style.RESET_ALL)

    async def delete_retained_topics(self) -> None:
        for topic in self.config_topics_to_delete:
            await self.mqttc.publish(topic, None, retain=True)
            # self.logger.debug(f"MQTT deleted - {topic}")
            # print(Fore.RED + f"•• MQTT DELETED •• " + Style.DIM + f"{topic}" + Style.RESET_ALL)

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    # ================================
    #           PROFILES
    # ================================

    async def setup_profiles(self) -> set[ZenProfile]:
        """Initialize all profiles for Home Assistant auto-discovery."""
        all_profiles = set()
        for ctrl in self.control:
            client_data = self._client_data_for_object(ctrl, "select")
            mqtt_topic = client_data['mqtt_topic']
            profiles = await self.zen.get_profiles(ctrl)
            config_dict = self.global_config | client_data.get("attributes",{}) | {
                "name": f"{ctrl.label} Profile",
                "command_topic": f"{mqtt_topic}/set",
                "state_topic": f"{mqtt_topic}/state",
                "options": [
                    profile.label for profile in profiles
                ]
            }
            await self._publish_config(mqtt_topic, config_dict, object=ctrl)
            await self._publish_state(mqtt_topic, ctrl.profile.label)
            all_profiles.update(profiles)
        return all_profiles

    async def _mqtt_profile_change(self, ctrl: ZenController, payload: str) -> None:
        print(f"HA asking to change profile of {ctrl.name} to {payload}")
        await ctrl.switch_to_profile(payload)

    async def _zen_profile_change(self, profile: ZenProfile) -> None:
        print(f"Zen to HA: profile changed to {profile}")

        ctrl = profile.controller
        mqtt_topic = ctrl.client_data.get("select", {}).get('mqtt_topic', None)
        if not mqtt_topic:
            self.logger.error(f"Controller {ctrl} has no MQTT topic")
            return

        await self._publish_state(mqtt_topic, profile.label)

    # ================================
    #            LIGHTS
    # ================================

    async def setup_lights(self) -> set[ZenLight]:
        """Initialize all lights for Home Assistant auto-discovery."""
        lights = await self.zen.get_lights()
        
        # First, publish all configs (this is fast and doesn't need rate limiting)
        for light in lights:
            client_data = self._client_data_for_object(light, "light")
            mqtt_topic = client_data['mqtt_topic']
            config_dict = self.global_config | client_data.get("attributes",{}) | {
                "name": light.label,
                "schema": "json",
                "payload_off": "OFF",
                "payload_on": "ON",
                "command_topic": f"{mqtt_topic}/set",
                "state_topic": f"{mqtt_topic}/state",
                "json_attributes_topic": f"{mqtt_topic}/attributes",
                "effect": False,
                "retain": False,
                "brightness": light.features["brightness"],
                "supported_color_modes": [],
            }
            if light.features["RGBWW"]:
                config_dict["supported_color_modes"] = ["rgbww"]
            elif light.features["RGBW"]:
                config_dict["supported_color_modes"] = ["rgbw"]
            elif light.features["RGB"]:
                config_dict["supported_color_modes"] = ["rgb"]
            elif light.features["temperature"]:
                config_dict["supported_color_modes"] = ["color_temp"]
                config_dict["min_mireds"] = self.kelvin_to_mireds(light.properties["max_kelvin"]) if light.properties["max_kelvin"] is not None else None
                config_dict["max_mireds"] = self.kelvin_to_mireds(light.properties["min_kelvin"]) if light.properties["min_kelvin"] is not None else None
            elif light.features["brightness"]:
                config_dict["supported_color_modes"] = ["brightness"]
            else:
                config_dict["supported_color_modes"] = ["onoff"]

            await self._publish_config(mqtt_topic, config_dict, object=light)

        # Then, sync all lights with rate limiting to prevent server overload
        sync_coros = [light.refresh_state_from_controller() for light in lights]
        await self.rate_limiter.execute_batch(sync_coros)

        # Return all lights
        return lights

    async def _mqtt_light_change(self, light: ZenLight|ZenGroup, payload: dict[str, Any]) -> None:
        addr = light.address
        ctrl = addr.controller
        state: Optional[str] = payload.get("state", None)
        brightness: Optional[int] = payload.get("brightness", None)
        mireds: Optional[int] = payload.get("color_temp", None)

        # If brightness or temperature is set
        if brightness or mireds:
            args = {}
            if brightness: args["level"] = self.brightness_to_arc(brightness)
            if mireds: args["colour"] = ZenColour(type=ZenColourType.TC, kelvin=self.mireds_to_kelvin(mireds))
            self.logger.info(f"♥️💡 Command from HA: {ctrl.name} setting gear {addr.number} to {args}")
            await light.set(**args)
            return
        
        # If switched on/off in HA
        if state == "OFF":
            self.logger.info(f"♥️💡 Command from HA: {ctrl.name} turning gear {addr.number} OFF")
            await light.off(fade=True)
        elif state == "ON":
            self.logger.info(f"♥️💡 Command from HA: {ctrl.name} turning gear {addr.number} ON")
            await light.on()

    async def _zen_light_change(self, light: ZenLight, level: Optional[int] = None, colour: Optional[ZenColour] = None, scene: Optional[int] = None) -> None:
        typestr = "group" if light.address.type == ZenAddressType.GROUP else "light"
        emoji = "👥" if light.address.type == ZenAddressType.GROUP else "💡"
        self.logger.info(f"🩵{emoji} Event from Zen: {typestr} {light.address.number}  level {level if level is not None else '--'}  colour {colour if colour is not None else '--'}  scene {scene if scene is not None else '--'}")
        
        # log traceback
        # self.logger.debug(f"   stack trace: {traceback.format_stack()}")

        mqtt_topic = light.client_data.get("light", {}).get('mqtt_topic', None)
        if not mqtt_topic:
            self.logger.error(f"Light {light} has no MQTT topic")
            return

        new_state = {
            "state": "OFF" if light.level == 0 else "ON"
        }

        if light.level and light.level > 0:
            new_state["brightness"] = self.arc_to_brightness(light.level)

        if light.colour and light.colour.type == ZenColourType.TC:
            new_state["color_mode"] = "color_temp"
            if light.colour.kelvin is not None:
                new_state["color_temp"] = self.kelvin_to_mireds(light.colour.kelvin)

        await self._publish_state(mqtt_topic, new_state)

    # ================================
    #           GROUPS
    # ================================

    async def setup_groups(self) -> set[ZenGroup]:
        """Initialize all groups for Home Assistant auto-discovery."""
        groups = await self.zen.get_groups()
        # Group-lights
        for group in groups:
            if group.lights:
                client_data = self._client_data_for_object(group, "light")
                mqtt_topic = client_data['mqtt_topic']
                config_dict = self.global_config | client_data.get("attributes",{}) | {
                    "name": group.label,
                    "schema": "json",
                    "payload_off": "OFF",
                    "payload_on": "ON",
                    "command_topic": f"{mqtt_topic}/set",
                    "state_topic": f"{mqtt_topic}/state",
                    "json_attributes_topic": f"{mqtt_topic}/attributes",
                    "effect": False,
                    "retain": False,
                    "brightness": False,
                }
                if group.contains_temperature_lights():
                    config_dict = config_dict | {
                        "brightness": True,
                        "supported_color_modes": ["color_temp"],
                        "min_mireds": self.kelvin_to_mireds(group.properties["max_kelvin"]),
                        "max_mireds": self.kelvin_to_mireds(group.properties["min_kelvin"]),
                    }
                elif group.contains_dimmable_lights():
                    config_dict = config_dict | {
                        "brightness": True,
                        "supported_color_modes": ["brightness"],
                    }
                else:
                    config_dict = config_dict | {
                        "supported_color_modes": ["onoff"],
                    }
                await self._publish_config(mqtt_topic, config_dict, object=group)
                # Get the latest state from the controller and trigger an event, which then sends a state update
                await group.refresh_state_from_controller()

        # Group-scenes
        for group in groups:
            if group.lights:
                client_data = self._client_data_for_object(group, "select")
                mqtt_topic = client_data['mqtt_topic']
                config_dict = self.global_config | client_data.get("attributes",{}) | {
                    "name": group.label,
                    "command_topic": f"{mqtt_topic}/set",
                    "state_topic": f"{mqtt_topic}/state",
                    "options": group.get_scene_labels(exclude_none=True),
                }
                await self._publish_config(mqtt_topic, config_dict, object=group)
                await self._publish_state(mqtt_topic, group.scene)

        # Return all groups
        return groups
            
    async def _mqtt_groupscene_change(self, group: ZenGroup, payload: str) -> None:
        self.logger.info(f"♥️👥 Command from HA: group {group.address.number} to scene {payload}")
        await group.set_scene(payload)
    
    # mqtt group light change calls _mqtt_light_change
        
    async def _zen_group_change(self, group: ZenGroup, level: Optional[int] = None, colour: Optional[ZenColour] = None, scene: Optional[int] = None, discoordinated: Optional[bool] = None) -> None:
        select_mqtt_topic = group.client_data.get("select", {}).get('mqtt_topic', None)

        # Get the scene label for the ID from the group
        if select_mqtt_topic and scene is not None:
            scene_label = group.get_scene_label_from_number(scene)
            if scene_label:
                await self._publish_state(select_mqtt_topic, scene_label)
            else:
                await self._publish_state(select_mqtt_topic, "None")
                self.logger.warning(f"Group {group} has no scene with ID {scene}")
        
        # If discoordinated, set the group-light's state to null and return
        if discoordinated:
            self.logger.info(f"🩵👥 Event from Zen: group {group.address.number}  discoordinated")
            await self._publish_state(select_mqtt_topic, "None")
            light_mqtt_topic = group.client_data.get("light", {}).get('mqtt_topic', None)
            await self._publish_state(light_mqtt_topic, {"state": None})
            return
        
        # Do light stuff
        await self._zen_light_change(light=group, level=level, colour=colour, scene=scene)

    # ================================
    #           BUTTONS
    # ================================

    async def setup_buttons(self) -> set[ZenButton]:
        """Initialize all buttons found on the DALI bus for Home Assistant auto-discovery."""
        buttons = await self.zen.get_buttons()
        for button in buttons:
            client_data = self._client_data_for_object(button, "device_automation")
            button.long_press_time = Const.DEFAULT_LONG_PRESS_TIME
            mqtt_topic = client_data['mqtt_topic']
            config_dict = self.global_config | client_data.get("attributes",{}) | {
                "automation_type": "trigger",
                "subtype": re.sub(r'[^a-z0-9]', '_', button.label.lower()) + "_" + re.sub(r'[^a-z0-9]', '_', button.instance_label.lower()),
                "type": "button_short_press",
                "payload": "button_short_press",
                "topic": f"{mqtt_topic}/event",
            }
            await self._publish_config(mqtt_topic, config_dict, object=button)
            # For long press, we use a different topic for the config, but the same topic for the event payload
            config_dict = config_dict | {
                "type": "button_long_press",
                "payload": "button_long_press",
            }
            await self._publish_config(mqtt_topic + "_long_press", config_dict, object=button)
        
        # Return all buttons
        return buttons
        
    async def _zen_button_press(self, button: ZenButton) -> None:
        self.logger.debug(f"🩵👆 Event from Zen: button press {button}")
        mqtt_topic = button.client_data.get("device_automation", {}).get("mqtt_topic", None)
        if not mqtt_topic:
            self.logger.error(f"Button {button} has no MQTT topic")
            return
        await self._publish_event(mqtt_topic, "button_short_press")
        
    async def _zen_button_long_press(self, button: ZenButton) -> None:
        self.logger.debug(f"🩵👆 Event from Zen: button LONG press {button}")
        mqtt_topic = button.client_data.get("device_automation", {}).get("mqtt_topic", None)
        if not mqtt_topic:
            self.logger.error(f"Button {button} has no MQTT topic")
            return
        await self._publish_event(mqtt_topic, "button_long_press")

    # ================================
    #         MOTION SENSORS
    # ================================

    async def setup_motion_sensors(self) -> set[ZenMotionSensor]:
        """Initialize all motion sensors found on the DALI bus for Home Assistant auto-discovery."""
        sensors = await self.zen.get_motion_sensors()
        for sensor in sensors:
            client_data = self._client_data_for_object(sensor, "binary_sensor")
            sensor.hold_time = Const.DEFAULT_HOLD_TIME
            mqtt_topic = client_data['mqtt_topic']
            config_dict = self.global_config | client_data.get("attributes",{}) | {
                "name": sensor.instance_label,
                "device_class": "motion",
                "payload_off": "OFF",
                "payload_on": "ON",
                "state_topic": f"{mqtt_topic}/state",
                "json_attributes_topic": f"{mqtt_topic}/attributes",
                "retain": False,
            }
            await self._publish_config(mqtt_topic, config_dict, object=sensor)
            await self._publish_state(mqtt_topic, "ON" if sensor.occupied else "OFF")

        # Return all motion sensors
        return sensors
    
    async def _zen_motion_event(self, sensor: ZenMotionSensor, occupied: bool) -> None:
        self.logger.debug(f"🩵👀 Event from Zen: sensor {sensor} occupied: {occupied}")
        mqtt_topic = sensor.client_data.get("binary_sensor", {}).get("mqtt_topic", None)
        if not mqtt_topic:
            self.logger.error(f"Sensor {sensor} has no MQTT topic")
            return
        await self._publish_state(mqtt_topic, "ON" if occupied else "OFF")

    # ================================
    #        SYSTEM VARIABLES
    # ================================

    async def setup_system_variables(self) -> set[ZenSystemVariable]:
        """Initialize system variables in config.yaml for Home Assistant auto-discovery."""
        
        # On first run, prep system variables with client_data
        if not self.system_variables:
            for sv in self.sv_config:
                ctrl: ZenController = sv['controller']
                zsv = ctrl.get_sysvar(sv['id'])
                attr = sv['attributes'] | {
                    "default_entity_id": f"{sv['component']}.{sv['object_id']}",
                    "unique_id": f"{ctrl.name}_{sv['object_id']}"
                }
                self._client_data_for_object(zsv, sv['component'], attributes=attr)
                self.system_variables.append(zsv)

        for zsv in self.system_variables:
            if zsv.client_data.get("switch", None):
                client_data = zsv.client_data["switch"]
                mqtt_topic = client_data["mqtt_topic"]
                config_dict = self.global_config | client_data.get("attributes",{}) | {
                    "component": "switch",
                    "state_topic": f"{mqtt_topic}/state",
                    "command_topic": f"{mqtt_topic}/set",
                    "payload_off": "OFF",
                    "payload_on": "ON",
                    "retain": False,
                }
            elif zsv.client_data.get("sensor", None):
                client_data = zsv.client_data["sensor"]
                mqtt_topic = client_data["mqtt_topic"]
                config_dict = self.global_config | client_data.get("attributes",{}) | {
                    "component": "sensor",
                    "state_topic": f"{mqtt_topic}/state",
                    "retain": False,
                }
            else:
                continue

            await self._publish_config(mqtt_topic, config_dict, object=zsv)
            await self._publish_state(mqtt_topic, await zsv.get_value())

        # Return all system variables
        return self.system_variables
    
    async def _mqtt_system_variable_change(self, sysvar: ZenSystemVariable, payload: str) -> None:
        self.logger.debug(f"♥️⚡️ Command from HA: {sysvar.controller.name} system variable {sysvar.id} set to {payload}")
        if sysvar.client_data.get("switch", None):
            await sysvar.set_value(1 if payload == "ON" else 0)
        elif sysvar.client_data.get("sensor", None):
            return # Read only

    async def _zen_system_variable_change(self, system_variable: ZenSystemVariable, value:int, changed: bool, by_me: bool) -> None:
        self.logger.debug(f"🩵⚡️ Event from Zen: {system_variable.controller.name} system variable {system_variable.id} set to {value}")
        # print(f"System Variable Change Event - controller {system_variable.controller.name} system_variable {system_variable.id} value {value} changed {changed} by_me {by_me}")
        if system_variable.client_data.get("switch", None):
            mqtt_topic = system_variable.client_data["switch"]["mqtt_topic"]
            await self._publish_state(mqtt_topic, "OFF" if value == 0 else "ON")
        elif system_variable.client_data.get("sensor", None):
            mqtt_topic = system_variable.client_data["sensor"]["mqtt_topic"]
            await self._publish_state(mqtt_topic, value)
        else:
            self.logger.error(f"Ignoring system variable {system_variable}")
        return
    
# Usage
async def main():
    bridge = ZenMQTTBridge()
    try:
        await bridge.run()
    except KeyboardInterrupt:
        print("Shutting down...")
        await bridge.stop()

if __name__ == "__main__":
    asyncio.run(main())