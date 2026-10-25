import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))
sys.path.insert(0, str(Path(__file__).parent))

import mk2  # noqa: E402
from mk2_sim import FakeSerial, Mk2Simulator  # noqa: E402


@pytest.fixture
def sim_tcp():
    sim = Mk2Simulator()
    server, port = sim.serve_tcp()
    unit = mk2.Mk2Unit(mk2.ModbusTcp("127.0.0.1", port, timeout=2))
    yield sim, unit
    unit.transport.close()
    server.shutdown()


def test_codec_roundtrip():
    assert mk2.regs_to_u32(mk2.u32_to_regs(0x12345678)) == 0x12345678
    assert mk2.u32_to_regs(0x00010002) == [2, 1]  # low word first
    assert abs(mk2.regs_to_f32(mk2.f32_to_regs(-7.25)) + 7.25) < 1e-6


def test_identify_and_snapshot_over_tcp(sim_tcp):
    sim, unit = sim_tcp
    info = unit.identify()
    assert info.model == "HCH 5 MKII" and info.firmware == "2.74" and info.serial_number == 123456
    assert info.has_bypass and info.has_humidity and info.has_air_quality
    data = unit.snapshot()
    assert data["unit_generation"] == "mk2"
    assert data["outdoor_temperature"] == 6.5 and data["extract_temperature"] == 22.1
    assert data["supply_fan_rpm"] == 1420 and data["extract_fan_rpm"] == 1515
    assert data["mk2_level"] == 2 and data["current_level"] == "level_2" and data["mk2_mode"] == "automatic"
    assert data["operating_mode"] == "auto_or_scheduled"
    assert data["bypass_active"] is False and data["bypass_raw"] == 0
    assert data["relative_humidity"] == 46 and data["air_quality_index"] == "good" and data["air_quality_raw"] == 650
    assert data["filter_days_remaining"] == 45 and data["filter_life_percent"] == 25
    assert data["alarm"] == "none" and data["hac1_connected"] is True


def test_level_switches_to_manual_and_standby(sim_tcp):
    sim, unit = sim_tcp
    unit.set_level(4)
    assert sim.u32(mk2.REG_CURRENT_MODE) == mk2.MODE_MANUAL and sim.u32(mk2.REG_FAN_LEVEL) == 4
    assert (mk2.REG_ACTIVE_MODE, mk2.u32_to_regs(mk2.CMD_MANUAL)) in sim.writes
    unit.set_level(9)  # clamped
    assert sim.u32(mk2.REG_FAN_LEVEL) == 4
    unit.set_operation("standby")
    data = unit.snapshot()
    assert data["standby"] is True and data["current_level"] == "off" and data["operating_mode"] == "standby"


def test_special_modes_end_before_the_next(sim_tcp):
    sim, unit = sim_tcp
    unit.set_fireplace(True)
    assert unit.snapshot()["fireplace"] is True
    assert unit.snapshot()["operating_mode"] == "fireplace"
    unit.set_operation("automatic")
    commands = [mk2.regs_to_u32(v) for a, v in sim.writes if a == mk2.REG_ACTIVE_MODE]
    assert commands[-2:] == [mk2.CMD_END_FIREPLACE, mk2.CMD_AUTOMATIC]
    assert unit.snapshot()["mk2_mode"] == "automatic"
    unit.set_away(True)
    assert unit.snapshot()["away"] is True
    unit.set_away(False)
    assert unit.snapshot()["mk2_mode"] == "automatic"
    with pytest.raises(ValueError):
        unit.set_operation("party")


def test_bypass_is_only_toggled_when_needed(sim_tcp):
    sim, unit = sim_tcp
    unit.set_bypass(True)
    assert unit.snapshot()["bypass_active"] is True and unit.snapshot()["bypass_raw"] == 255
    before = len(sim.writes)
    unit.set_bypass(True)  # already open: no second toggle
    assert len(sim.writes) == before
    unit.set_bypass(False)
    assert unit.snapshot()["bypass_active"] is False


def _bypass_commands(sim):
    return [mk2.regs_to_u32(v) for a, v in sim.writes if a == mk2.REG_ACTIVE_MODE]


@pytest.fixture
def sim_travel(sim_tcp):
    sim, unit = sim_tcp
    sim.bypass_travel_reads = 3  # the damper reports OPENING/CLOSING for three reads
    return sim, unit


def test_bypass_on_shows_selected_while_the_damper_opens(sim_travel):
    sim, unit = sim_travel
    assert unit.snapshot()["manual_bypass"] is False
    unit.set_bypass(True)
    data = unit.snapshot()
    assert data["manual_bypass"] is True and data["bypass_state"] == "opening"
    assert data["bypass_raw"] == mk2.BYPASS_OPENING and data["bypass_active"] is True
    for _ in range(3):
        data = unit.snapshot()
    assert data["bypass_state"] == "open" and data["manual_bypass"] is True
    assert _bypass_commands(sim) == [mk2.CMD_BYPASS_OPEN]


