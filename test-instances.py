from zen import ZenProtocol, ZenController, ZenInstance
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=False)


addresses = tpi.query_dali_addresses_with_instances(ctrl, 0)
print(f"Addresses with instances: {addresses}")


for address in addresses:
    print(f"  {address}")

    label = tpi.query_dali_device_label(ctrl, ecd=address, generic_if_none=True)
    print(f"    label: {label}")

    operating_mode = tpi.query_operating_mode_by_address(ctrl, ecd=address)
    print(f"    operating mode: {operating_mode}")

    instances = tpi.query_instances_by_address(ctrl, ecd=address)
    # print(f"    instances: {instances}")

    for instance in instances:
        
        instance_label = tpi.query_dali_instance_label(ctrl, instance=instance, ecd=address, generic_if_none=True, device_label=label)
        print(f"      {instance} - {instance_label}")

        groups = tpi.query_instance_groups(ctrl, instance=instance, ecd=address)
        print(f"      groups: {groups}")

        fitting = tpi.query_dali_instance_fitting_number(ctrl, instance=instance, ecd=address)
        print(f"      fitting: {fitting}")

        occupancy_timers = tpi.query_occupancy_instance_timers(ctrl, instance=instance, ecd=address)
        print(f"      occupancy timers: {occupancy_timers}")

        last_known_led_state = tpi.query_last_known_dali_button_led_state(ctrl, instance=instance, ecd=address)
        print(f"      last known led state: {last_known_led_state}")

