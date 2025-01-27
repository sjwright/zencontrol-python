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
            
            # Split string by / and check if it starts with discovery_prefix
            topic_parts = msg.topic.split('/')
            
            # Most of what we deal with has five parts
            if len(topic_parts) == 5:
                prefix = topic_parts[0]
                component = topic_parts[1]
                controller = topic_parts[2]
                target = topic_parts[3]
                command = topic_parts[4]
                payload = json.loads(msg.payload)
                
                # Ignore messages not for us        
                if prefix != self.discovery_prefix: return

                # Match to a controller, or ignore
                ctrl = next((c for c in self.control if c.name == controller), None)
                if not ctrl: return

                # Process commands
                match command:
                    case "config":
                        # If we haven't started yet, delete old retained config entries
                        if not self.setup_started:
                            client.publish(msg.topic, None, retain=True)
                        return
                    case "set":
                        match component:
                            case "light":
                                match_ecg = re.search(r'ecg(\d+)', target)
                                if not match_ecg: return
                                address = int(match_ecg.group(1))
                                print(f"Parsed MQTT: {command} {component} ECG {address} = {payload}")
                                addr = ZenAddress(ctrl, type=ZenAddressType.ECG, number=address)
                                light = ZenLight(protocol=self.zen, address=addr)
                                self._apply_payload_to_zenlight(light, payload)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON payload: {e}")
        except Exception as e:
            self.logger.error(f"Error processing MQTT message: {e}")

    def _apply_payload_to_zenlight(self, light: ZenLight, payload: dict[str, Any]) -> None:
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
        inst = sensor.instance
        addr = inst.address
        ctrl = addr.controller
        mqtt_target = f"ecd{addr.number}_inst{inst.number}"
        mqtt_topic = f"{self.discovery_prefix}/binary_sensor/{ctrl.name}/{mqtt_target}"
        self._publish_state(mqtt_topic, "ON" if occupied else "OFF")

    def _system_variable_change_event(self, system_variable: ZenSystemVariable, value:int, from_controller: bool) -> None:
        print(f"System Variable Change Event - controller {system_variable.controller.name} system_variable {system_variable.id} value {value} from_controller {from_controller}")
        if 'mqtt_topic' in system_variable.client_data:
            self._publish_state(system_variable.client_data['mqtt_topic'], value)
        return

    def send_light_level_to_homeassistant(self, address: ZenAddress, arc_level: Optional[int] = None) -> None:
        if arc_level is None:
            arc_level = self.zen.dali_query_level(address)
        if arc_level == 0:
            self._send_state_mqtt(address, "light", {
                "state": "OFF"
            })
        else:
            brightness = self.arc_to_brightness(arc_level)
            self._send_state_mqtt(address, "light", {
                "color_mode": "color_temp",
                "brightness": brightness,
                "state": "ON"
            })

    def send_light_temp_to_homeassistant(self, address: ZenAddress, kelvin: Optional[int] = None, mireds: Optional[int] = None) -> None:
        if kelvin is None and mireds is None:
            raise ValueError("Cannot get temperature value dynamically")
        if mireds is None:
            mireds = self.kelvin_to_mireds(kelvin)
        if kelvin is None:
            kelvin = self.mireds_to_kelvin(mireds)
        self._send_state_mqtt(address, "light", {
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
            ctrl = light.address.controller
            mqtt_topic = f"{self.discovery_prefix}/light/{ctrl.name}/ecg{light.address.number}"
            
            config_dict = {
                "component": "light",
                "name": light.label,
                "object_id": f"{ctrl.name}_ecg{light.address.number}",
                "unique_id": f"{ctrl.name}_ecg{light.address.number}_{light.serial}",
                "schema": "json",
                "payload_off": "OFF",
                "payload_on": "ON",
                "command_topic": f"{mqtt_topic}/set",
                "state_topic": f"{mqtt_topic}/state",
                "availability_topic": f"{ctrl.name}/availability",
                "json_attributes_topic": f"{mqtt_topic}/attributes",
                "device": {
                    "manufacturer": "zencontrol",
                    "identifiers": f"zencontrol-{ctrl.name}",
                    "name": ctrl.label,
                },
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
            
            self._publish_config(mqtt_topic, config_dict)

    def setup_motion_sensors(self) -> None:
        """Initialize all motion sensors found on the DALI bus for Home Assistant auto-discovery."""
        sensors = self.zen.get_motion_sensors()
        for sensor in sensors:
            sensor.hold_time = 5
            inst = sensor.instance
            addr = inst.address
            ctrl = addr.controller
            mqtt_target = f"ecd{addr.number}_inst{inst.number}"
            mqtt_topic = f"{self.discovery_prefix}/binary_sensor/{ctrl.name}/{mqtt_target}"
            object_id = f"{ctrl.name}_{mqtt_target}"
            unique_id = f"{ctrl.name}_{mqtt_target}_{sensor.serial}"
            config_dict = {
                "component": "binary_sensor",
                "device_class": "motion",
                "name": sensor.instance_label,
                "object_id": object_id,
                "unique_id": unique_id,
                "payload_off": "OFF",
                "payload_on": "ON",
                "state_topic": f"{mqtt_topic}/state",
                "availability_topic": f"{ctrl.name}/availability",
                "json_attributes_topic": f"{mqtt_topic}/attributes",
                "device": {
                    "manufacturer": "zencontrol",
                    "identifiers": f"zencontrol-{ctrl.name}",
                    "name": ctrl.label,
                },
                "retain": False,
                "expire_after": 120,
            }
            self._publish_config(mqtt_topic, config_dict)

    def setup_system_variables(self) -> None:
        """Initialize system variables in config.yaml for Home Assistant auto-discovery."""
        
        # On first run, prep system variables with client_data
        if not self.system_variables:
            for sv in self.sv_config:
                ctrl = sv['controller']
                attr = sv['attributes']
                zsv = ZenSystemVariable(protocol=self.zen, controller=ctrl, id=sv['id'])
                mqtt_target = f"sv{sv['id']}"
                zsv.client_data = {
                    "platform": sv['platform'],
                    "attributes": attr,
                    "mqtt_target": mqtt_target,
                    "mqtt_topic": f"{self.discovery_prefix}/sensor/{ctrl.name}/{mqtt_target}",
                    "object_id": sv['object_id'],
                    "unique_id": f"{ctrl.name}_{sv['object_id']}",
                }
                self.system_variables.append(zsv)

        for zsv in self.system_variables:
            attr: dict = zsv.client_data['attributes']
            ctrl: ZenController = zsv.controller
            match zsv.client_data['platform']:
                case "sensor":
                    config_platform = {
                        "component": "sensor",
                        "object_id": zsv.client_data['object_id'],
                        "unique_id": zsv.client_data['unique_id'],
                        "state_topic": f"{zsv.client_data['mqtt_topic']}/state",
                        "availability_topic": f"{ctrl.name}/availability",
                        "device": {
                            "manufacturer": "zencontrol",
                            "identifiers": f"zencontrol-{ctrl.name}",
                            "name": ctrl.label,
                        },
                        "retain": False
                    }
                    self._publish_config(zsv.client_data['mqtt_topic'], attr | config_platform)
    
    # ================================
    # PUBLISH TO MQTT
    # ================================
    
    def _publish_config(self, topic: str, config: dict, retain: bool = True) -> None:
        json_config = json.dumps(config)
        self.mqttc.publish(f"{topic}/config", json_config, retain=retain)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/config: " + Style.DIM + f"{json_config}" + Style.RESET_ALL)
    
    def _publish_state(self, topic: str, state: str|dict, retain: bool = False) -> None:
        if isinstance(state, dict): state = json.dumps(state)
        self.mqttc.publish(f"{topic}/state", state, retain=retain)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {topic}/state: " + Style.DIM + f"{state}" + Style.RESET_ALL)

    def _send_state_mqtt(self, address: ZenAddress, component: str, state_dict: dict[str, Any]) -> None:
        ctrl = address.controller
        target = f"ecg{address.number}"
        mqtt_topic = f"{self.discovery_prefix}/{component}/{ctrl.name}/{target}"
        self._publish_state(mqtt_topic, state_dict)
    
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

            # Wait for retained config to be deleted
            time.sleep(0.2)

            self.setup_started = True
            self.setup_lights()
            self.setup_motion_sensors()
            self.setup_system_variables()
            
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