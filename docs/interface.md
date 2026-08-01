# Interface layer - get started

`ZenControl` is the recommended entry point. It owns:

- a **command client** (`zen.commands` → `ZenCommandClient`)
- an **event session** (receiver, bindings, supervisor, keepalive)
- an **entity registry** (lights, groups, buttons, sensors, SVs, profiles)
- an application **callback registry** (`zen.callbacks`)

Most integrations only need this layer. Drop to [API](api.md) when you want typed TPI without entities, or [IO](io.md) for wire framing alone.

## Minimal example

```python
import asyncio
import zencontrol

async def on_light_change(*, light) -> None:
    print(light, light.level, light.colour, light.scene)

async def on_resync() -> None:
    # Recoverable event-session gap - re-poll anything you cache locally
    print("resync")

async def main() -> None:
    async with zencontrol.ZenControl() as zen:
        zen.add_controller(
            id=1,
            name="living",          # stable key within this ZenControl
            label="Living Room",    # human-facing
            host="192.168.1.100",
            port=5108,
            # mac="AA:BB:CC:DD:EE:FF",  # optional; improves event binding
            # filtering=False,          # if True, ctrl only emits filtered events
        )
        zen.callbacks.light_change = on_light_change
        zen.callbacks.on_resync = on_resync

        await zen.start()  # bind listener, enable emit, wait until connected

        for light in await zen.get_lights():
            print(light.label, light.level, light.features)
            await light.set(level=50)

        await asyncio.sleep(3600)

asyncio.run(main())
```

Constructor options worth knowing: `unicast=True` (events to this host instead of multicast), `listen_port=…` (unicast bind), `print_traffic=True`, `logger=…`.

## Lifecycle

| Step | What happens |
| --- | --- |
| `ZenControl(...)` | Builds command client, empty context, event receiver. No sockets yet. |
| `add_controller(...)` | Registers a controller object; does not interview the site. |
| `zen.callbacks.* = …` | Hook app code (before or after `start`). |
| `await zen.start()` | Starts supervisor + keepalive, attaches event bindings, enables TPI emit, waits until the listener is up (or raises `ZenConnectionError`). |
| `get_*` / entity methods | Command-plane queries and control; entity `create` runs `interview()`. |
| `await zen.stop()` | Stops monitoring; **keeps** entity caches and command clients. |
| `await zen.aclose()` / leaving `async with` | Full teardown: stop monitoring, close UDP clients, clear caches. |

Hot-plug: `await zen.remove_controller(...)` is safe while monitoring is running. After `add_controller` on a live session, call `await zen.configure_controller_events(ctrl)` so the new controller joins the shared listener.

## Controllers

`add_controller` returns a `ZenController` (interface subclass). Treat `name` as the stable identity inside one `ZenControl` instance.

Useful methods:

- `await ctrl.interview()` - label / version / current profile
- `await zen.switch_to_profile(ctrl, …)` / `await zen.commands.return_to_scheduled_profile(ctrl)`
- `await zen.commands.query_controller_startup_complete(ctrl)` / `query_is_dali_ready(ctrl)`

Health while monitoring:

```python
from zencontrol import EventHealth

health = zen.event_health_for(ctrl)  # RECEIVING / SILENT / …
```

`controller_status_change` reports `"online" | "starting" | "unreachable"` from keepalive / binding loss. Prefer that for UI status rather than inventing your own ping loop - keepalive already re-asserts emit after a controller reboot.

## Entities

`get_control_gear()` / `get_lights()` / `get_fans()` / `get_blinds()`, `get_groups()`, `get_instances()`, `get_profiles()`, and `get_system_variables()` scan the controller(s) and return **singletons** for this `ZenControl` (same address → same object). Passing `ctrl=ctrl` limits the scan. Prefer one `get_control_gear()` and partition when you need all ECG kinds. `get_buttons()` / `get_motion_sensors()` / `get_absolute_inputs()` remain as filters over `get_instances()`.

Fans and blinds are classified by label suffix (`… Fan` / `… Blind`) or a GTIN+bus-unit allowlist when seeded; they are **not** returned from `get_lights()`.

Identity is owned by `zen.ctx` factories (`ctx.light(address)`, `ctx.fan(address)`, `ctx.blind(address)`, `ctx.group(address)`, …). Prefer those (or the async `ctx.create_*` interview wrappers) over constructing entity classes directly.

