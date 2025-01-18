from zen import ZenProtocol, ZenController, ZenAddress, ZenAddressType
import yaml
import time

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)


gear3 = ZenAddress(ctrl, ZenAddressType.ECG, 3)
group1 = ZenAddress(ctrl, ZenAddressType.GROUP, 1)
bcast = ZenAddress.broadcast(ctrl)



print("__ switch off address 3 __")
tpi.dali_off(gear3)
time.sleep(1)

print("__ querying address 3 __")
tpi.dali_query_control_gear_status(gear3)

print("__ querying address 65 __")
tpi.dali_query_control_gear_status(group1)

print("__ querying address 80 __")
tpi.dali_query_control_gear_status(bcast)

print("__ querying address 255 __")
tpi._send_basic(ctrl, tpi.CMD["DALI_QUERY_CONTROL_GEAR_STATUS"], 255)




print("__ switch on address 3 __")
tpi.dali_on_step_up(gear3)
time.sleep(1)

print("__ querying address 3 __")
tpi.dali_query_control_gear_status(gear3)

print("__ querying address 65 __")
tpi.dali_query_control_gear_status(group1)

print("__ querying address 80 __")
tpi.dali_query_control_gear_status(bcast)

print("__ querying address 255 __")
tpi._send_basic(ctrl, tpi.CMD["DALI_QUERY_CONTROL_GEAR_STATUS"], 255)