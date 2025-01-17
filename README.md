# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python. Right now, it's mostly methods which map directly to the API, however I'm also adding commands which sanitises some of the complexity of TPI, whereever possible. This python implememtation is still early in development and has only been exposed to bench testing with extremely basic code. My test environment is one zc-controller-pro connected to one light, one sensor, and one wall switch. Temper your expectations accordingly.

Supported:

* Most documented commands
* All documented event packets (in the default multicast UDP mode)
* Packet checksums generated, tested and enforced
* Packet sequence counter maintained and enforced

Supported in theory:
  
* Dealing with multiple controllers (I can't test as I only have one controller)
* RGB+ and XY colour commands (I can't test as I don't have any compatible lights)
* Event filtering (I haven't tested it)

Not yet supported:

* Unicast event packets (setup commands are written but not yet tested)

No support planned:

* DACP sequences (some weird DALI thing which doesn't seem to be useful)
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

_Note: fixes for these should come a future firmware update. Thank you to ZenControl for an impressively prompt reponse to my bug reports._

## TPI Advanced wishlist

* Command to fetch colour temperatures as entered in the "colour scene assignment" section of the Zen cloud grid view. Similar to QUERY_SCENE_LEVELS_BY_ADDRESS but for colour temperatures.
* Command to read an ambient light sensor's lux value. _(Workaround: you can target a light sensor to a system variable and read its value.)_
* Event notification for ambient light sensor lux values. _(Workaround: you can target a light sensor to a system variable, which a future firmware release will send as a SYSTEM_VARIABLE_CHANGED_EVENT.)_
* Event notification when a scene is recalled on a group, i.e. SCENE_CHANGE_EVENT with a target of 64-79. _(ZenControl has confirmed this will come in a future firmware update.)_

The following would also be nice, but low priority:

* Event notifications when a system variables changes. _(ZenControl has confirmed SYSTEM_VARIABLE_CHANGED_EVENT will come in a future firmware update.)_
* Event notifications for IS_UNOCCUPIED. _(Technically unnecessary, as you can query the instance's timing and calculate when a space is deemed unoccupied.)_
* Command to run a sequence
* Command to list sequences on a controller
* Command to list labels for system variables

## Links

* [About TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
* [TPI Advanced documentation (PDF)](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
