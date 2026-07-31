# zencontrol-python

A Python implementation of the **Zencontrol TPI Advanced** protocol, organised in three layers:

- zencontrol.io: raw TPI Advanced UDP packet framing;
- zencontrol.api: control surface for TPI Advanced API commands and events;
- zencontrol.interface: an opinionated world model, suitable for smart-building integrations. It provides a fully resolved set of methods, objects, and callbacks for lights, groups, buttons, sensors and everything else in your zencontrol universe.

[**Documentation**](docs/overview.md)

In addition to its own test suite, this library is exercised heavily by
[zencontrol-simulator](https://github.com/sjwright/zencontrol-simulator), a nearly feature-complete simulator of zencontrol hardware.
As part of that suite, the simulator imports and drives this library to a substantial extent.

As a practical demonstration of the library in production use, [zencontrol-homeassistant](https://github.com/sjwright/zencontrol-homeassistant) exposes the full capability of this library. Home Assistant is an open source smart building system ostensibly designed for residential homes, but is seeing increasing use in office environments too, because nothing else can match it for the sheer breadth of compatibility.

## Features

Beyond basic lighting control, this library supports:

* **Broad command surface** — inhibit, custom fade, step/up/down helpers, colour scene membership queries, EAN/serial, and most related TPI Advanced commands
* **Object-based entity model** — optional. Expresses lights, groups, profiles, buttons, motion sensors, absolute inputs, and system variables as rich objects with interview/discovery helpers
* **UDP transport resilience** — request retries and queue-failure backoff
* **Event keepalive** — periodic emit-state ping; re-enables TPI configuration and event emission if a controller reboots while the listener stays up
* **Multicast controller discovery** — find controllers on the LAN without a preconfigured host
* **Button events** — discovery of control-device button instances, plus press and long-press event callbacks
* **Absolute inputs** — discovery of numerical ECD instances (dials/sliders) with 16-bit value-change event callbacks
* **Event filtering** — configure which TPI events the controller emits
* **System variables** — labelled SV discovery, read/write, and change events
* **Profiles** — query, change, and return to the scheduled profile
* **Simulator-backed tests** — protocol path exercised against [zencontrol-simulator](https://github.com/sjwright/zencontrol-simulator)

## Known limitations

* RGB+ and XY colour commands have not been tested with hardware
* Numerical (absolute) instances have not been tested with hardware
* Fans and blinds have not been tested with field-deployed hardware

## Out of scope

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for these, so I couldn't test them even if I wanted to — but the scaffolding is there if anyone wishes to add support)

## Requirements

* Python 3.14 (or later)
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

## TPI Advanced wishlist

* Command to return a controller's MAC address used for multicast packets _(There are other ways to get or infer the MAC address, but they're unreliable.)_
* Command to list active system variables _(As a workaround, you can query every number for its label. This assumes no system variables of interest are unlabelled.)_
* Command to read an ambient light sensor's lux value. _(As a workaround, you can target a light sensor to a system variable. Not elegant, but it works.)_
* Event notification for ambient light sensor lux values. _(Same workaround as above.)_

## License

[MIT](LICENSE)

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
