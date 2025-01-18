from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)


groups = tpi.get_groups(ctrl)

for group in groups:
    print(f"  - {group}")
