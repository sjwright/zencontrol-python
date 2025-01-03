# zencontrol-python

This is an implementation of the **Zencontrol TPI Advanced** protocol written in python. This implememtation has only been exposed to extremely basic testing with one zc-controller-pro sitting on one bench, connected to one light and one wall switch. It could also work for you, but I promise nothing.

Right now this is mostly 1:1 with Zencontrol documentation. However I may end up changing stuff to make it more consistent, or to accept/return data in a more usefully structured way. Expect tuples to be replaced with dicts, or dicts to be replaced with tuples. Expect change. Assume nothing.

Nominally supported:

* Most documented commands
* Multicast UDP event packets
* Packet checksums generated, tested and enforced
* Packet sequence counter maintained and enforced

Not yet supported:

* Unicast UDP event packets
* Event filters

No support planned:

* Any commands involving DMX, Control4, or virtual instances (I don't have licenses for any of these, nor a use case)
* Any commands described in the documentation as "legacy" (though they'd be extremely trivial to add if someone had a use case)

## Use cases

In order to test the code, I have started writing an MQTT bridge to Home Assistant, which can be run by editing **config.yaml** to suit your environment, setting up a vdev, calling pip install -r requirements.txt, and running **mqtt.py** within the vdev.

```
git clone <repo>
cd <repo>
python3 -m venv .
source ./.venv/bin/activate
pip3 install -r requirements.txt
```

## TPI Advanced errata

The following TPI Advanced commands currently don't work as documented:

* QUERY_DALI_COLOUR — This command is supposed to be able to return a light's current colour temperature, but it does not. The documentation (page 52) says it can report back with TC colour type.

The following TPI Advanced events are not firing:

* COLOUR_CHANGE_EVENT — This event is supposed to fire when a light's colour temperature changes, but it does not. The documentation (page 105) even provideds a sample response with a colour type of 0x20 ("Colour Mode TC") and a Kelvin value as two bytes.

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

* Fetching scene colour temperatures (as entered in the "colour scene assignment" section of the cloud grid view)
* Event notification when a system variables changes (this is supported and works in their MQTT integration)
* Event notification for ambient light sensor lux values
* Event notification for IS_UNOCCUPIED (Not tested, but documentation says "Not currently used".)

## Links

[About Zencontrol TPI Advanced](https://support.zencontrol.com/hc/en-us/articles/360000337175-What-is-the-Third-Party-Interface-TPI)
[TPI Advanced documentation PDF](https://support.zencontrol.com/hc/en-us/article_attachments/10831057855503)
