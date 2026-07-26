# IO layer — get started

`zencontrol.io` is the wire stack: UDP datagrams in and out, checksums, sequence numbers.
It does **not** know TPI command names or event codes — those live in `zencontrol.api`.

## What you get

| Piece | Role |
| --- | --- |
| `ZenClient` | Connected UDP client; send `Request`, await `Response` |
| `Request` / `Response` | Framed command envelopes |
| `ZenEvent` / `parse_frame` | Validate an inbound event datagram (code stays opaque `int`) |
| `ZenEndpoint` | Bind multicast/unicast UDP for the event plane |

Prefer [API](api.md) or [Interface](interface.md) unless you are debugging the wire or building a custom transport.

## Send one command

```python
import asyncio
from zencontrol.io import Request, RequestType, ResponseType, ZenClient

# QUERY_CONTROLLER_LABEL = 0x24 (see ZenCommandClient.CMD in api.commands)
async def main() -> None:
    client = await ZenClient.create(("192.168.1.100", 5108))
    try:
        req = Request(command=0x24, data=[0x00], request_type=RequestType.BASIC)
        resp = await client.send_request_with_retries(req)
        print(resp.response_type, resp.data)
        # ANSWER (0xA1) → resp.data is the label bytes
    finally:
        await client.close()

asyncio.run(main())
```

`RequestType.BASIC` pads the data field to 4 bytes. Sequence numbers and XOR checksums are handled inside `ZenClient`.

## Listen for events

`ZenEndpoint` binds one UDP socket and pushes raw datagrams to a sink. Parse inside the sink with `parse_frame` — the endpoint itself does no framing.

```python
import asyncio
from zencontrol.io import EventConst, ZenEndpoint, parse_frame

def on_datagram(data: bytes, addr: tuple[str, int]) -> None:
    event = parse_frame(data, addr)
    if event is None:
        return  # bad magic / checksum / length
    # code is an opaque int here — decode in zencontrol.api.event_decode
    print(event.mac.hex(":"), event.code, event.target, event.payload.hex())

async def main() -> None:
    endpoint = await ZenEndpoint.open(
        unicast=False,  # True + listen_port=… for unicast
        sink=on_datagram,
    )
    print(f"Listening on {EventConst.MULTICAST_GROUP}:{endpoint.bound_port}")
    try:
        await asyncio.Future()  # run until cancelled
    finally:
        await endpoint.close()

asyncio.run(main())
```

Multicast joins `239.255.90.67:6969` (`EventConst`). Unicast binds `listen_ip` / `listen_port` (use `0` for an ephemeral port, then read `endpoint.bound_port`).

This path is for debugging or custom transports. Application code usually wants the queued funnel in `ZenEventReceiver` ([API](api.md)).

## Rules of thumb

- Retries and queue-failure backoff: use `send_request_with_retries`.
- Event codes stay opaque in `io`; vocabulary lives in `api.event_decode`.
- Controllers must have TPI event emit enabled (command plane) or you will hear nothing.

## See also

- [Overview](overview.md)
- [API](api.md)
- [Interface](interface.md)
