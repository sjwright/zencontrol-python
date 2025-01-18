from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance, ZenInstanceType
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)


motion_sensors = tpi.get_motion_sensors(ctrl)

for motion_sensor in motion_sensors:
    print(f"  - {motion_sensor}")
