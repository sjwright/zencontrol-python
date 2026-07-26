"""Byte-literal tests for parse_frame and decode (phase 1).

No sockets, no controllers, no event loop. Frames are built as literals with
an XOR checksum — never via a shared encoder that could agree with itself.
"""

from __future__ import annotations

import pytest

from zencontrol.api.event_decode import (
    AbsoluteInput,
    ButtonHold,
    ButtonPress,
    ColourChange,
    GroupLevelChange,
    GroupOccupied,
    IsOccupied,
    LevelChange,
    LevelChangeV2,
    ProfileChange,
    SceneChange,
    SystemVariableChange,
    ZenEventCode,
    ZenEventMask,
    decode,
)
from zencontrol.io.event import ZenEvent, parse_frame

ADDR = ("192.168.1.10", 6969)
MAC = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x01])


def _xor(buf: bytes) -> int:
    acc = 0
    for b in buf:
        acc ^= b
    return acc & 0xFF


def _frame(
    *,
    mac: bytes = MAC,
    target: int = 0,
    code: int,
    payload: bytes = b"",
    corrupt_checksum: bool = False,
    force_payload_len: int | None = None,
) -> bytes:
    body = (
        bytes([0x5A, 0x43])
        + mac
        + target.to_bytes(2, "big")
        + bytes([code, force_payload_len if force_payload_len is not None else len(payload)])
        + payload
    )
    checksum = _xor(body)
    if corrupt_checksum:
        checksum ^= 0xFF
    return body + bytes([checksum])


# ---------------------------------------------------------------------------
# parse_frame — envelope rejection paths
# ---------------------------------------------------------------------------


def test_parse_frame_accepts_valid_empty_payload() -> None:
    data = _frame(code=0x00, payload=b"\x03")
    event = parse_frame(data, ADDR)
    assert event is not None
    assert event.mac == MAC
    assert event.host == "192.168.1.10"
    assert event.code == 0x00
    assert event.target == 0
    assert event.payload == b"\x03"
    assert isinstance(event.code, int)
    assert not isinstance(event.code, ZenEventCode)


def test_parse_frame_rejects_too_short() -> None:
    assert parse_frame(b"ZC", ADDR) is None
    assert parse_frame(bytes(12), ADDR) is None


def test_parse_frame_rejects_bad_magic() -> None:
    data = _frame(code=0x00, payload=b"\x00")
    bad = bytes([0x00, 0x00]) + data[2:]
    assert parse_frame(bad, ADDR) is None


def test_parse_frame_rejects_bad_checksum() -> None:
    data = _frame(code=0x00, payload=b"\x00", corrupt_checksum=True)
    assert parse_frame(data, ADDR) is None


def test_parse_frame_rejects_payload_length_mismatch() -> None:
    # Declared length 5 but only 1 payload byte before checksum
    data = _frame(code=0x00, payload=b"\x00", force_payload_len=5)
    assert parse_frame(data, ADDR) is None


def test_parse_frame_extracts_two_byte_target() -> None:
    data = _frame(target=0x0040, code=0x06, payload=b"\x01")
    event = parse_frame(data, ADDR)
    assert event is not None
    assert event.target == 64


# ---------------------------------------------------------------------------
# decode — one typed dataclass per code
# ---------------------------------------------------------------------------


def _event(code: int, payload: bytes, target: int = 0) -> ZenEvent:
    return ZenEvent(
        mac=MAC,
        target=target,
        code=code,
        payload=payload,
        host="192.168.1.10",
        received_at=0.0,
    )


@pytest.mark.parametrize(
    ("code", "payload", "target", "expected"),
    [
        (0x00, b"\x02", 64, ButtonPress(target=64, instance=2)),
        (0x01, b"\x03", 65, ButtonHold(target=65, instance=3)),
        (0x02, b"\x01\x12\x34", 64, AbsoluteInput(target=64, instance=1, value=0x1234)),
        (0x03, b"\xFE", 5, LevelChange(target=5, level=0xFE)),
        (0x04, b"\x80", 64, GroupLevelChange(target=64, level=0x80)),
        (0x05, b"\x01\x01", 3, SceneChange(target=3, scene=1, active=True)),
        (0x05, b"\x02\x00", 67, SceneChange(target=67, scene=2, active=False)),
        (0x06, b"\x00\x01", 64, IsOccupied(target=64, instance=0)),
        (
            0x07,
            b"\x00\x00\x00\x2A\x00",
            7,
            SystemVariableChange(target=7, value=42),
        ),
        (0x08, b"\x20\x0B\xB8\x00\x00\x00\x00", 1, ColourChange(
            target=1, colour=b"\x20\x0B\xB8\x00\x00\x00\x00"
        )),
        (0x09, b"\x00\x0F", 0, ProfileChange(profile=15)),
        (0x0A, b"\xFF\x01", 64, GroupOccupied(target=64, occupied=True)),
        (0x0A, b"\xFF\x00", 65, GroupOccupied(target=65, occupied=False)),
        (0x0B, b"\xFE\x00", 59, LevelChangeV2(target=59, current=0xFE, level=0)),
    ],
)
def test_decode_each_code(code: int, payload: bytes, target: int, expected: object) -> None:
    assert decode(_event(code, payload, target)) == expected


