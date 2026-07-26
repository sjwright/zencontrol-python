# API layer — get started

`zencontrol.api` maps TPI Advanced to typed Python: a **command client** and a separate **event receiver**.
Import from `zencontrol.api` (not the top-level package for the command client).

## Command plane

```python
import asyncio
from zencontrol.api import ZenCommandClient, ZenController

async def main() -> None:
    ctrl = ZenController(
        id="1",
        name="living",
        label="Living Room",
        host="192.168.1.100",
        port=5108,
    )
    async with ZenCommandClient(print_traffic=True) as commands:
        print(await commands.query_controller_version_number(ctrl))
        print(await commands.query_controller_label(ctrl))

        gears = await commands.query_control_gear_dali_addresses(ctrl)
        for addr in gears[:3]:
            level = await commands.dali_query_level(addr)
            print(addr.number, level)
            await commands.dali_arc_level(addr, 128)

asyncio.run(main())
```

`ZenController` here is the API dataclass (identity + host). It does not own sockets — `ZenCommandClient` opens a `ZenClient` per controller as needed.

Useful families on `ZenCommandClient`:

- Controller / DALI ready / labels
- Gear levels, scenes, colour, inhibit, custom fade
- Groups, profiles, instances, system variables
- TPI event emit / unicast address / per-address filters (`ZenEventMask`)

## Event plane

One funnel: `ZenEventReceiver`. Acquire a lease, subscribe by MAC (or provisional host IP), handle decoded events.

```python
import asyncio
from zencontrol.api import Transport, ZenEventReceiver
from zencontrol.api.event_decode import ZenDecodedEvent

async def on_event(ev: ZenDecodedEvent) -> None:
    # Do not await command I/O here — it stalls the shared funnel.
    print(type(ev).__name__, ev)

async def main() -> None:
    receiver = ZenEventReceiver()
    lease = await receiver.acquire(Transport.MULTICAST)
    sub = receiver.subscribe(
        on_event,
        mac=bytes.fromhex("aabbccddeeff"),  # controller MAC
    )
    try:
        await asyncio.sleep(3600)
    finally:
        sub.close()
        await receiver.close()

asyncio.run(main())
```

Notes:

- `subscribe(host=...)` must be a resolved IPv4 string — call `await resolve_host(...)` first.
- Enable emit / set unicast target on the **command** plane (`tpi_event_emit`, `set_tpi_event_unicast_address`).
- `ZenEventMask.all_events()` skips deprecated level-change codes; prefer `LEVEL_CHANGE_V2` / `IS_OCCUPIED`.

## When to stay on the API layer

- Scripting queries without entity caches
- Custom routing on top of `ZenEventReceiver`
- Tests that only need commands (`zencontrol.testing.ZenTestClient` for harnesses)

For discovery, keepalives, entity objects, and app callbacks, use [Interface](interface.md).

## See also

- [Overview](overview.md)
- [IO](io.md)
- [Interface](interface.md)
