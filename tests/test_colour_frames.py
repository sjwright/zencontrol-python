"""DALI_COLOUR fixed-length framing (type + 7 colour-data bytes)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zencontrol import ZenRgbColour, ZenTcColour, ZenXyColour, colour_from_bytes
from zencontrol.api.commands import CMD, ZenCommandClient
from zencontrol.api.const import Const
from zencontrol.api.models import ZenController
from zencontrol.io.models import ZenRequest, ZenRequestType


def _padded(colour: ZenTcColour | ZenXyColour | ZenRgbColour) -> bytes:
    payload = colour.to_bytes()
    need = 1 + Const.COLOUR_DATA_LEN
    if len(payload) < need:
        payload = payload + bytes([0xFF] * (need - len(payload)))
    return payload


def test_tc_to_bytes_pads_in_send_form() -> None:
    padded = _padded(ZenTcColour(kelvin=4000))
    assert len(padded) == 1 + Const.COLOUR_DATA_LEN
    assert padded[0] == 0x20
    assert padded[1:3] == (4000).to_bytes(2, "big")
    assert padded[3:] == bytes([0xFF] * (Const.COLOUR_DATA_LEN - 2))


def test_xy_to_bytes_pads_in_send_form() -> None:
    padded = _padded(ZenXyColour(x=0x1111, y=0x2222))
    assert len(padded) == 1 + Const.COLOUR_DATA_LEN
    assert padded[0] == 0x10
    assert padded[1:5] == bytes([0x11, 0x11, 0x22, 0x22])
    assert padded[5:] == bytes([0xFF] * (Const.COLOUR_DATA_LEN - 4))


def test_rgb_to_bytes_pads_control_byte() -> None:
    colour = ZenRgbColour(r=10, g=20, b=30, w=40, a=50, f=60)
    assert len(colour.to_bytes()) == 7
    assert list(_padded(colour)) == [0x80, 10, 20, 30, 40, 50, 60, 0xFF]


@pytest.mark.asyncio
async def test_send_colour_pads_to_fixed_frame() -> None:
    client = ZenCommandClient()
    client._send_packet = AsyncMock()  # type: ignore[method-assign]
    ctrl = ZenController(id="t", name="t", label="t", host="127.0.0.1", port=5108)

    await client._send_colour(ctrl, CMD.DALI_COLOUR, 0x05, ZenTcColour(kelvin=4000), level=0xFF)

    req = client._send_packet.await_args.args[1]
    assert isinstance(req, ZenRequest)
    assert req.request_type == ZenRequestType.DALI_COLOUR
    assert len(req.data) == 2 + 1 + Const.COLOUR_DATA_LEN
    assert req.data[0] == 0x05
    assert req.data[1] == 0xFF
    assert req.data[2:] == _padded(ZenTcColour(kelvin=4000))


def test_colour_from_bytes_accepts_padded_forms() -> None:
    assert colour_from_bytes(_padded(ZenTcColour(kelvin=4000))) == ZenTcColour(kelvin=4000)
    assert colour_from_bytes(_padded(ZenXyColour(x=9, y=8))) == ZenXyColour(x=9, y=8)
    rgb = ZenRgbColour(r=1, g=2, b=3, w=4, a=5, f=6)
    assert colour_from_bytes(_padded(rgb)) == rgb
    # Narrow COLOUR_CHANGED_EVENT still works
    assert colour_from_bytes(bytes([0x80, 10, 20, 30])) == ZenRgbColour(r=10, g=20, b=30)