def test_bypass_off_while_opening_deselects_at_once(sim_travel):
    sim, unit = sim_travel
    unit.set_bypass(True)
    assert unit.snapshot()["bypass_state"] == "opening"
    unit.set_bypass(False)
    data = unit.snapshot()
    assert data["manual_bypass"] is False  # off at once, although the damper is still travelling
    assert data["bypass_state"] == "opening" and data["bypass_active"] is True
    unit.set_bypass(False)  # already deselected: nothing more is sent
    assert _bypass_commands(sim) == [mk2.CMD_BYPASS_OPEN, mk2.CMD_BYPASS_CLOSE]
    states = [unit.snapshot()["bypass_state"] for _ in range(10)]
    assert "closing" in states and states[-1] == "closed"
    data = unit.snapshot()
    assert data["bypass_active"] is False and data["manual_bypass"] is False
    assert _bypass_commands(sim).count(mk2.CMD_BYPASS_CLOSE) == 1


def test_bypass_follows_the_selection_not_the_damper(sim_tcp):
    sim, unit = sim_tcp
    # Selected, but the damper is still closed: nothing to send for "on".
    sim.set_u32(mk2.REG_ACTIVE_MODE, mk2.CMD_AUTOMATIC | mk2.ACTIVE_MANUAL_BYPASS)
    unit.set_bypass(True)
    assert sim.writes == []
    unit.set_bypass(False)
    assert _bypass_commands(sim) == [mk2.CMD_BYPASS_CLOSE]
    # The unit's own (automatic) bypass is open, manual bypass not selected.
    sim.writes.clear()
    sim.set_u32(mk2.REG_ACTIVE_MODE, mk2.CMD_AUTOMATIC)
    sim.set_u32(mk2.REG_BYPASS_DAMPER, mk2.BYPASS_OPENED)
    unit.set_bypass(False)
    assert sim.writes == []
    unit.set_bypass(True)
    assert _bypass_commands(sim) == [mk2.CMD_BYPASS_OPEN]


def test_bypass_falls_back_to_the_damper_without_active_mode(sim_tcp):
    sim, unit = sim_tcp
    sim.failing_registers.add(mk2.REG_ACTIVE_MODE)
    data = unit.snapshot()
    assert "manual_bypass" not in data and data["bypass_state"] == "closed"
    unit.set_bypass(True)
    assert _bypass_commands(sim) == [mk2.CMD_BYPASS_OPEN]
    assert unit.snapshot()["bypass_active"] is True
    unit.set_bypass(True)  # damper already open: no second toggle
    unit.set_bypass(False)
    assert _bypass_commands(sim) == [mk2.CMD_BYPASS_OPEN, mk2.CMD_BYPASS_CLOSE]
    assert unit.snapshot()["bypass_active"] is False


def test_bypass_raw_and_active_keep_their_meaning(sim_tcp):
    sim, unit = sim_tcp
    for raw, state, active in (
        (mk2.BYPASS_CLOSED, "closed", False), (mk2.BYPASS_IN_PROGRESS, "in_progress", False),
        (mk2.BYPASS_CLOSING, "closing", False), (mk2.BYPASS_OPENING, "opening", True),
        (mk2.BYPASS_OPENED, "open", True), (7, "raw_7", False),
    ):
        sim.set_u32(mk2.REG_BYPASS_DAMPER, raw)
        data = unit.snapshot()
        assert (data["bypass_raw"], data["bypass_state"], data["bypass_active"]) == (raw, state, active)


def test_filter_and_alarm_reset(sim_tcp):
    sim, unit = sim_tcp
    sim.set_u32(mk2.REG_FILTER_REMAIN, 0)
    sim.set_u32(mk2.REG_ALARM, 2)
    data = unit.snapshot()
    assert data["filter_alarm"] is True and data["alarm"] == "supply_air_fan" and data["status_code"] == 2
    unit.reset_filter()
    unit.reset_alarm()
    data = unit.snapshot()
    assert data["filter_days_remaining"] == 180 and data["alarm"] == "none"


def test_rtu_transport_and_errors():
    sim = Mk2Simulator()
    unit = mk2.Mk2Unit(mk2.ModbusRtu(FakeSerial(sim), unit_id=1, timeout=0.2))
    assert unit.identify().model == "HCH 5 MKII"
    unit.set_level(1)
    assert sim.u32(mk2.REG_FAN_LEVEL) == 1
    with pytest.raises(mk2.ModbusError):
        unit.transport.read(9000, 2)  # illegal address -> exception response
    silent = mk2.Mk2Unit(mk2.ModbusRtu(FakeSerial(sim, unit_id=7), unit_id=1, timeout=0.05))
    with pytest.raises(mk2.ModbusError):
        silent.identify()


def test_tcp_unreachable_raises_modbus_error():
    unit = mk2.Mk2Unit(mk2.ModbusTcp("127.0.0.1", 1, timeout=0.5))
    with pytest.raises(mk2.ModbusError):
        unit.identify()


def test_level_from_percent_follows_controller_steps():
    steps = [20, 35, 50, 65, 80, 100]  # six controller levels
    assert mk2.level_from_percent(0, steps) == 0
    assert mk2.level_from_percent(20, steps) == 1
    assert mk2.level_from_percent(100, steps) == 4
    assert mk2.level_from_percent(52, steps) in (2, 3)
    assert mk2.level_from_percent(60, []) == 2
