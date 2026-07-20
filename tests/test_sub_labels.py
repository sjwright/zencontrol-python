"""Unit tests for comma-separated light sub_label assignment."""

from types import SimpleNamespace

from zencontrol.interface.interface import _assign_light_sub_labels


def _light(controller_name: str, number: int, label: str | None):
    controller = SimpleNamespace(name=controller_name)
    address = SimpleNamespace(controller=controller, number=number)
    return SimpleNamespace(address=address, label=label, sub_label="stale")


def test_shared_comma_label_splits_by_address_order() -> None:
    lights = [
        _light("c1", 34, "Hallway,Bathroom,,Annex"),
        _light("c1", 31, "Hallway,Bathroom,,Annex"),
        _light("c1", 33, "Hallway,Bathroom,,Annex"),
        _light("c1", 32, "Hallway,Bathroom,,Annex"),
    ]

    _assign_light_sub_labels(lights)

    by_number = {lt.address.number: lt.sub_label for lt in lights}
    assert by_number == {
        31: "Hallway",
        32: "Bathroom",
        33: "Unused 33",
        34: "Annex",
    }


def test_unique_or_non_comma_labels_keep_sub_label_none() -> None:
    lights = [
        _light("c1", 1, "Kitchen"),
        _light("c1", 2, "Kitchen"),
        _light("c1", 3, "Hallway,OnlyOne"),
        _light("c1", 4, None),
    ]

    _assign_light_sub_labels(lights)

    assert all(lt.sub_label is None for lt in lights)


def test_clusters_are_scoped_per_controller() -> None:
    lights = [
        _light("a", 1, "One,Two"),
        _light("a", 2, "One,Two"),
        _light("b", 1, "One,Two"),
    ]

    _assign_light_sub_labels(lights)

    assert lights[0].sub_label == "One"
    assert lights[1].sub_label == "Two"
    assert lights[2].sub_label is None


def test_extra_lights_beyond_parts_become_unused() -> None:
    lights = [
        _light("c1", 10, "Alpha,Beta"),
        _light("c1", 11, "Alpha,Beta"),
        _light("c1", 12, "Alpha,Beta"),
    ]

    _assign_light_sub_labels(lights)

    assert [lt.sub_label for lt in sorted(lights, key=lambda x: x.address.number)] == [
        "Alpha",
        "Beta",
        "Unused 12",
    ]
