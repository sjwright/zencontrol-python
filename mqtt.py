import ipaddress
import time
import json
import yaml
import re
from typing import Optional, Any, Callable
from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance, ZenAddressType, ZenColourGeneric, ZenColourTC, ZenLight, ZenMotionSensor, ZenSystemVariable
import paho.mqtt.client as mqtt
from colorama import Fore, Back, Style
import logging
from logging.handlers import RotatingFileHandler
import math

class Constants:

    # Default color temperature limits
    DEFAULT_WARMEST_TEMP = 2700
    DEFAULT_COOLEST_TEMP = 6500
    
    # RGBWAF channel counts
    RGB_CHANNELS = 3
    RGBW_CHANNELS = 4
    RGBWW_CHANNELS = 5
    
    # Timing
    STARTUP_POLL_DELAY = 5

    # MQTT settings
    MQTT_RECONNECT_MIN_DELAY = 1
    MQTT_RECONNECT_MAX_DELAY = 10
    
    # Logging
    LOG_FILE = 'zenmqtt.log'
    LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
    LOG_BACKUP_COUNT = 5
    
    # Logarithmic constants

    # Based on curves seen in DALI documentation
    # LOG_A = 44.74
    # LOG_B = 37.77

    # Attempt at a better fit, less extreme
    LOG_A = -59.53
    LOG_B = 56.58

