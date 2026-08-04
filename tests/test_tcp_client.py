"""TCP command-plane client tests against zencontrol-simulator."""

from __future__ import annotations

import asyncio

import pytest

from zencontrol import ZenControl, ZenTcColour, ZenTcpClient
from zencontrol.api.commands import CMD
from zencontrol.io import ZenRequest, ZenRequestType, ZenResponseType

pytestmark = pytest.mark.simulator


@pytest.fixture
async def tcp_sim(live_sim):
    """Rebind the live_sim facade to use TCP for commands."""
    live_sim.ctrl.tcp = True
    await live_sim.commands._invalidate_client(live_sim.ctrl)
    return live_sim


@pytest.mark.asyncio
async def test_tcp_client_query_label(tcp_sim) -> None:
    client = await ZenTcpClient.create(("127.0.0.1", tcp_sim.port))
    try:
        resp = await client.send_request_with_retries(
            ZenRequest(command=CMD.QUERY_CONTROLLER_LABEL, data=[0x00], request_type=ZenRequestType.BASIC)
        )
        assert resp.response_type is ZenResponseType.ANSWER
        assert resp.data is not None
        assert len(resp.data) > 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_tcp_command_client_arc_and_colour(tcp_sim) -> None:
    p = tcp_sim.commands
    await p.dali_arc_level(tcp_sim.ecg(1), 88)
    assert tcp_sim.world.lights[1].level == 88

    tc = ZenTcColour(kelvin=4100)
    assert await p.dali_colour(tcp_sim.ecg(0), tc) is True
    queried = await p.query_dali_colour(tcp_sim.ecg(0))
    assert isinstance(queried, ZenTcColour)
    assert queried.kelvin == 4100


@pytest.mark.asyncio
async def test_tcp_add_controller_option(live_sim) -> None:
    async with ZenControl(listen_ip="127.0.0.1", listen_port=0) as zen:
        ctrl = zen.add_controller(
            id=1,
            name="tcp-sim",
            label="TCP Sim",
            host="127.0.0.1",
            port=live_sim.port,
            mac=live_sim.mac,
            tcp=True,
            unicast=True,
        )
        assert ctrl.tcp is True
        assert await zen.commands.query_controller_label(ctrl) is not None
        assert isinstance(zen.commands.client_for(ctrl), ZenTcpClient)


@pytest.mark.asyncio
async def test_tcp_default_retries_is_zero() -> None:
    async def _silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(64)
            await asyncio.sleep(10)
        finally:
            writer.close()

    server = await asyncio.start_server(_silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = await ZenTcpClient.create(("127.0.0.1", port))
    try:
        resp = await client.send_request(
            ZenRequest(command=0x24, data=[0x00], request_type=ZenRequestType.BASIC),
            timeout=0.2,
        )
        assert resp.response_type is ZenResponseType.TIMEOUT
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