def test_decode_sysvar_with_magnitude() -> None:
    # raw=5, magnitude=2 → 500
    assert decode(_event(0x07, b"\x00\x00\x00\x05\x02", target=1)) == SystemVariableChange(
        target=1, value=500
    )


def test_decode_rejects_unknown_code() -> None:
    assert decode(_event(0xFF, b"\x00")) is None


@pytest.mark.parametrize(
    ("code", "payload"),
    [
        (0x00, b""),
        (0x01, b""),
        (0x02, b"\x01\x00"),  # need 3
        (0x03, b""),
        (0x04, b""),
        (0x05, b"\x01"),  # need 2
        (0x06, b""),
        (0x06, b"\x00"),  # occupancy needs exactly 2
        (0x07, b"\x00\x00\x00\x01"),  # need 5
        (0x08, b""),
        (0x08, b"\x20"),  # colour needs 3–7
        (0x08, b"\x20" + bytes(7)),  # too long
        (0x09, b""),
        (0x09, b"\x0F"),  # profile needs exactly 2
        (0x0A, b"\xFF"),  # need 2
        (0x0B, b"\xFE"),  # need 2
    ],
)
def test_decode_rejects_wrong_length_payload(code: int, payload: bytes) -> None:
    assert decode(_event(code, payload)) is None


@pytest.mark.parametrize(
    ("code", "payload"),
    [
        (0x00, b"\x02\x00"),
        (0x01, b"\x03\xFF"),
        (0x02, b"\x01\x12\x34\x00"),
        (0x03, b"\xFE\x00"),
        (0x04, b"\x80\x00"),
        (0x05, b"\x01\x01\x00"),
        (0x06, b"\x00\x01\x00"),
        (0x07, b"\x00\x00\x00\x2A\x00\x00"),
        (0x09, b"\x00\x0F\x00"),
        (0x0A, b"\xFF\x01\x00"),
        (0x0B, b"\xFE\x00\x00"),
    ],
)
def test_decode_rejects_trailing_junk(code: int, payload: bytes) -> None:
    assert decode(_event(code, payload)) is None


def test_decode_rejects_sysvar_out_of_range_target() -> None:
    assert decode(_event(0x07, b"\x00\x00\x00\x01\x00", target=148)) is None
    assert decode(_event(0x07, b"\x00\x00\x00\x01\x00", target=200)) is None


def test_level_change_and_v2_remain_distinct_types() -> None:
    a = decode(_event(0x03, b"\x10", target=1))
    b = decode(_event(0x0B, b"\x10\x20", target=1))
    assert type(a) is LevelChange
    assert type(b) is LevelChangeV2
    assert type(a) is not type(b)


def test_occupied_types_remain_distinct() -> None:
    a = decode(_event(0x06, b"\x01\x01", target=64))
    b = decode(_event(0x0A, b"\xFF\x01", target=64))
    assert type(a) is IsOccupied
    assert type(b) is GroupOccupied


def test_all_events_excludes_deprecated_and_unused_codes() -> None:
    mask = ZenEventMask.all_events()
    assert mask.level_change is False
    assert mask.group_level_change is False
    assert mask.group_occupied is False
    assert mask.level_change_v2 is True
    assert mask.is_occupied is True
    bits = mask.bitmask()
    assert bits & (1 << ZenEventCode.LEVEL_CHANGE) == 0
    assert bits & (1 << ZenEventCode.GROUP_LEVEL_CHANGE) == 0
    assert bits & (1 << ZenEventCode.GROUP_OCCUPIED) == 0
    assert bits & (1 << ZenEventCode.LEVEL_CHANGE_V2) != 0


def test_end_to_end_parse_then_decode() -> None:
    data = _frame(target=59, code=0x0B, payload=b"\xFE\x00")
    event = parse_frame(data, ADDR)
    assert event is not None
    assert decode(event) == LevelChangeV2(target=59, current=0xFE, level=0)


def test_io_has_no_event_code_vocabulary() -> None:
    """I7a: io/ must not define or import ZenEventCode."""
    import zencontrol.io.event as event_mod

    assert not hasattr(event_mod, "ZenEventCode")
    assert "ZenEventCode" not in event_mod.__dict__
    source = open(event_mod.__file__, encoding="utf-8").read()
    assert "from zencontrol.api" not in source
    assert "BUTTON_PRESS" not in source
    assert "LEVEL_CHANGE" not in source
