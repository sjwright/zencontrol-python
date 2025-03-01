# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol, written in Python. The TPI Advanced protocol is implemented as **ZenProtocol** in *zen.py*, featuring class methods for most API commands and callbacks for event packets (multicast or unicast). This code is relatively mature and stable.

An opinionated abstraction layer is implemented as **ZenInterface** in *zen_interface.py*, built on top of ZenProtocol. It abstracts the raw protocol into a cohesive interface suitable for smart building control software. It currently provides methods, objects, and callbacks for managing lights, groups, profiles, buttons, motion sensors, and Zen system variables. This code is feature complete but still undergoing significant refinement.

Built on top of ZenInterface is **mqtt.py**, a bridge to Home Assistant via MQTT. It reads settings from *config.json* and (currently) spams your console with lots of debug messages. To run this, ensure you have Python 3.11 (or later) installed, along with the following Python packages: `paho-mqtt`, `yaml`, and `colorama`. To run, modify **config.yaml** as needed for your environment, then execute **mqtt.py**.

## Requirements

* Python 3.11 (or later)
* Controller firmware 2.1.35 (or later)

## Quick start

The following are the minimum steps necessary to run the MQTT bridge on a Raspberry Pi running the lastest release of Raspberry Pi OS.

```
# Update/Install packages:
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3-yaml python3-colorama python3-paho-mqtt

# Download this code:
cd ~/Documents
git clone https://github.com/sjwright/zencontrol-python
cd zencontrol-python

# Edit config.yaml to suit your environment
nano config.yaml

# Run the MQTT bridge
python3 mqtt.py
```

Be aware that other distributions may ship with an older version of python and could require non-trivial steps to install a newer version. You can check your python version by running `python3 -V`

## Limitations

Implemented but untested:
  
* Dealing with multiple controllers
* RGB+ and XY colour commands (I don't have any compatible lights)
* Numerical (absolute) instances (I don't have any such devices)
* Event filtering (I haven't tested it)

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they don't serve any purpose)

## TPI Advanced errata

The following TPI Advanced commands/events are incomplete:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it only returns correct values under some instances. If the temperature is changed by way of scene recall, this query returns wrong information. _(A fix is anticipated soon)_
* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it only does so under some circumstances. If the temperature is changed by way of scene recall, the event does not fire. _(A fix is anticipated soon)_

## TPI Advanced wishlist

High priority:

* Command to fetch colour temperatures as entered in the "colour scene assignment" section of the Zen cloud grid view. _(This has been added in firmware version 2.1.38)_

Low priority:

* Command to return a controller's MAC address used for multicast packets _(There are other ways to get or infer the MAC access, but being able to query it directly would be ideal.)_
* Command to list active system variables _(As a workaround, you can query every number for its label. This assumes no system variables of interest are unlabelled.)_
* Command to read an ambient light sensor's lux value. _(As a workaround, you can target a light sensor to a system variable. Less elegant but it works.)_
* Event notification for ambient light sensor lux values. _(Same workaround as above.)_

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
