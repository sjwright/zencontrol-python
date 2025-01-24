from zen import ZenProtocol, ZenController, ZenSystemVariable
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl], narration=True)



# Direct access
print("Direct access")
var1 = tpi.query_system_variable(ctrl, 1)
var2 = tpi.query_system_variable(ctrl, 2)
var3 = tpi.query_system_variable(ctrl, 3)
var4 = tpi.query_system_variable(ctrl, 4)
print(f"sys var 1: {var1}")
print(f"sys var 2: {var2}")
print(f"sys var 3: {var3}")
print(f"sys var 4: {var4}")

# Direct set
print("Direct set")
tpi.set_system_variable(ctrl, 4, 420)
var4 = tpi.query_system_variable(ctrl, 4)
print(f"sys var 4: {var4}")

# Using ZenSystemVariable
print("Using ZenSystemVariable")
var1 = ZenSystemVariable(tpi, ctrl, 1)
var2 = ZenSystemVariable(tpi, ctrl, 2)
var3 = ZenSystemVariable(tpi, ctrl, 3)
var4 = ZenSystemVariable(tpi, ctrl, 4)
print(f"sys var 1: {var1.value}")
print(f"sys var 2: {var2.value}")
print(f"sys var 3: {var3.value}")
print(f"sys var 4: {var4.value}")
var4.value = 421
print(f"sys var 4: {var4.value}")
