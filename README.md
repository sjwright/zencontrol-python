# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python, plus an abstraction layer suited for smart building control integrations. A Home Assistant bridge (via MQTT) is under development using this abstraction layer.

The TPI Advanced protocol has been fully implemented, including sequence counters, enforcing of checksums, and listening for multicast (or unicast) event packets. Most API commands have been implemented. This code has currently only been tested within my own development environment, with one zc-controller-pro connected to a small number of lights, sensors and wall switches. It's working for me, but temper your expectations accordingly.

Note that controller firmware 2.1.32 is required for full functionality, however this version isn't publicly available yet. If it's not available from within Zen Cloud, you might be able to request it by raising a support ticket.

Implemented but untested:
  
* Dealing with multiple controllers (I can't test as I only have one controller)
* RGB+ and XY colour commands (I don't have any compatible lights)
* Event filtering (I haven't tested it)

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these so I couldn't test them even if I wanted to, but the scaffolding is there if anyone wishes to add support)
* Any commands described in the documentation as "legacy" (they don't serve any purpose)

## Use cases

In order to test this code, I am writing an MQTT bridge to Home Assistant, which can be run by editing **config.yaml** to suit your environment, setting up a venv, calling pip install -r requirements.txt, and running **mqtt.py** within the venv.

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

_Note: Partial fixes for the above have been added to the firmware as of version 2.1.32. They now work in some contexts but not others — most notably, after a scene recall._

## TPI Advanced wishlist

Highest priority:

* Command to fetch colour temperatures as entered in the "colour scene assignment" section of the Zen cloud grid view. This would be similar to QUERY_SCENE_LEVELS_BY_ADDRESS but for colour temperatures.

Medium priority:

* Command to identify properties of each profile, including status (enabled, disabled) and priority (high, medium, scheduled, emergency).

Low priority:

* Event notification for ambient light sensor lux values. _(Workaround: you can target a light sensor to a system variable, which sends event notifications as of firmware version 2.1.32.)_
* Command to read an ambient light sensor's lux value. _(Workaround: you can target a light sensor to a system variable and read the system variable.)_
* Command to list sequences on a controller
* Command to list system variables by label

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
* [TPI Advanced documentation (PDF)](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
