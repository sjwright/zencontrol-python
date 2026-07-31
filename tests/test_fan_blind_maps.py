"""Unit tests for fan/blind arc maps."""

from zencontrol import ZenBlind, ZenFan


def test_fan_arcs() -> None:
    assert ZenFan.arc_for_speed(0) == 0
    assert ZenFan.arc_for_speed(1) == 32
    assert ZenFan.arc_for_speed(2) == 95
    assert ZenFan.arc_for_speed(3) == 159
    assert ZenFan.arc_for_speed(4) == 254
    assert ZenFan.speed_from_arc(0) == 0
    assert ZenFan.speed_from_arc(32) == 1
    assert ZenFan.speed_from_arc(100) == 2
    assert ZenFan.speed_from_arc(150) == 3
    assert ZenFan.speed_from_arc(200) == 4


def test_blind_position_linear() -> None:
    assert ZenBlind.position_from_arc(0) == 0
    assert ZenBlind.position_from_arc(254) == 100
    assert ZenBlind.position_from_arc(255) is None
    assert ZenBlind.position_from_arc(None) is None
    assert ZenBlind.arc_for_position(0) == 0
    assert ZenBlind.arc_for_position(100) == 254
    assert ZenBlind.arc_for_position(50) == 127
