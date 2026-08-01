# Overview

`zencontrol-python` talks to Zencontrol controllers over **TPI Advanced** (UDP).
The library is stacked in three layers - pick the highest one that fits your job.

| Layer | Description | Docs |
| --- | --- | --- |
| **Interface** | Rich applications and platform integrations, with complete bus enumeration and all the trimmings. | [interface.md](interface.md) |
| **API** | You want access to the documented API suface pretty much exactly as described by zencontrol, but in python form. | [api.md](api.md) |
| **IO** | You want raw request/response framing and validated event envelopes only. (Really though, you don't. It's only mentioned here to describe a clear divison in the library architecture.) | [io.md](io.md) |

## Example

```python
import zencontrol

async with zencontrol.ZenControl() as zen:
    zen.add_controller(
        id=1,
        name="mainoffice",
        label="Main Office",
        host="192.168.1.100",
        port=5108,
    )
    await zen.start()
    for light in await zen.get_lights():
        await light.set(level=50)
```

## Requirements

- Python 3.14+
- Controller firmware **2.2.130+** recommended (minimum **2.2.11**)

## See also

- [Interface](interface.md)
- [API](api.md)
- [IO](io.md)
