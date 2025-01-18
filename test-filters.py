from zen import ZenProtocol, ZenController, ZenAddress, ZenAddressType, ZenEventMask
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)

ecg1 = ZenAddress(ctrl, ZenAddressType.ECG, 0x01)
ecd1 = ZenAddress(ctrl, ZenAddressType.ECD, 0x01)
broadcast = ZenAddress.broadcast(ctrl)

add_filter = tpi.dali_add_tpi_event_filter(ecg1)
print(f"Added filter: {add_filter}")
add_filter = tpi.dali_add_tpi_event_filter(ecd1)
print(f"Added filter: {add_filter}")
get_filters = tpi.query_dali_tpi_event_filters(broadcast)
print(f"Filters: {get_filters}")
rem_filter = tpi.dali_clear_tpi_event_filter(ecg1)
print(f"Removed filter: {rem_filter}")
rem_filter = tpi.dali_clear_tpi_event_filter(ecd1)
print(f"Removed filter: {rem_filter}")
get_filters = tpi.query_dali_tpi_event_filters(broadcast)
print(f"Filters: {get_filters}")