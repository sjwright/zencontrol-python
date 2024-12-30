from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])
tpi.debug = True

# tpi.dali_scene(0x03, 2)

# 
# print(f"    colour: {colour}")

cgtype = tpi.query_dali_colour_features(ctrl, 0x03)
print(f"    colour features: {cgtype}")

#exit()

tpi.dali_illuminate(ctrl, group=1, kelvin=7000, level=150)

colour = tpi.query_dali_colour(ctrl, 3)
print(f"    colour: {colour}")
