from zen import ZenProtocol, ZenController, ZenAddress, AddressType
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)

address = ZenAddress(controller=ctrl, type=AddressType.ECG, number=0)

status = tpi.dali_query_control_gear_status(address)
print(status)