class ZenMQTTBridge:
    """Bridge between Zen lighting control system and MQTT/Home Assistant.
    
    Handles bidirectional communication between Zen lighting controllers and 
    Home Assistant via MQTT, including auto-discovery and state management.
    """
    
    def __init__(self, config_path: str = "config.yaml") -> None:
        self.logger: logging.Logger
        self.config: dict[str, Any]
        self.discovery_prefix: str
        self.control: list[ZenController]
        self.zen: ZenProtocol
        self.mqttc: mqtt.Client
        self.setup_started: bool = False
        self.sv_config: list[dict]
        self.system_variables: list[ZenSystemVariable] = []

        self.config_topics_to_delete: list[str] = [] # List of topics to delete after completing setup
        self.topic_object: dict[str, Any] = {} # Map of topics to objects
        
        # Setup logging
        self.setup_logging()
        self.logger.setLevel(logging.DEBUG)
        self.logger.info("==================================== Starting ZenMQTTBridge ====================================")
        
        # Load configuration
        try:
            with open(config_path) as f:
                self.config = yaml.safe_load(f)
            
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
            
            zencontrol_required = ['name', 'label', 'mac', 'host', 'port']
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
            
        except Exception as e:
            self.logger.error(f"Failed to load config file: {e}")
            raise
        
        # Initialize Zen
        try:
            self.control = []
            self.sv_config = []
            for config in self.config['zencontrol']:
                # Define controller
                ctrl = ZenController(
                    name=config['name'],
                    label=config['label'],
                    mac=config['mac'],
                    host=config['host'],
                    port=config['port']
                )
                # Add to list
                self.control.append(ctrl)
                # Add system variables to list
                for sv in config.get('system_variables', []):
                    sv['controller'] = ctrl
                    self.sv_config.append(sv)
            self.zen = ZenProtocol(controllers=self.control, logger=self.logger, narration=True)
        except Exception as e:
            self.logger.error(f"Failed to initialize Zen: {e}")
            raise
        
        
        self.discovery_prefix = self.config['homeassistant']['discovery_prefix']
        

        # Initialize MQTT
        try:
            self.mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.mqttc.on_connect = self._on_mqtt_connect
            self.mqttc.on_message = self._on_mqtt_message
            self.mqttc.on_disconnect = self._on_mqtt_disconnect
            
            mqtt_config = self.config["mqtt"]
            for ctrl in self.control:
                self.mqttc.will_set(topic=f"{ctrl.name}/availability", payload="offline", retain=True)
            self.mqttc.username_pw_set(mqtt_config["user"], mqtt_config["password"])
            self.mqttc.reconnect_delay_set(1, 30)
            self.mqttc.connect(mqtt_config["host"], mqtt_config["port"], mqtt_config["keepalive"])
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            raise


    def _on_mqtt_disconnect(self, client: mqtt.Client, userdata: Any, 
                          rc: int, properties: Any = None) -> None:
        """Handle MQTT disconnection events."""
        self.logger.warning(f"Disconnected from MQTT broker with code: {rc}")
        if rc != 0:
            self.logger.info("Attempting to reconnect to MQTT broker")

    def setup_logging(self) -> None:
        """Configure logging with both file and console handlers."""
        self.logger = logging.getLogger('ZenMQTTBridge')
        self.logger.setLevel(logging.INFO)

        # File handler
        file_handler = RotatingFileHandler(
            Constants.LOG_FILE,
            maxBytes=Constants.LOG_MAX_BYTES,
            backupCount=Constants.LOG_BACKUP_COUNT
        )
        file_handler.setFormatter(logging.Formatter(fmt="%(asctime)s\t%(levelname)s\t%(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(file_handler)

        # Console handler
        # console_handler = logging.StreamHandler()
        # console_handler.setFormatter(logging.Formatter(
        #     '%(levelname)s: %(message)s'
        # ))
        # self.logger.addHandler(console_handler)

    def _on_mqtt_connect(self, client: mqtt.Client, userdata: Any, flags: dict, 
                        reason_code: int, properties: Any) -> None:
        """Called when the client connects or reconnects to the MQTT broker.

        Args:
            client: MQTT client instance
            userdata: Private user data
            flags: Response flags sent by the broker
            reason_code: Connection result code (0 indicates success)
            properties: Properties from the connection response
        """
        if reason_code == 0:
            self.logger.info("Successfully connected to MQTT broker")
            # Resubscribe to topics in case of reconnection
            for ctrl in self.control:
                client.subscribe(f"{self.discovery_prefix}/light/{ctrl.name}/#")
                client.subscribe(f"{self.discovery_prefix}/binary_sensor/{ctrl.name}/#")
                client.subscribe(f"{self.discovery_prefix}/sensor/{ctrl.name}/#")
                client.subscribe(f"{self.discovery_prefix}/switch/{ctrl.name}/#")
                client.publish(topic=f"{ctrl.name}/availability", payload="online", retain=True)
        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {reason_code}")

    # ================================
    # RECEIVED FROM HOME ASSISTANT
    #  ---> SEND TO ZEN
    # ================================

    def _on_mqtt_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """Handle incoming MQTT messages with improved error handling."""
        try:

            # Debug
            print(Fore.YELLOW + f"MQTT received - {msg.topic}: " + Style.DIM + f"{msg.payload.decode('UTF-8')}" + Style.RESET_ALL)
            
            # Get the last part of the topic
            command = msg.topic.split('/')[-1]
            
            # Config commands are ignored
            if command == "config":
                # If we haven't started setup yet, it's a retained topic, so add to delete list
                if not self.setup_started:
                    self.config_topics_to_delete.append(msg.topic)
                return
            
            # State commands are always ignored
            if command == "state":
                return
            
            # Only set commands from here onwards
            if command != "set":
                return
            
            # Get the base topic from the message
            base_topic = msg.topic.rsplit('/', 1)[0]
            
            # Find the matching object in our map
            target_object = self.topic_object.get(base_topic)

            # If we don't have an object, ignore the message
            if not target_object:
                self.logger.debug(f"No matching object found for topic {base_topic}")
                return
            
            # Convert payload to str
            payload = msg.payload.decode('UTF-8')

            # Match on object type
            match target_object:
                case ZenLight():
                    light: ZenLight = target_object
                    self._apply_mqtt_payload_to_zenlight(light, json.loads(payload))
                case ZenSystemVariable():
                    zsv: ZenSystemVariable = target_object
                    match zsv.client_data['component']:
                        case "switch":
                            self._apply_mqtt_payload_to_zsv(zsv, payload)
                        case "sensor":
                            return # Read only
                case ZenMotionSensor():
                    return # Read only
                case _:
                    print(f"Unknown object type found in self.topic_object: {type(target_object)}")
                    raise ValueError(f"Unknown object type found in self.topic_object: {type(target_object)}")
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON payload: {e}")

    def _apply_mqtt_payload_to_zsv(self, zsv: ZenSystemVariable, payload: str) -> None:
        self.logger.info(f"Setting {zsv.controller.name} system variable {zsv.id} to {payload}")
        match payload:
            case "ON":
                zsv.set_value(1)
            case "OFF":
                zsv.set_value(0)
            case _:
                raise ValueError(f"Invalid payload for system variable: {payload}")

    def _apply_mqtt_payload_to_zenlight(self, light: ZenLight, payload: dict[str, Any]) -> None:
        addr = light.address
        ctrl = addr.controller
        arc_level = None
        kelvin = None
        if "brightness" in payload:
            brightness = int(payload["brightness"])
            arc_level = self.brightness_to_arc(brightness)
        if "color_temp" in payload and light.features["temperature"]:
            mireds = int(payload["color_temp"])
            kelvin = self.mireds_to_kelvin(mireds)
        if kelvin and arc_level:
            self.logger.info(f"Setting {ctrl.name} gear {addr.number} brightness to arc {arc_level} (linear {brightness}) and temperature to {kelvin}K (mireds {mireds})")
            light.set(level=arc_level, kelvin=kelvin)
            return
        elif kelvin:
            self.logger.info(f"Setting {ctrl.name} gear {addr.number} temperature to {kelvin}K (mireds {mireds})")
            light.set(kelvin=kelvin)
            return
        elif arc_level:
            self.logger.info(f"Setting {ctrl.name} gear {addr.number} brightness to arc {arc_level} (linear {brightness}) ")
            light.set(level=arc_level)
            return

        # Respond to on/off switching in Home Assistant
        if "state" in payload:
            state = payload["state"]
            if state == "OFF":
                self.logger.info(f"Turning off {ctrl.name} gear {addr.number}")
                light.off(fade=False)
            elif state == "ON":
                self.logger.info(f"Turning on {ctrl.name} gear {addr.number}")
                light.on()

    # ================================
    # RECEIVED FROM ZEN
    #  ---> SEND TO HOME ASSISTANT
    # ================================

    def _level_change_event(self, address: ZenAddress, arc_level: int, event_data: dict = {}) -> None:
        print(f"Zen to HA: gear {address.number} arc_level {arc_level}")
        self.send_light_level_to_homeassistant(address, arc_level)

    def _colour_change_event(self, address: ZenAddress, colour: ZenColourGeneric, event_data: dict = {}) -> None:
        print(f"Zen to HA: gear {address.number} colour {colour}")
        if isinstance(colour, ZenColourTC):
            self.send_light_temp_to_homeassistant(address, colour.kelvin)
    
    def _motion_sensor_event(self, sensor: ZenMotionSensor, occupied: bool) -> None:
        print(f"Zen to HA: sensor {sensor} occupied: {occupied}")
        mqtt_topic = sensor.client_data['mqtt_topic']
        self._publish_state(mqtt_topic, "ON" if occupied else "OFF")

    def _system_variable_change_event(self, system_variable: ZenSystemVariable, value:int, from_controller: bool) -> None:
        print(f"System Variable Change Event - controller {system_variable.controller.name} system_variable {system_variable.id} value {value} from_controller {from_controller}")
        if 'mqtt_topic' in system_variable.client_data:
            match system_variable.client_data['component']:
                case "switch":
                    self._publish_state(system_variable.client_data['mqtt_topic'], "OFF" if value == 0 else "ON")
                case "sensor":
                    self._publish_state(system_variable.client_data['mqtt_topic'], value)
                case _:
                    raise ValueError(f"Unknown component: {system_variable.client_data['component']}")

        return

    def send_light_level_to_homeassistant(self, address: ZenAddress, arc_level: Optional[int] = None) -> None:
        mqtt_topic = self._mqtt_topic_for_address(address, "light")
        if arc_level is None:
            arc_level = self.zen.dali_query_level(address)
        if arc_level == 0:
            self._publish_state(mqtt_topic, {
                "state": "OFF"
            })
        else:
            brightness = self.arc_to_brightness(arc_level)
            self._publish_state(mqtt_topic, {
                "color_mode": "color_temp",
                "brightness": brightness,
                "state": "ON"
            })

    def send_light_temp_to_homeassistant(self, address: ZenAddress, kelvin: Optional[int] = None, mireds: Optional[int] = None) -> None:
        mqtt_topic = self._mqtt_topic_for_address(address, "light")
        if kelvin is None and mireds is None:
            raise ValueError("Cannot get temperature value dynamically")
        if mireds is None:
            mireds = self.kelvin_to_mireds(kelvin)
        if kelvin is None:
            kelvin = self.mireds_to_kelvin(mireds)
        self._publish_state(mqtt_topic, {
            "color_mode": "color_temp",
            "color": mireds,
            "state": "ON",
        })

    # ================================
    # SETUP AUTODISCOVERY
    # ================================

    def setup_lights(self) -> None:
        """Initialize all lights for Home Assistant auto-discovery."""
        lights = self.zen.get_lights()
        for light in lights:
            self._client_data_for_object(light, "light")
            addr: ZenAddress = light.address
            ctrl: ZenController = addr.controller
            mqtt_topic = light.client_data['mqtt_topic']
            config_dict = light.client_data['attributes'] | {
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
                "supported_color_modes": ["brightness"],
            }
            if light.features["temperature"]:
                config_dict["supported_color_modes"] = ["color_temp"]
                config_dict["min_mireds"] = self.kelvin_to_mireds(light.properties["max_kelvin"]) if light.properties["max_kelvin"] is not None else None
                config_dict["max_mireds"] = self.kelvin_to_mireds(light.properties["min_kelvin"]) if light.properties["min_kelvin"] is not None else None
            elif light.features["RGBWW"]:
                config_dict["supported_color_modes"] = ["rgbww"]
            elif light.features["RGBW"]:
                config_dict["supported_color_modes"] = ["rgbw"]
            elif light.features["RGB"]:
                config_dict["supported_color_modes"] = ["rgb"]
            
            self._publish_config(mqtt_topic, config_dict, object=light)

    def setup_motion_sensors(self) -> None:
        """Initialize all motion sensors found on the DALI bus for Home Assistant auto-discovery."""
        sensors = self.zen.get_motion_sensors()
        for sensor in sensors:
            self._client_data_for_object(sensor, "binary_sensor")
            sensor.hold_time = 5
            inst: ZenInstance = sensor.instance
            addr: ZenAddress = inst.address
            ctrl: ZenController = addr.controller
            mqtt_topic = sensor.client_data['mqtt_topic']
            config_dict = sensor.client_data['attributes'] | {
                "name": sensor.instance_label,
                "device_class": "motion",
                "payload_off": "OFF",
                "payload_on": "ON",
                "state_topic": f"{mqtt_topic}/state",
                "json_attributes_topic": f"{mqtt_topic}/attributes",
                "retain": False,
                "expire_after": 120,
            }
            self._publish_config(mqtt_topic, config_dict, object=sensor)

    def setup_system_variables(self) -> None:
        """Initialize system variables in config.yaml for Home Assistant auto-discovery."""
        
        # On first run, prep system variables with client_data
        if not self.system_variables:
            for sv in self.sv_config:
                ctrl: ZenController = sv['controller']
                zsv = ZenSystemVariable(protocol=self.zen, controller=ctrl, id=sv['id'])
                attr = sv['attributes'] | {
                    "object_id": sv['object_id'],
                    "unique_id": f"{ctrl.name}_{sv['object_id']}"
                }
                self._client_data_for_object(zsv, sv['component'], attributes=attr)
                self.system_variables.append(zsv)

        for zsv in self.system_variables:
            self._client_data_for_object(zsv, zsv.client_data['component'])
            ctrl: ZenController = zsv.controller
            mqtt_topic = zsv.client_data['mqtt_topic']
            match zsv.client_data['component']:
                case "sensor":
                    config_dict = {
                        "component": "sensor",
                        "state_topic": f"{mqtt_topic}/state",
                        "retain": False,
                    }
                case "switch":
                    config_dict = {
                        "component": "switch",
                        "state_topic": f"{mqtt_topic}/state",
                        "command_topic": f"{mqtt_topic}/set",
                        "payload_off": "OFF",
                        "payload_on": "ON",
                        "retain": False,
                    }
                case _:
                    raise ValueError(f"Unknown component: {zsv.client_data['component']}")
            self._publish_config(zsv.client_data['mqtt_topic'], zsv.client_data['attributes'] | config_dict, object=zsv)
    
    def delete_retained_topics(self) -> None:
        for topic in self.config_topics_to_delete:
            self.mqttc.publish(topic, None, retain=True)
            print(Fore.RED + f"•• MQTT DELETED •• " + Style.DIM + f"{topic}" + Style.RESET_ALL)

    # ================================
    # PUBLISH TO MQTT
    # ================================
    
    def _mqtt_topic_for_address(self, address: ZenAddress, component: str) -> str:
        # Search through existing objects to find matching address
        for obj in self.topic_object.values():
            if hasattr(obj, 'address') and obj.address == address:
                return obj.client_data['mqtt_topic']
    
    def _client_data_for_object(self, object: Any, component: str, attributes: dict = {}) -> dict:
        match object:
            case ZenLight():
                light: ZenLight = object
                addr = light.address
                ctrl = addr.controller
                serial = light.serial
                mqtt_target = f"ecg{addr.number}"
            case ZenMotionSensor():
                sensor: ZenMotionSensor = object
                inst = sensor.instance
                addr = inst.address
                ctrl = addr.controller
                serial = sensor.serial
                mqtt_target = f"ecd{addr.number}_inst{inst.number}"
            case ZenSystemVariable():
                zsv: ZenSystemVariable = object
                ctrl = zsv.controller
                serial = component
                mqtt_target = f"sv{zsv.id}"
            case _:
                raise ValueError(f"Unknown object type: {type(object)}")
        object.client_data = object.client_data | {
            "component": component,
            "attributes": attributes | {
                "component": component,
                "object_id": f"{ctrl.name}_{mqtt_target}",
                "unique_id": f"{ctrl.name}_{mqtt_target}_{serial}",
                "device": {
                    "manufacturer": "zencontrol",
                    "identifiers": f"zencontrol-{ctrl.name}",
                    "name": ctrl.label,
                },
                "availability_topic": f"{ctrl.name}/availability",
            },
            "mqtt_target": mqtt_target,
            "mqtt_topic": f"{self.discovery_prefix}/{component}/{ctrl.name}/{mqtt_target}",
        }
        return object.client_data
    
    def _publish_config(self, topic: str, config: dict, object: Any = None, retain: bool = True) -> None:
        self.topic_object[topic] = object
        config_topic = f"{topic}/config"
        config_json = json.dumps(config)
        self.mqttc.publish(config_topic, config_json, retain=retain)
        if config_topic in self.config_topics_to_delete: self.config_topics_to_delete.remove(config_topic)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/config: " + Style.DIM + f"{config_json}" + Style.RESET_ALL)
    
    def _publish_state(self, topic: str, state: str|dict, retain: bool = False) -> None:
        if isinstance(state, dict): state = json.dumps(state)
        self.mqttc.publish(f"{topic}/state", state, retain=retain)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/state: " + Style.DIM + f"{state}" + Style.RESET_ALL)
    
    # ================================
    # UTILITY FUNCTIONS
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
        X = round(math.exp((value - Constants.LOG_A) / Constants.LOG_B))
        print(f"arc_to_brightness({value}) = {X}")
        return X

    def brightness_to_arc(self, value):
        """Convert linear brightness (0-255) to logarithmic DALI ARC value (0-254)"""
        if value <= 0: return 0
        X = round(Constants.LOG_A + Constants.LOG_B * math.log(value))
        print(f"brightness_to_arc({value}) = {X}")
        return X
    
    # ================================
    # MAIN LOOP
    # ================================

    def run(self) -> None:
        """Main method to start the bridge."""
        try:
            
            # Wait for Zen controllers to be ready
            for ctrl in self.control:
                print(f"Connecting to Zen controller {ctrl.label} on {ctrl.host}:{ctrl.port}...")
                self.logger.info(f"Connecting to Zen controller {ctrl.label} on {ctrl.host}:{ctrl.port}...")
                while not self.zen.query_is_dali_ready(controller=ctrl):
                    print(f"DALI bus not ready...")
                    time.sleep(Constants.STARTUP_POLL_DELAY)
                while not self.zen.query_controller_startup_complete(controller=ctrl):
                    print(f"Controller still starting up...")
                    time.sleep(Constants.STARTUP_POLL_DELAY)
                
            # Start event monitoring and MQTT services
            self.zen.set_callbacks(
                level_change_callback=self._level_change_event,
                colour_change_callback=self._colour_change_event,
                system_variable_change_callback=self._system_variable_change_event,
                motion_sensor_callback=self._motion_sensor_event
            )
            self.zen.start_event_monitoring()
            self.mqttc.loop_start()

            # Wait for retained topics to be received
            time.sleep(0.3)

            self.setup_started = True
            self.setup_lights()
            self.setup_motion_sensors()
            self.setup_system_variables()
            self.delete_retained_topics()

            # Use signal handling for graceful shutdown
            import signal
            signal.signal(signal.SIGINT, lambda s, f: self.stop())
            signal.signal(signal.SIGTERM, lambda s, f: self.stop())
            
            while True:
                time.sleep(1)
                
        except Exception as e:
            print(f"Fatal error: {e}")
            self.logger.error(f"Fatal error: {e}")
            raise
        
        finally:
            print(f"Stopping: {e}")
            self.stop()

    def stop(self) -> None:
        """Clean shutdown of the bridge"""
        self.zen.stop_event_monitoring()
        self.mqttc.loop_stop()
        self.mqttc.disconnect()

# Usage
if __name__ == "__main__":
    bridge = ZenMQTTBridge()
    bridge.run()