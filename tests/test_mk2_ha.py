"""End-to-end: set up the integration against the MKII simulator and drive it from Home Assistant.

Needs pytest-homeassistant-custom-component; skipped in the light-weight CI job.
"""

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

sys.path.insert(0, str(Path(__file__).parents[1]))  # custom_components/
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))
import mk2  # noqa: E402
from mk2_sim import Mk2Simulator  # noqa: E402

DOMAIN = "hch_passivelink"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    import custom_components

    ours = str(Path(__file__).parents[1] / "custom_components")
    if ours not in list(custom_components.__path__):
        custom_components.__path__.append(ours)  # the test package ships its own custom_components
    yield


@pytest.fixture
def sim(socket_enabled):
    simulator = Mk2Simulator()
    server, port = simulator.serve_tcp()
    simulator.port = port
    yield simulator
    server.shutdown()


def _entity(hass, domain: str, unique_suffix: str):
    for entry in er.async_get(hass).entities.values():
        if entry.platform == DOMAIN and entry.domain == domain and entry.unique_id == f"hch5_mk1_hac1_{unique_suffix}":
            return entry.entity_id
    raise AssertionError(f"no {domain} {unique_suffix}")


async def _until(hass, check, timeout: float = 8.0):
    for _ in range(int(timeout / 0.1)):
        await hass.async_block_till_done()
        if check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not reached")


async def test_mk2_setup_entities_and_controls(hass, sim):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"connection_type": "mk2_tcp"})
    assert result["step_id"] == "mk2"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": sim.port, "unit_id": 1}
    )
    assert result["type"] == "create_entry", result
    assert "HCH 5 MKII" in result["title"]
    await _until(hass, lambda: hass.states.get(_entity(hass, "select", "mk2_operation")) is not None)

    operation = _entity(hass, "select", "mk2_operation")
    level = _entity(hass, "select", "mk2_fan_level")
    await _until(hass, lambda: hass.states.get(operation).state == "automatic")
    assert hass.states.get(level).state == "2"
    assert float(hass.states.get(_entity(hass, "sensor", "outdoor_temperature")).state) == 6.5
    await _until(hass, lambda: hass.states.get(_entity(hass, "sensor", "alarm")).state == "none")
    # MK1-only values get no entities on an MKII.
    uids = {e.unique_id for e in er.async_get(hass).entities.values() if e.platform == DOMAIN}
    assert "hch5_mk1_hac1_afterheat_setpoint" not in uids and "hch5_mk1_hac1_co2" not in uids
    assert "hch5_mk1_hac1_hac1_connected" not in uids

    await hass.services.async_call("select", "select_option", {"entity_id": operation, "option": "fireplace"}, blocking=True)
    await _until(hass, lambda: hass.states.get(operation).state == "fireplace")
    assert sim.u32(mk2.REG_CURRENT_MODE) == mk2.MODE_FIREPLACE

    await hass.services.async_call("select", "select_option", {"entity_id": level, "option": "4"}, blocking=True)
    await _until(hass, lambda: hass.states.get(level).state == "4")
    assert hass.states.get(operation).state == "manual"

    # The switch follows the manual-bypass selection at once; the damper takes minutes to travel.
    sim.bypass_travel_ticks = 1000  # never arrives during the test
    bypass = _entity(hass, "switch", "mk2_bypass")
    await hass.services.async_call("switch", "turn_on", {"entity_id": bypass}, blocking=True)
    await _until(hass, lambda: hass.states.get(bypass).state == "on")
    assert hass.states.get(bypass).attributes["damper"] == "opening"
    assert sim.u32(mk2.REG_BYPASS_DAMPER) == mk2.BYPASS_OPENING
    await hass.services.async_call("switch", "turn_off", {"entity_id": bypass}, blocking=True)
    await _until(hass, lambda: hass.states.get(bypass).state == "off")
    assert hass.states.get(bypass).attributes["damper"] == "opening"  # still finishing its travel
    commands = [mk2.regs_to_u32(v) for a, v in sim.writes if a == mk2.REG_ACTIVE_MODE]
    assert commands[-2:] == [mk2.CMD_BYPASS_OPEN, mk2.CMD_BYPASS_CLOSE]
    # Units that do not report the active mode: the switch shows the damper instead.
    sim.failing_registers.add(mk2.REG_ACTIVE_MODE)
    writes = len(sim.writes)
    await hass.services.async_call("switch", "turn_on", {"entity_id": bypass}, blocking=True)
    await _until(hass, lambda: hass.states.get(bypass).state == "on")
    assert len(sim.writes) == writes  # the damper is already opening: nothing to send
    sim.failing_registers.clear()

    sim.set_u32(mk2.REG_FILTER_REMAIN, 0)
    sim.set_u32(mk2.REG_ALARM, 2)
    alarm = _entity(hass, "sensor", "alarm")
    await hass.services.async_call("button", "press", {"entity_id": _entity(hass, "button", "mk2_alarm_reset")}, blocking=True)
    await _until(hass, lambda: sim.u32(mk2.REG_ALARM) == 0 and hass.states.get(alarm).state == "none")
    await hass.services.async_call("button", "press", {"entity_id": _entity(hass, "button", "filter_reset")}, blocking=True)
    remaining = _entity(hass, "sensor", "filter_days_remaining")
    await _until(hass, lambda: hass.states.get(remaining).state == "180")
    assert sim.u32(mk2.REG_FILTER_REMAIN) == 180


async def test_mk2_unreachable_shows_cannot_connect(hass, socket_enabled):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"connection_type": "mk2_tcp"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "127.0.0.1", "port": 1, "unit_id": 1})
    assert result["type"] == "form" and result["errors"] == {"base": "cannot_connect"}
