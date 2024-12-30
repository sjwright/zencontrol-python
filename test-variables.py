from zen import ZenProtocol, ZenController
import yaml

config = yaml.safe_load(open("config.yaml"))
ctrl = ZenController(**config.get('zencontrol')[0])
tpi = ZenProtocol(controllers=[ctrl])


tpi.set_system_variable(ctrl, 4, 420)


var1 = tpi.query_system_variable(ctrl, 1)
var2 = tpi.query_system_variable(ctrl, 2)
var3 = tpi.query_system_variable(ctrl, 3)
var4 = tpi.query_system_variable(ctrl, 4)
print(f"sys var 1: {var1}")
print(f"sys var 2: {var2}")
print(f"sys var 3: {var3}")
print(f"sys var 4: {var4}") 

