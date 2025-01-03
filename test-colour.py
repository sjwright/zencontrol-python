from zen import ZenProtocol, ZenController, ZenAddress, AddressType, ZenColourTC
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=False)





address = ZenAddress(ctrl, AddressType.ECG, 0x03)
colour = ZenColourTC(level=200, kelvin=7700)
tpi.dali_colour(address, colour)



exit()

addresses = tpi.query_control_gear_dali_addresses(ctrl)
print(f"Control gears")    

for address in addresses:
    print(f"  {address.number}")

    level = tpi.dali_query_level(address)
    print(f"    current level: {level}")

    colour = tpi.query_dali_colour(address)
    print(f"    colour: {colour}")
    
    cgtype = tpi.query_dali_colour_features(address)
    print(f"    colour features: {cgtype}")

    colour_temp_limits = tpi.query_dali_colour_temp_limits(address)
    print(f"    colour temp limits: {colour_temp_limits}")