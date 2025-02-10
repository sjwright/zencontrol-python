# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python. The TPI Advanced protocol has been fully implemented as **ZenProtocol** in *zen.py*, including class methods for most API commands, and callbacks for multicast (or unicast) event packets. It works for me, but temper your expectations. This code has only been tested in my development environment, with one zc-controller-pro connected to a small number of lights, sensors and wall switches.

An opinionated abstraction layer has been implemented as **ZenInterface** in *zen_interface.py*, using ZenProtocol, intended for writing integrations into smart building control software. This sanitises the raw protocol into a relatively straightforward set of methods for controlling lights, groups, profiles, buttons, motion sensors and system variables.

A bridge to Home Assistant has been implmented as **mqtt.py**, which uses ZenInterface to interact with Zencontrol devices, and MQTT to interact with Home Assistant. Currently it's a headless process which reads *config.json* for settings and it spews out a lot of console messsages and log entries.

Note that controller firmware 2.1.35 is required for full functionality, which may not be publicly available yet. If it's not available from within Zen cloud interface, you might be able to request it by raising a support ticket.

Implemented but untested:
  
* Dealing with multiple controllers (I can't test as I only have one controller)
* RGB+ and XY colour commands (I don't have any compatible lights)
* Event filtering (I haven't tested it)

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they don't serve any purpose)

## How to run

In order to test this code, I am writing an MQTT bridge to Home Assistant, which can be run by editing **config.yaml** (and **test-config.yaml** if you wish to run the test suite) to suit your environment, setting up a venv, calling pip install -r requirements.txt, and then running **mqtt.py** within the venv.

```
git clone <repo>
cd <repo>
python3 -m venv .venv
source ./.venv/bin/activate
pip3 install -r requirements.txt
python3 ./mqtt.py
```

## TPI Advanced errata

The following TPI Advanced commands/events are incomplete:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it only returns correct values under some instances. If the temperature is changed by way of scene recall, this query returns wrong information.
* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it only does so under some circumstances. If the temperature is changed by way of scene recall, the event does not fire.

## TPI Advanced wishlist

High priority:

* Command to fetch colour temperatures as entered in the "colour scene assignment" section of the Zen cloud grid view. This would be similar to QUERY_SCENE_LEVELS_BY_ADDRESS but for colour temperatures.

Low priority:

* Command to list active system variables _(Workaround: you can iterate through every number and request its label string. This assumes all system variables have been assigned labels.)_
* Event notification for ambient light sensor lux values. _(Workaround: you can target a light sensor to a system variable, which sends event notifications as of firmware version 2.1.32.)_
* Command to read an ambient light sensor's lux value. _(Workaround: you can target a light sensor to a system variable and read the system variable.)_

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
* [TPI Advanced documentation (PDF)](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
