import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink.smart_coordinator import SmartPassiveLinkCoordinator  # noqa: E402
from alarms import derive_alarm_values  # noqa: E402

STOPPED = {"extract_fan_rpm": 0, "supply_fan_rpm": 0}
PI_COMMAND = {"actual_fan_extract_percent": 100, "actual_fan_supply_percent": 94}


def _alarms(data, controller_state, connected=True):
    fake = SimpleNamespace(
        controller_client=SimpleNamespace(connected=connected),
        controller_state=controller_state,
    )
    return derive_alarm_values(SmartPassiveLinkCoordinator._alarm_inputs(fake, data))


def test_stopped_fans_raise_the_alarm_under_the_pi():
    alarms = _alarms(STOPPED, PI_COMMAND)
    assert alarms["extract_fan_fault"] is True
    assert alarms["supply_fan_fault"] is True


def test_running_or_idle_fans_raise_no_alarm():
    assert _alarms({"extract_fan_rpm": 2945, "supply_fan_rpm": 2829}, PI_COMMAND)["extract_fan_fault"] is False
    idle = {"actual_fan_extract_percent": 0, "actual_fan_supply_percent": 0}
    assert _alarms(STOPPED, idle)["supply_fan_fault"] is False


def test_the_bus_command_wins_and_a_lost_controller_adds_nothing():
    assert _alarms(dict(STOPPED, extract_fan_percent=0), PI_COMMAND)["extract_fan_fault"] is False
    assert _alarms(STOPPED, PI_COMMAND, connected=False)["extract_fan_fault"] is False
