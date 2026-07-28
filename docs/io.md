# IO layer — get started

`zencontrol.io` is the wire stack: UDP in and out, framing, checksums, sequence numbers.
It does **not** know TPI command names or event-code vocabulary — those live in `zencontrol.api`.

| Plane | Shape |
| --- | --- |
| **Commands** | One connected `ZenClient` **per controller** (`ZenRequest` → `ZenResponse`) |
| **Events** | Typically one shared `ZenEndpoint` for the process; parses envelopes and pushes `ZenEvent` to a sync sink |

You probably don't want to be writing any code with these, other than for debugging purposes, or writing tests.

## What you get

| Piece | Role |
| --- | --- |
| `ZenClient` | Connected UDP client to one controller host:port |
| `ZenRequest` | Outbound command envelope |
| `ZenResponse` | Inbound reply (`OK` / `ANSWER` / `TIMEOUT` / …) |
| `ZenEndpoint` | Bind multicast or unicast; validate envelopes; sink `ZenEvent`s |
| `ZenEvent` | Validated event frame (code stays an opaque `int`) |

## Send one command

```python
import asyncio
from zencontrol.io import ZenRequest, ZenRequestType, ZenResponseType, ZenClient

# QUERY_CONTROLLER_LABEL = 0x24 (see ZenCommandClient.CMD in api.commands)
async def main() -> None:
    client = await ZenClient.create(("192.168.1.100", 5108))
    try:
        req = ZenRequest(command=0x24, data=[0x00], request_type=ZenRequestType.BASIC)
        resp = await client.send_request_with_retries(req)
        print(resp.response_type, resp.data)
        # ANSWER (0xA1) → resp.data is the label bytes
    finally:
        await client.close()

asyncio.run(main())
```

`ZenRequestType.BASIC` pads the data field to 4 bytes. Sequence numbers, XOR checksums, datagram retries, and queue-full backoff are handled inside `ZenClient` / `send_request_with_retries`. Bad packets and transport death surface as `TIMEOUT` / `INVALID` rather than raising. Set `print_traffic=True` on the client to dump request/response bytes and RTT; timeouts are also logged there.

## Listen for events

`ZenEndpoint` is a dumb pipe that understands the wire: it binds a socket, drops malformed datagrams (debug-logged), and pushes parsed `ZenEvent`s into a **sync** sink (no `await`). Queuing, MAC routing, leases, and decoding live in `ZenEventReceiver` ([API](api.md)).

```python
import asyncio
from zencontrol.io import EventConst, ZenEndpoint, ZenEvent

def on_event(event: ZenEvent) -> None:
    # code is opaque here — decode in zencontrol.api.event_decode
    print(event.mac.hex(":"), event.code, event.target, event.payload.hex())

async def main() -> None:
    endpoint = await ZenEndpoint.open(
        unicast=False,  # True + listen_port=… for unicast
        sink=on_event,
    )
    print(f"Listening on {EventConst.MULTICAST_GROUP}:{endpoint.bound_port}")
    try:
        await asyncio.Future()  # run until cancelled
    finally:
        await endpoint.close()

asyncio.run(main())
```

- **Multicast** — joins `EventConst.MULTICAST_GROUP`:`MULTICAST_PORT` (not configurable on the controller).
- **Unicast** — binds `listen_ip` / `listen_port` (`0` = ephemeral; then read `endpoint.bound_port`). Program that address into the controller via the command plane (`SET_TPI_EVENT_UNICAST_ADDRESS`).

Use `accept_datagram(data, addr, sink)` when simulating the endpoint handoff without a socket (same parse-then-sink path as `ZenEventProtocol`).

## Rules of thumb

- Prefer `send_request_with_retries` for commands (datagram retries + queue-full backoff).
- Event codes stay opaque in `io`; vocabulary lives in `api.event_decode`.
- Controllers must have TPI event emit enabled (command plane) or you will hear nothing.
- Application code should use `ZenEventReceiver` / `ZenControl`, not a bare `ZenEndpoint`, unless you are debugging.

## See also

- [Overview](overview.md)
- [API](api.md)
- [Interface](interface.md)
