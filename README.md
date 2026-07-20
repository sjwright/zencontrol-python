# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol, written in Python. This library has been written with three levels of abstraction:

- zencontrol.io: Implementation of the raw TPI Advanced UDP packet specification;
- zencontrol.api: Implementation of most TPI Advanced API commands and events;
- zencontrol.interface: An opinionated abstraction layer suitable for integration into smart building control software. It provides methods, objects, and callbacks for managing lights, groups, profiles, buttons, motion sensors, and system variables. This code is still undergoing significant refinement.

Built on top of this is an example MQTT bridge for Home Assistant. See [examples/mqtt_bridge.md](examples/mqtt_bridge.md).

## Requirements

* Python 3.11 (or later)
* Controller firmware 2.2.11 (or later)

## Install

Refer to zencontrol-tpi project for now.
For integrators, the library is also published on PyPI.

## Limitations

Implemented but untested:
  
* Dealing with multiple controllers (I only have one controller)
* RGB+ and XY colour commands (I don't have any compatible lights)
* Numerical (absolute) instances (I don't have any such ECDs)
* Event filtering (I haven't tested it)

Not implemented:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they aren't useful)
## TPI Advanced wishlist

* Command to return a controller's MAC address used for multicast packets _(There are other ways to get or infer the MAC access, but they're unreliable.)_
* Command to list active system variables _(As a workaround, you can query every number for its label. This assumes no system variables of interest are unlabelled.)_
* Command to read an ambient light sensor's lux value. _(As a workaround, you can target a light sensor to a system variable. Not elegant but it works.)_
* Event notification for ambient light sensor lux values. _(Same workaround as above.)_

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
