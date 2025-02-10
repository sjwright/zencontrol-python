from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("test-config.yaml"))
tpi = ZenProtocol(narration=True)
ctrl = ZenController(protocol=tpi, **config.get('zencontrol')[0])
tpi.set_controllers([ctrl])


# Direct access
print("Direct access")
var1 = tpi.query_system_variable(ctrl, 1)
print(f"  sys var 1: {var1}")
var2 = tpi.query_system_variable(ctrl, 2)
print(f"  sys var 2: {var2}")
var3 = tpi.query_system_variable(ctrl, 3)
print(f"  sys var 3: {var3}")
var4 = tpi.query_system_variable(ctrl, 4)
print(f"  sys var 4: {var4}")

# Direct set
print("Direct set")
tpi.set_system_variable(ctrl, 4, 420)
var4 = tpi.query_system_variable(ctrl, 4)
print(f"  sys var 4: {var4}")
