# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol, written in Python. This library has been written with three levels of abstraction:

- zencontrol.io: Implementation of the raw TPI Advanced UDP packet specification;
- zencontrol.api: Implementation of most TPI Advanced API commands and events;
- zencontrol.interface: An opinionated abstraction layer suitable for integration into smart building control software. It provides methods, objects, and callbacks for managing lights, groups, profiles, buttons, motion sensors, and system variables.

This library has now undergone validation in multiple environments. There is an extensive test suite, some of which uses
[zencontrol-simulator](https://github.com/sjwright/zencontrol-simulator), a nearly feature-complete simulator of zencontrol hardware.
A practical demonstration is [zencontrol-tpi](https://github.com/sjwright/zencontrol-tpi), a comprehensive Home Assistant integration.

## Requirements

* Python 3.11 (or later)
* Controller firmware 2.2.130 or later is strongly recommended (minimum 2.2.11 required)

## Install

This library is available on PyPI.

## Testing

Integration tests start [zencontrol-simulator](https://github.com/sjwright/zencontrol-simulator) on an ephemeral local port and exercise a real UDP TPI protocol path. Either install the simulator, or check it out as a sibling directory (`../zencontrol-simulator`); tests will pick it up automatically. Note that PyYAML is a simulator dependency.

```bash
pip install -e ".[dev]"
pip install PyYAML
# optional if not using a sibling checkout:
# pip install -e ../zencontrol-simulator
pytest -m simulator
pytest -m "not simulator"
# or run everything:
pytest
```

## Limitations

* RGB+ and XY colour commands are not tested (I don't have any compatible lights)
* Numerical (absolute) instances are not tested (I don't have any such ECDs)

## Out of scope

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they aren't useful)

## TPI Advanced wishlist

* Command to return a controller's MAC address used for multicast packets _(There are other ways to get or infer the MAC access, but they're unreliable.)_
* Command to list active system variables _(As a workaround, you can query every number for its label. This assumes no system variables of interest are unlabelled.)_
* Command to read an ambient light sensor's lux value. _(As a workaround, you can target a light sensor to a system variable. Not elegant but it works.)_
* Event notification for ambient light sensor lux values. _(Same workaround as above.)_

## License

[MIT](LICENSE)

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
