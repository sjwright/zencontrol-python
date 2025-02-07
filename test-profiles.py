from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance
import yaml

config = yaml.safe_load(open("test-config.yaml"))
tpi = ZenProtocol(narration=False)
ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
tpi.set_controllers([ctrl])


current_profile = tpi.query_current_profile_number(ctrl)
print(f"Current profile: {current_profile}")


profile_info, profiles = tpi.query_profile_information(ctrl)
for info in profile_info:
    print(f"  {info} = {profile_info[info]}")

for profile in profiles:
    label = tpi.query_profile_label(ctrl, profile)
    print(f"  {profile} = {label} {profiles[profile]}")