Each `create` / first fetch runs `interview()` (labels, features, membership, timers, …). Cached state (`light.level`, `sensor.occupied`, …) then updates from events and occasional refresh.

| Type | Control / read highlights |
| --- | --- |
| `ZenControlGear` | Shared base for all control gear (ECG) and groups: `level`/`colour`/`scene` |
| `ZenLight` | Lighting ECG gear: group membership, membership-driven discoordination |
| `ZenFan` | Ceiling fan speeds via discrete ARC levels (`set_speed` / `speed`) |
| `ZenBlind` | Position 0-100%; `stop()` sends DAPC MASK (255) |
| `ZenGroup` | Group gear: mostly the same as lights, plus scene label helpers and group state assertions |
| `ZenButton` | Press / long-press via callbacks |
| `ZenMotionSensor` | `occupied`; occupancy events |
| `ZenAbsoluteInput` | 16-bit `value` |
| `ZenSystemVariable` | `get_value` / `set_value` |
| `ZenProfile` | `select()` |

Example - groups and SVs:

```python
for group in await zen.get_groups():
    await group.set(level=0)

for sv in await zen.get_system_variables():
    print(sv.label, await sv.get_value())
```

`clear_entity_caches()` drops the registries (rare; `aclose` already clears). After a long gap, prefer refreshing via `on_resync` and entity `refresh_state_from_controller` where available, rather than assuming cached levels are still true.

## Callbacks

All hooks are on `zen.callbacks` (`ZenCallbacks`). Wire events are dispatched **keyword-only** to match the typed protocols:

| Callback | Arguments (keyword) | When |
| --- | --- | --- |
| `light_change` | `light` | Light level / colour / scene changed - read state from the entity |
| `fan_change` | `fan` | Fan level / scene changed |
| `blind_change` | `blind` | Blind level / scene changed |
| `group_change` | `group`, `discoordinated` | Group target changed - read state from the entity; `discoordinated=True` when members diverge |
| `button_press` / `button_long_press` | `button` | ECD push button |
| `motion_event` | `sensor` | Occupancy - read `sensor.occupied` |
| `absolute_input_change` | `absolute_input` | Dial / slider - read `absolute_input.value` |
| `system_variable_change` | `system_variable`, `by_me` | SV update - read `system_variable.value`; `by_me=True` when this process set it |
| `profile_change` | `profile` | Active profile |
| `on_resync` | *(none)* | Recoverable session gap after re-arm - **not** a hard disconnect |
| `on_disconnect` | *(none)* | Session actually torn down |
| `on_connect` | *(none)* | First successful connect |
| `controller_discovered` | `discovered` | Multicast identity (not yet added) |
| `controller_identified` | `ctrl`, `mac` | Provisional binding learned MAC - persist it |
| `controller_status_change` | `ctrl`, `status` | `"online"` / `"starting"` / `"unreachable"` |

Silent gaps and rebinds fire `on_resync`, not `on_disconnect`. Use `on_resync` to re-poll; use `on_disconnect` for “listener is gone.”

## Discovery

```python
found = await zen.discover(timeout=5.0)
for i, d in enumerate(found, start=1):
    print(d.host, d.mac, d.label)
    zen.add_controller(
        id=i,
        name=d.mac.replace(":", ""),
        label=d.label or d.mac,
        host=d.host,
        port=d.port,
        mac=d.mac,
    )
```

`discover()` listens for identity on the event plane, then enriches each result with `QUERY_CONTROLLER_LABEL` when possible. It returns controllers **heard in that window** (`last_seen`), so a second call on a long-lived instance can still surface emitters. Identity-only records (no label yet) are also available as `zen.discovered_controllers`; call `await zen.enrich_discovered(d)` if you need a label without the full discover window.

## Tips

- Prefer `ZenControl` over assembling `EntityContext` + `ZenCommandClient` yourself. `EntityContext` is for advanced command-only setups without an event session.
- Reach for `zen.commands.…` when an entity method does not expose a TPI call you need.
- Callbacks already run off the shared funnel on a per-controller task chain - keep them reasonably short; long work can still delay *that* controller's later events.
- For protocol-only scripts and custom routing, see [API](api.md).

## See also

- [Overview](overview.md)
- [API](api.md)
- [IO](io.md)
