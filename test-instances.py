from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])


addresses = tpi.query_dali_addresses_with_instances(ctrl, 0)
print(f"Addresses with instances: {addresses}")


for address in addresses:
    print(f"  {address}")

    operating_mode = tpi.query_operating_mode_by_address(ctrl, address)
    print(f"    operating mode: {operating_mode}")

    instances = tpi.query_instances_by_address(ctrl, address)
    print(f"    instances: {instances}")

    for instance in instances:

        label = tpi.query_dali_instance_label(ctrl, instance=instance[0], ecd=address)
        print(f"    label: {label}")

        groups = tpi.query_instance_groups(ctrl, instance=instance[0], ecd=address)
        print(f"      groups: {groups}")

        fitting = tpi.query_dali_instance_fitting_number(ctrl, instance=instance[0], ecd=address)
        print(f"      fitting: {fitting}")

