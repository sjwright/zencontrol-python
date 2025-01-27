# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python. Most API commands have been implemented. These commands have been used to build a relatively sane interface for programmatically interacting with lights, motion sensors and system variables. An MQTT bridge to Home Assistant is under development using this library.

This code has only been tested in one minimal environment, with one zc-controller-pro connected to a small number of lights, sensors and wall switches. It works, but temper your expectations accordingly.

Supported:

* Most documented commands
* All documented event packets, multicast or unicast
* Packet checksums generated, tested and enforced
* Packet sequence counter maintained and enforced

Supported in theory:
  
* Dealing with multiple controllers (I can't test as I only have one controller)
* RGB+ and XY colour commands (I can't test as I don't have any compatible lights)
* Event filtering (I haven't tested it)

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these, so I couldn't test them even if I wanted to)
* Any commands described in the documentation as "legacy" (I'm pretty sure they don't serve any purpose, but would be trivial to add)

## Use cases

In order to test this code, I have started writing an MQTT bridge to Home Assistant, which can be run by editing **config.yaml** to suit your environment, setting up a venv, calling pip install -r requirements.txt, and running **mqtt.py** within the venv.

```
git clone <repo>
cd <repo>
python3 -m venv .venv
source ./.venv/bin/activate
pip3 install -r requirements.txt
python3 ./mqtt.py
```

## TPI Advanced errata

The following TPI Advanced commands/events are not working as documented:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it does not. The documentation (page 52) says it can report back with TC colour type.
* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it does not. The documentation (page 105) explicitly describes a sample response with a colour type of 0x20 ("Colour Mode TC") and a two byte Kelvin value.

_Note: Partial fixes for the above have been added to the firmware as of version 2.1.32. They now work in some contexts and not others (most notably, after a scene recall). Thank you to ZenControl for an impressively prompt reponse to my bug reports._

## TPI Advanced wishlist

* Command to fetch colour temperatures as entered in the "colour scene assignment" section of the Zen cloud grid view. This would be similar to QUERY_SCENE_LEVELS_BY_ADDRESS but for colour temperatures.
* Event notification when a scene is recalled on a group, i.e. SCENE_CHANGE_EVENT with a target of 64-79. _(ZenControl has added SYSTEM_VARIABLE_CHANGED_EVENT as of firmware version 2.1.32.)_
* Event notifications when a system variables changes. _(ZenControl has added SYSTEM_VARIABLE_CHANGED_EVENT as of firmware version 2.1.32.)_

The following would also be nice, but low priority:

* Event notification for ambient light sensor lux values. _(Workaround: you can target a light sensor to a system variable, which sends event notifications as of firmware version 2.1.32.)_
* Command to read an ambient light sensor's lux value. _(Workaround: you can target a light sensor to a system variable and read its value.)_
* Command to list sequences on a controller
* Command to list system variables by label

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
* [TPI Advanced documentation (PDF)](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
