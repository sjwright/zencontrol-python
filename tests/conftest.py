"""Shared fixtures for unit and simulator integration tests."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Markers / sibling checkout discovery
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIBLING_SIMULATOR_ROOT = _REPO_ROOT.parent / "zencontrol-simulator"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "simulator: integration tests that start zencontrol-simulator "
        "(pip install -e ../zencontrol-simulator, or a sibling checkout)",
    )


def _ensure_simulator_importable() -> None:
    """Make zencontrol_simulator importable via install or sibling checkout.

    Prefers an already-installed package. Otherwise, if
    ``../zencontrol-simulator`` exists beside this repo, adds it to
    ``sys.path`` so the package can be imported without ``pip install -e``.
    """
    try:
        import zencontrol_simulator  # noqa: F401

        return
    except ImportError:
        pass

    pkg_dir = _SIBLING_SIMULATOR_ROOT / "zencontrol_simulator"
    if not pkg_dir.is_dir():
        return

    sibling = str(_SIBLING_SIMULATOR_ROOT.resolve())
    if sibling not in sys.path:
        sys.path.insert(0, sibling)


def _require_simulator():
    """Import zencontrol_simulator or skip with a clear reason."""
    _ensure_simulator_importable()
    try:
        import zencontrol_simulator
    except ImportError:
        pytest.skip(
            "zencontrol-simulator not available — pip install -e "
            "../zencontrol-simulator or check it out as a sibling directory"
        )

    # Sibling path import still needs the simulator runtime dependency.
    pytest.importorskip("yaml", reason="PyYAML required for zencontrol-simulator")
    return zencontrol_simulator


def _simulator_config_path() -> Path:
    """Resolve the demo world YAML shipped with zencontrol-simulator."""
    sim = _require_simulator()
    packaged = Path(sim.__file__).resolve().parent / "config.yaml"
    if packaged.is_file():
        return packaged
    sibling = _SIBLING_SIMULATOR_ROOT / "config.yaml"
    if sibling.is_file():
        return sibling
    pytest.skip("zencontrol-simulator config.yaml not found")


# ---------------------------------------------------------------------------
# Simulator fixtures
# ---------------------------------------------------------------------------


@dataclass
class LiveSimulator:
    """Running simulator paired with a ``ZenTestClient`` test facade."""

    world: Any
    sim: Any
    commands: Any
    controller: Any

    def ecg(self, number: int):
        from zencontrol import ZenAddress, ZenAddressType

        return ZenAddress(
            controller=self.controller,
            type=ZenAddressType.ECG,
            number=number,
        )

    def group(self, number: int):
        from zencontrol import ZenAddress, ZenAddressType

        return ZenAddress(
            controller=self.controller,
            type=ZenAddressType.GROUP,
            number=number,
        )

    def ecd(self, number: int):
        from zencontrol import ZenAddress, ZenAddressType

        return ZenAddress(
            controller=self.controller,
            type=ZenAddressType.ECD,
            number=number,
        )

    def broadcast(self):
        from zencontrol import ZenAddress

        return ZenAddress.broadcast(self.controller)

    def instance(self, ecd: int, number: int, type_code: int = 1):
        from zencontrol import ZenInstance, ZenInstanceType

        return ZenInstance(
            address=self.ecd(ecd),
            number=number,
            type=ZenInstanceType(type_code),
        )

    @property
    def port(self) -> int:
        return int(self.sim.bind_port)

    @property
    def mac(self) -> str:
        return ":".join(f"{b:02x}" for b in self.world.mac)


@pytest.fixture
async def live_sim() -> LiveSimulator:
    """Start zencontrol-simulator on an ephemeral port with a ZenTestClient facade."""
    _require_simulator()
    from zencontrol_simulator.server import Simulator
    from zencontrol_simulator.world import load_world

    from zencontrol.testing import ZenTestClient

    config = _simulator_config_path()
    world = load_world(config)
    world.bind_host = "127.0.0.1"
    world.bind_port = 0
    world.heartbeat_interval = 0  # avoid background occupancy noise

    sim = Simulator(world)
    await sim.start()
    port = sim.bind_port
    mac = ":".join(f"{b:02x}" for b in world.mac)

    protocol = ZenTestClient(unicast=True, listen_ip="127.0.0.1", listen_port=0)
    controller = protocol.ctx.controller(
        id=1,
        name="sim",
        label="Sim",
        host="127.0.0.1",
        port=port,
        mac=mac,
    )
    protocol.set_controllers([controller])

    live = LiveSimulator(world=world, sim=sim, commands=protocol, controller=controller)
    try:
        yield live
    finally:
        await protocol.aclose()
        await sim.stop()


@pytest.fixture
async def live_zen(live_sim: LiveSimulator):
    """ZenControl high-level client pointed at the running simulator."""
    from zencontrol import ZenControl

    async with ZenControl(unicast=True, listen_ip="127.0.0.1", listen_port=0) as zen:
        zen.add_controller(
            id=1,
            name="sim",
            label="Sim",
            host="127.0.0.1",
            port=live_sim.port,
            mac=live_sim.mac,
        )
        yield zen, live_sim
