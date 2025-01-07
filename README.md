# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python. Right now most methods map directly to the API as described in its documentation, however I've done my best to handle data in a structured way. This implememtation is still under development and has only been exposed to bench testing with extremely basic code, talking to one zc-controller-pro connected to one light, one sensor, and one wall switch. Temper your expectations accordingly.

Nominally supported:

* Most documented commands
* Multicast UDP event packets
* Packet checksums generated, tested and enforced
* Packet sequence counter maintained and enforced
* Dealing with multiple controllers (in theory)

Not yet supported:

* Event filtering
* Unicast UDP event packets (some initial work is done)
* RGB+ and XY colour commands (initial work done, but untested)
* DACP sequences (don't even know what they are)

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these, so I couldn't test them even if I wanted to)
* Any commands described in the documentation as "legacy" (I'm pretty sure they don't serve any purpose, but would be trivial to add)

## Use cases

In order to test the code, I have started writing an MQTT bridge to Home Assistant, which can be run by editing **config.yaml** to suit your environment, setting up a venv, calling pip install -r requirements.txt, and running **mqtt.py** within the venv.

```
git clone <repo>
cd <repo>
python3 -m venv .venv
source ./.venv/bin/activate
pip3 install -r requirements.txt
python3 ./mqtt.py
```

## TPI Advanced errata

The following TPI Advanced commands are not working as documented:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it does not. The documentation (page 52) says it can report back with TC colour type.

The following TPI Advanced events are not firing as documented:

* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it does not. The documentation (page 105) explicitly describes a sample response with a colour type of 0x20 ("Colour Mode TC") and a two byte Kelvin value.

## TPI Advanced documentation errata

* The TPI Advanced API document includes the MQTT API Documentation.
* On page 30, *TPI Event Multicast Frame* is incorrect. It shows 3 bytes for Target instead of 2, and then all subsequent bytes are off-by-one.
* On page 32, ECDs are said to be integers between 64–128, but it should be 64–127.
* On pages 32 and 79, groups are said to be integers between 64–80, but it should be 64–79.
* On page 79, address 81 is said to be for broadcast, which is either wrong or inconsistent.
* On page 36, the TPI Event Types table uses inconsistent event labels.
* On page 36, some listed TPI Event Types are not documented.

## TPI Advanced wishlist

The following is not yet possible with the current TPI Advanced:

* Command to fetch colour temperatures for a gear/scene pair (as entered in the "colour scene assignment" section of the cloud grid view).
* Command to fetch an ambient light sensor's lux value. (There may be some way to forward a lux sensor's value to a system variable, but I haven't worked that out yet.)
* Event notifications for an ambient light sensor's lux value.

The following would be nice, but low priority:

* Event notifications when a system variables changes. The MQTT integration supports this, but not TPI Advanced. Strictly speaking not necessary as you could instead listen for button press events, but there could be some scenarios where this is impractical.
* Event notifications for IS_UNOCCUPIED. Documentation says "Not currently used". Strictly speaking unnecessary, as you can query for the instance's timing and infer when a space is deemed unoccupied, but for the tiny increase in packets I see no reason why not receive the truth in real time from the controller.
* Command to list sequences; command to run a sequence.

## Links

[About Zencontrol TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
[TPI Advanced documentation PDF](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
