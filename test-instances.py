from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance
import yaml

config = yaml.safe_load(open("test-config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=False)


addresses = tpi.query_dali_addresses_with_instances(ctrl, 0)
print(f"Addresses with instances:")


for address in addresses:
    print(f"  {address.number}")

    label = tpi.query_dali_device_label(address)
    print(f"    label: {label}")

    operating_mode = tpi.query_operating_mode_by_address(address)
    print(f"    operating mode: {operating_mode}")

    instances = tpi.query_instances_by_address(address)
    # print(f"    instances: {instances}")

    for instance in instances:
        
        instance_label = tpi.query_dali_instance_label(instance)
        print(f"      {instance.number} - {instance_label}")

        groups = tpi.query_instance_groups(instance)
        print(f"      groups: {groups}")

        fitting = tpi.query_dali_instance_fitting_number(instance)
        print(f"      fitting: {fitting}")

        occupancy_timers = tpi.query_occupancy_instance_timers(instance)
        print(f"      occupancy timers: {occupancy_timers}")

        last_known_led_state = tpi.query_last_known_dali_button_led_state(instance)
        print(f"      last known led state: {last_known_led_state}")

