from zen import ZenProtocol, ZenController, ZenAddress, ZenInstance
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])


current_profile = tpi.query_current_profile_number(ctrl)
print(f"Current profile: {current_profile}")

profiles = tpi.query_profile_numbers(ctrl)
print(f"Profiles")

for profile in profiles:
    print(f"  {profile}")
    label = tpi.query_profile_label(ctrl, profile)
    print(f"    label: {label}")    

#tpi.change_profile_number(1)
#tpi.return_to_scheduled_profile()
