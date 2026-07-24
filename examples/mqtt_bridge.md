# MQTT bridge example

Example Home Assistant bridge via MQTT auto-discovery. It reads settings from `config.yaml` and prints debug messages to the console. This example is deprecated in favour of [zencontrol-homeassistant](https://github.com/sjwright/zencontrol-homeassistant), but serves as an example for how to implement the full-featured `interface.py` layer of this library.

## Requirements

* Python 3.14 (or later)
* Controller firmware 2.2.11 (or later)
* An MQTT broker reachable from this host

## Quick start

Minimum steps on a sufficiently modern Debian-based system (Ubuntu, Raspberry Pi OS, etc.):

```
# Update/Install packages:
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3-pip

# Download this code:
cd ~/Documents
git clone https://github.com/sjwright/zencontrol-python
cd zencontrol-python
pip3 install -e ".[mqtt]"

# Edit config.yaml to suit your environment
cp examples/config-example.yaml examples/config.yaml
nano examples/config.yaml

# Run the MQTT bridge
zencontrol-mqtt
# or: python3 examples/mqtt_bridge.py
```

Be aware that many Linux distributions ship with old versions of Python and it could require non-trivial steps to install a newer version. You can check your current Python version with `python3 -V`.

## Config

Start from [`config-example.yaml`](config-example.yaml). Controllers, MQTT broker credentials, and related options are documented inline in that file.
