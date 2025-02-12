# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python. The TPI Advanced protocol has been fully implemented as **ZenProtocol** in *zen.py*, including class methods for most API commands, and callbacks for multicast (or unicast) event packets. It works for me, but temper your expectations. This code has only been tested in my development environment, with one zc-controller-pro connected to a small number of lights, sensors and wall switches.

An opinionated abstraction layer has been implemented as **ZenInterface** in *zen_interface.py*, using ZenProtocol, intended for writing integrations into smart building control software. This sanitises the raw protocol into a relatively straightforward set of methods for controlling lights, groups, profiles, buttons, motion sensors and system variables.

A bridge to Home Assistant has been implmented as **mqtt.py**, which uses ZenInterface to interact with Zencontrol devices, and MQTT to interact with Home Assistant. Currently it's a headless process which reads *config.json* for settings and dumps out a ton of console messsages and log entries. To run this, you need to have Python 3.11 installed, plus the following python3 packages: paho-mqtt, yaml, colorama. Edit **config.yaml** appropriately for your environment. Then run **mqtt.py**.

## Requirements

* Python 3.11
* Controller firmware 2.1.35

_Note: A sufficiently new version of Zencontrol firmware may not be publicly available yet. If it's not available from within Zen cloud interface, you might be able to request it by raising a support ticket._

## Limitations

Implemented but untested:
  
* Dealing with multiple controllers (I can't test as I only have one controller)
* RGB+ and XY colour commands (I don't have any compatible lights)
* Event filtering (I haven't tested it)

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they don't serve any purpose)

## TPI Advanced errata

The following TPI Advanced commands/events are incomplete:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it only returns correct values under some instances. If the temperature is changed by way of scene recall, this query returns wrong information.
* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it only does so under some circumstances. If the temperature is changed by way of scene recall, the event does not fire.

## TPI Advanced wishlist

High priority:

* Command to fetch colour temperatures as entered in the "colour scene assignment" section of the Zen cloud grid view. This would be similar to QUERY_SCENE_LEVELS_BY_ADDRESS but for colour temperatures.

Low priority:

* Command to return a controller's MAC address used for multicast packets _(There are other ways to get or infer the MAC access, but being able to query it directly would be ideal.)_
* Command to list active system variables _(As a workaround, you can query every number for its label. This assumes no system variables of interest are unlabelled.)_
* Command to read an ambient light sensor's lux value. _(As a workaround, you can target a light sensor to a system variable. Less elegant but it works.)_
* Event notification for ambient light sensor lux values. _(Same workaround as above.)_

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
* [TPI Advanced documentation (PDF)](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
