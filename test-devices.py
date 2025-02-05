from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("test-config.yaml"))
tpi = ZenProtocol(narration=False)
ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
tpi.set_controllers([ctrl])


addresses = tpi.query_dali_addresses_with_instances(ctrl, 0)
print(f"Addresses with instances:")


for address in addresses:
    print(f"  {address.number}")

    label = tpi.query_dali_device_label(address, generic_if_none=True)
    print(f"    label: {label}")

    operating_mode = tpi.query_operating_mode_by_address(address)
    print(f"    operating mode: {operating_mode}")

    serial = tpi.query_dali_serial(address)
    print(f"    serial: {serial}")

    ean = tpi.query_dali_ean(address)
    print(f"    ean: {ean}")

    fitting = tpi.query_dali_fitting_number(address)
    print(f"    fitting: {fitting}")
