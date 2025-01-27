from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("test-config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)


version = tpi.query_controller_version_number(ctrl)
print(f"ZenController version: {version}")    

controller_label = tpi.query_controller_label(ctrl)
print(f"ZenController label: {controller_label}")

controller_fitting_number = tpi.query_controller_fitting_number(ctrl)
print(f"ZenController fitting number: {controller_fitting_number}")

startup_complete = tpi.query_controller_startup_complete(ctrl)
print(f"ZenController startup complete: {startup_complete}")

dali_ready = tpi.query_is_dali_ready(ctrl)
print(f"DALI bus is ready: {dali_ready}")

