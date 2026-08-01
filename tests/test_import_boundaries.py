"""Import boundary tests for event-listener overhaul (I1).

These tests are the enforceable forbidden-import rule between the event and
command planes. If a future change wants to import across the boundary, that
change belongs in ZenEventWiring (interface layer).
"""

from __future__ import annotations

from pathlib import Path


def test_event_router_imports_no_command_plane() -> None:
    """event_router must not import the command plane."""
    import zencontrol.api.event_router as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "api.commands" not in source
    assert "io.command" not in source
    assert "ZenCommandClient" not in source
    assert "query_" not in source
    assert "from ..io import" not in source  # must use io.event directly


def test_commands_imports_no_event_plane() -> None:
    """commands must not import event_router or io.event."""
    import zencontrol.api.commands as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "api.event_router" not in source
    assert "io.event" not in source
    assert "ZenEventReceiver" not in source
    assert "Subscription" not in source
    assert "Lease" not in source
    assert "from ..io import" not in source  # must use io.command directly


def test_commands_has_no_entity_context_concerns() -> None:
    """Command plane must not own entity callbacks, registry, or bg tasks."""
    import zencontrol.api.commands as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "ZenCallbacks" not in source
    assert "EntityRegistry" not in source
    assert "EntityContext" not in source
    assert "entity_registry" not in source
    assert "track_task" not in source
    assert "clear_entity_cache" not in source
    assert "clear_entity_caches" not in source


def test_commands_has_no_session_roster_or_emit_policy() -> None:
    """Command plane must not own controller roster or event-emit preference."""
    import zencontrol.api.commands as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "set_controllers" not in source
    assert "self.controllers" not in source
    assert "self.unicast" not in source
    assert "def _checksum" not in source


def test_commands_has_no_label_display_fallbacks() -> None:
    """Command plane returns wire labels only; generics live in the interface."""
    import zencontrol.api.commands as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "generic_if_none" not in source
    # Fallback formulas that used to live next to the query methods.
    assert 'f"Group {address.number}"' not in source
    assert 'f"Scene {scene}"' not in source
    assert 'f"{address.ctrl.label} ECD' not in source
