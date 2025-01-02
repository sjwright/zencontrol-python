import time
import json
import yaml
import re
from typing import Optional, Dict, List, Any, Callable
from zen import ZenProtocol, ZenController, ZenInstance
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
        self.config: Dict[str, Any]
        self.discovery_prefix: str
        self.ctrl: ZenController
        self.tpi: ZenProtocol
        self.mqttc: mqtt.Client
        
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
                
            self.discovery_prefix = self.config['homeassistant']['discovery_prefix']
        except Exception as e:
            self.logger.error(f"Failed to load config file: {e}")
            raise
        
        # Initialize controller
        try:
            self.ctrl = ZenController(**self.config['zencontrol'][0])
            self.tpi = ZenProtocol(controllers=[self.ctrl], logger=self.logger, narration=True)
        except Exception as e:
            self.logger.error(f"Failed to initialize ZenProtocol: {e}")
            raise
        
        # Initialize MQTT
        try:
            self.mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.mqttc.on_connect = self._on_mqtt_connect
            self.mqttc.on_message = self._on_mqtt_message
            self.mqttc.on_disconnect = self._on_mqtt_disconnect
            
            mqtt_config = self.config["mqtt"]
            self.mqttc.will_set(topic=f"{self.ctrl.name}/availability", payload="offline", retain=True)
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

    def _on_mqtt_connect(self, client: mqtt.Client, userdata: Any, flags: Dict, 
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
            client.subscribe(f"test/#")
            client.subscribe(f"{self.discovery_prefix}/light/{self.ctrl.name}/#")
            client.publish(topic=f"{self.ctrl.name}/availability", payload="online", retain=True)
        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {reason_code}")

    # ================================
    # RECEIVED FROM HOME ASSISTANT
    # ================================

    def _on_mqtt_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """Handle incoming MQTT messages with improved error handling."""
        try:
            if not msg.topic.startswith(f"{self.discovery_prefix}/"):
                return # Not for us
                
            if f"/{self.ctrl.name}/" not in msg.topic:
                return # we don't currently support multiple controllers
                
            match = re.search(r'ecg(\d+)\/.?set', msg.topic)
            if not match:
                return # Not a set command
                
            gear = int(match.group(1))
            payload = json.loads(msg.payload)
        
            # Debug
            print(Fore.YELLOW + f"MQTT received - {msg.topic}: " + Style.DIM + f"{msg.payload.decode('UTF-8')}" + Style.RESET_ALL)
            
            self._process_mqtt_payload(gear, payload)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON payload: {e}")
        except Exception as e:
            self.logger.error(f"Error processing MQTT message: {e}")

    def _process_mqtt_payload(self, gear: int, payload: Dict[str, Any]) -> None:
        """Process parsed MQTT payload."""
        if "brightness" in payload:
            brightness = int(payload["brightness"])
            arc_level = self.brightness_to_arc(brightness)
            self.logger.info(f"Setting gear {gear} brightness to arc {arc_level} (linear {brightness}) ")
            self.tpi.dali_illuminate(
                controller=self.ctrl,
                level=arc_level,
                gear=gear
            )
        
        if "color_temp" in payload:
            mireds = int(payload["color_temp"])
            kelvin = self.mireds_to_kelvin(mireds)
            self.logger.info(f"Setting gear {gear} temperature to {kelvin}K (mireds {mireds})")
            self.tpi.dali_illuminate(controller=self.ctrl, kelvin=kelvin, gear=gear)
            self.send_light_temp_to_homeassistant(target=gear, kelvin=kelvin)
        
        # Respond to on/off switching in Home Assistant
        if "state" in payload and "brightness" not in payload and "color_temp" not in payload:
            state = payload["state"]
            if state == "OFF":
                self.logger.info(f"Turning off gear {gear}")
                self.tpi.dali_off(controller=self.ctrl, gear=gear)
            elif state == "ON":
                self.logger.info(f"Turning on gear {gear}")
                self.tpi.dali_go_to_last_active_level(controller=self.ctrl, gear=gear)
                
    # ================================
    # SEND TO HOME ASSISTANT
    # ================================

    def send_light_level_to_homeassistant(self, target: int, arc_level: Optional[int] = None) -> None:
        """Send light brightness level updates to Home Assistant.

        Args:
            target: The gear/address number of the light
            arc_level: Optional brightness level (1-255). If None, queries current level
        """
        if arc_level is None:
            arc_level = self.tpi.dali_query_level(controller=self.ctrl, gear=target)
        if arc_level == 0:
            self._send_state_mqtt(target, {
                "state": "OFF"
            })
        else:
            brightness = self.arc_to_brightness(arc_level)
            self._send_state_mqtt(target, {
                "color_mode": "color_temp",
                "brightness": brightness,
                "state": "ON"
            })

    def send_light_temp_to_homeassistant(self, target: int, 
                                        kelvin: Optional[int] = None, 
                                        mireds: Optional[int] = None) -> None:
        """Send color temperature updates to Home Assistant.

        Args:
            target: The gear/address number of the light
            kelvin: Optional color temperature in Kelvin
            mireds: Optional color temperature in Mireds
            
        Raises:
            ValueError: If neither kelvin nor mireds is provided
        """
        if kelvin is None and mireds is None:
            raise ValueError("Cannot get temperature value dynamically")
        if mireds is None:
            mireds = self.kelvin_to_mireds(kelvin)
        if kelvin is None:
            kelvin = self.mireds_to_kelvin(mireds)
        self._send_state_mqtt(target, {
            "color_mode": "color_temp",
            "color": mireds,
            "state": "ON",
        })

    def _send_state_mqtt(self, target: int, state_dict: Dict[str, Any]) -> None:
        mqtt_topic = f"{self.discovery_prefix}/light/{self.ctrl.name}/ecg{target}/state"
        state_json = json.dumps(state_dict)
        self.mqttc.publish(
            topic=mqtt_topic, 
            payload=state_json, 
            retain=False)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {mqtt_topic}: " + Style.DIM + f"{state_json}" + Style.RESET_ALL)

    # ================================
    # RECEIVED FROM ZEN
    # ================================

    def _level_change_event(self, controller: ZenController, gear: int, arc_level: int, event_data: Dict = {}) -> None:
        print(f"Zen to HA: gear {gear} arc_level {arc_level}")
        self.send_light_level_to_homeassistant(gear, arc_level)

    def _color_temp_change_event(self, controller: ZenController, address: int, kelvin: int, event_data: Dict = {}) -> None:
        print(f"Zen to HA: gear {address} temperature {kelvin}K")
        self.send_light_temp_to_homeassistant(address, kelvin)

    # ================================
    # SETUP AUTODISCOVERY
    # ================================

    def setup_lights(self) -> None:
        """Initialize all lights found on the DALI bus for Home Assistant auto-discovery."""
        gears = self.tpi.query_control_gear_dali_addresses(controller=self.ctrl)
        for gear in gears:
            self.setup_light(gear)

    def setup_light(self, gear: int) -> None:
        """Configure a single light for Home Assistant auto-discovery."""
        label = self.tpi.query_dali_device_label(controller=self.ctrl, gear=gear)
        cgtype = self.tpi.query_dali_colour_features(controller=self.ctrl, gear=gear)
        colour_temp_limits = self.tpi.query_dali_colour_temp_limits(controller=self.ctrl, gear=gear)
        serial = self.tpi.query_dali_serial(controller=self.ctrl, gear=gear)
        # fitting = self.tpi.query_dali_fitting_number(controller=self.ctrl, gear=gear)
        object_id = f"{self.ctrl.name}_ecg{gear}"
        unique_id = f"{self.ctrl.name}_ecg{gear}_{serial}"
        mqtt_topic = f"{self.discovery_prefix}/light/{self.ctrl.name}/ecg{gear}"
        
        config_dict = {
            "component": "light",
            "name": label,
            "object_id": object_id,
            "unique_id": unique_id,
            "schema": "json",
            "payload_off": "OFF",
            "payload_on": "ON",
            "command_topic": f"{mqtt_topic}/set",
            "state_topic": f"{mqtt_topic}/state",
            "availability_topic": f"{self.ctrl.name}/availability",
            "json_attributes_topic": f"{mqtt_topic}/attributes",
            "device": {
                "manufacturer": "zencontrol",
                "identifiers": f"zencontrol-{self.ctrl.name}",
                "name": self.ctrl.label,
            },
            "effect": False,
            "retain": False,
        }
        if cgtype.get("supports_tunable", False) is True:
            config_dict["brightness"] = True
            config_dict["supported_color_modes"] = ["color_temp"]
            config_dict["min_mireds"] = self.kelvin_to_mireds(colour_temp_limits.get("soft_coolest", Constants.DEFAULT_COOLEST_TEMP))
            config_dict["max_mireds"] = self.kelvin_to_mireds(colour_temp_limits.get("soft_warmest", Constants.DEFAULT_WARMEST_TEMP))
        
        elif cgtype.get("rgbwaf_channels", 0) == Constants.RGB_CHANNELS:
            config_dict["brightness"] = True
            config_dict["supported_color_modes"] = ["rgb"]
        
        elif cgtype.get("rgbwaf_channels", 0) == Constants.RGBW_CHANNELS:
            config_dict["brightness"] = True
            config_dict["supported_color_modes"] = ["rgbw"]
        
        elif cgtype.get("rgbwaf_channels", 0) == Constants.RGBWW_CHANNELS:
            config_dict["brightness"] = True
            config_dict["supported_color_modes"] = ["rgbww"]
        
        else:
            config_dict["brightness"] = True
            config_dict["supported_color_modes"] = ["rgb"]
        
        mqtt_topic = f"{mqtt_topic}/config"
        config_json = json.dumps(config_dict)
        self.mqttc.publish(
            topic=mqtt_topic, 
            payload=config_json, 
            retain=True)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {mqtt_topic}: " + Style.DIM + f"{config_json}" + Style.RESET_ALL)

    def setup_instances(self) -> None:
        """Initialize all instances found on the DALI bus for Home Assistant auto-discovery."""
        addresses = self.tpi.query_dali_addresses_with_instances(controller=self.ctrl, start_address=0)
        for address in addresses:
            device_label = self.tpi.query_dali_device_label(controller=self.ctrl, ecd=address, generic_if_none=True)
            instances = self.tpi.query_instances_by_address(controller=self.ctrl, ecd=address)
            for instance in instances:
                # print(f"attempting to setup address {address} device {device_label} instance {instance}")
                self.setup_instance(instance, device_label)

    def setup_instance(self, instance: ZenInstance, device_label: str) -> None:
        """Configure a single instance for Home Assistant auto-discovery."""
        label = self.tpi.query_dali_instance_label(self.ctrl, instance=instance, ecd=instance.address, generic_if_none=True, device_label=device_label)
        serial = self.tpi.query_dali_serial(controller=self.ctrl, ecd=instance.address)
        # fitting = self.tpi.query_dali_fitting_number(controller=self.ctrl, gear=gear)
        object_id = f"{self.ctrl.name}_ecd{instance.address}_inst{instance.number}"
        unique_id = f"{self.ctrl.name}_ecd{instance.address}_{serial}_inst{instance.number}"
        match instance.type:
            case 0x01: # Push button - generates short/long press events
                component = "event"
            case 0x02: # Absolute input (slider/dial) - generates integer values
                component = "event"
            case 0x03: # Occupancy/motion sensor - generates occupied/unoccupied events
                component = "binary_sensor"
            case 0x04: # Light sensor - events not currently forwarded
                return
            case 0x06: # General sensor (water flow, power etc) - events not currently forwarded
                return
            case _:
                return
        mqtt_topic = f"{self.discovery_prefix}/{component}/{self.ctrl.name}/ecd{instance.address}_inst{instance.number}"
        config_dict = {
            "component": component,
            "name": label,
            "object_id": object_id,
            "unique_id": unique_id,
            "schema": "json",
            "payload_off": "OFF",
            "payload_on": "ON",
            "command_topic": f"{mqtt_topic}/set",
            "state_topic": f"{mqtt_topic}/state",
            "availability_topic": f"{self.ctrl.name}/availability",
            "json_attributes_topic": f"{mqtt_topic}/attributes",
            "device": {
                "manufacturer": "zencontrol",
                "identifiers": f"zencontrol-{self.ctrl.name}",
                "name": self.ctrl.label,
            },
            "retain": False,
        }
        match instance.type:
            case 0x03: # Occupancy/motion sensor - generates occupied/unoccupied events
                config_dict["device_class"] = "motion"
                config_dict["expire_after"] = 120
        mqtt_topic = f"{mqtt_topic}/config"
        config_json = json.dumps(config_dict)
        self.mqttc.publish(
            topic=mqtt_topic, 
            payload=config_json, 
            retain=True)
        print(Fore.LIGHTRED_EX + f"MQTT sent - {mqtt_topic}: " + Style.DIM + f"{config_json}" + Style.RESET_ALL)
        
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
            
            self.logger.info("Waiting for DALI bus to be ready...")
            while not self.tpi.query_is_dali_ready(controller=self.ctrl):
                time.sleep(Constants.STARTUP_POLL_DELAY)

            self.logger.info("Waiting for controller to finish booting...")
            while not self.tpi.query_controller_startup_complete(controller=self.ctrl):
                time.sleep(Constants.STARTUP_POLL_DELAY)
                
            """Start event monitoring and MQTT services."""
            self.tpi.start_event_monitoring(
                level_change_callback=self._level_change_event,
                colour_changed_callback=self._color_temp_change_event
            )
            self.mqttc.loop_start()
            time.sleep(0.1)
            self.setup_lights()
            self.setup_instances()
            
            # Use signal handling for graceful shutdown
            import signal
            signal.signal(signal.SIGINT, lambda s, f: self.stop())
            signal.signal(signal.SIGTERM, lambda s, f: self.stop())
            
            while True:
                time.sleep(1)
                
        except Exception as e:
            self.logger.error(f"Fatal error: {e}")
            raise
        finally:
            self.stop()

    def stop(self) -> None:
        """Clean shutdown of the bridge"""
        self.tpi.stop_event_monitoring()
        self.mqttc.loop_stop()
        self.mqttc.disconnect()

# Usage
if __name__ == "__main__":
    bridge = ZenMQTTBridge()
    bridge.run()