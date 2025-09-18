# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol, written in Python. This library has been written with three levels of abstraction:

# zencontrol.io: Implementation of the raw TPI Advanced UDP packet specifications;
# zencontrol.api: Implementation of most TPI Advanced API commands and events;
# zencontrol.interface: An opinionated abstraction layer suitable for integration into smart building control software. It provides methods, objects, and callbacks for managing lights, groups, profiles, buttons, motion sensors, and system variables. This code is still undergoing significant refinement.

Built on top of this is an example application **mqtt_bridge.py**, which is a bridge to Home Assistant via MQTT. It reads settings from *config.json* and spams your console with lots of debug messages. To run this, ensure you have Python 3.11 (or later) installed, along with the following Python packages: `aiomqtt`, `yaml`, and `colorama`. Modify **config.yaml** as needed for your environment, then execute **mqtt_bridge.py**.

## Requirements

* Python 3.11 (or later)
* Controller firmware 2.1.35 (or later)

## Quick start

The following are the minimum steps necessary to run the MQTT bridge on a Raspberry Pi running the lastest release of Raspberry Pi OS.

```
# Update/Install packages:
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3-yaml python3-colorama python3-pip
pip3 install aiomqtt

# Download this code:
cd ~/Documents
git clone https://github.com/sjwright/zencontrol-python
cd zencontrol-python

# Edit config.yaml to suit your environment
nano config.yaml

# Run the MQTT bridge
python3 mqtt.py
```

Be aware that many Linux distributions ship with old versions of python and it could require non-trivial steps to install a newer version. You can check your current python version by running `python3 -V`

## Limitations

Implemented but untested:
  
* Dealing with multiple controllers (I only have one controller)
* RGB+ and XY colour commands (I don't have any compatible lights)
* Numerical (absolute) instances (I don't have any such ECDs)
* Event filtering (I haven't tested it)

Not implemented:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they aren't useful)

## TPI Advanced errata

The following TPI Advanced commands/events are incomplete:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it only returns correct values under some instances. If the temperature is changed by way of scene recall, this query returns wrong information. _(A fix is anticipated soon)_
* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it only does so under some circumstances. If the temperature is changed by way of scene recall, the event does not fire. _(A fix is anticipated soon)_

## TPI Advanced wishlist

* Command to return a controller's MAC address used for multicast packets _(There are other ways to get or infer the MAC access, but being able to query it directly would be ideal.)_
* Command to list active system variables _(As a workaround, you can query every number for its label. This assumes no system variables of interest are unlabelled.)_
* Command to read an ambient light sensor's lux value. _(As a workaround, you can target a light sensor to a system variable. Less elegant but it works.)_
* Event notification for ambient light sensor lux values. _(Same workaround as above.)_

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
