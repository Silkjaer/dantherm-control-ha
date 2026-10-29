import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from homeassistant.const import UnitOfTemperature  # noqa: E402
from homeassistant.components.sensor import SensorStateClass  # noqa: E402
from hch_passivelink.sensor import ControllerStatusSensor  # noqa: E402


def _sensor():
    coordinator = SimpleNamespace(controller_state={}, controller_client=None)
    sensor = ControllerStatusSensor.__new__(ControllerStatusSensor)
    ControllerStatusSensor.__init__(sensor, coordinator, "actual_supply_air_temperature", "T2AH", UnitOfTemperature.CELSIUS, "mdi:x")
    return sensor, coordinator


def _feed(sensor, coordinator, value):
    coordinator.controller_state = {"actual_supply_air_temperature": value}
    return sensor.native_value


def test_temperature_is_a_measurement():
    sensor, _ = _sensor()
    assert sensor.state_class == SensorStateClass.MEASUREMENT


def test_lone_spike_is_dropped_and_value_rounded():
    sensor, c = _sensor()
    assert [_feed(sensor, c, v) for v in (20.31, 20.28, 19.12, 20.19)] == [20.3, 20.3, 20.3, 20.2]
    sensor, c = _sensor()
    assert [_feed(sensor, c, v) for v in (21.9, 22.04, 20.86, 21.94)] == [21.9, 22.0, 22.0, 21.9]


def test_repeated_reads_do_not_confirm_a_spike():
    sensor, c = _sensor()
    _feed(sensor, c, 20.3)
    assert _feed(sensor, c, 26.9) == 20.3
    assert sensor.native_value == 20.3 and sensor.native_value == 20.3


def test_real_step_is_accepted_after_two_samples():
    sensor, c = _sensor()
    assert [_feed(sensor, c, v) for v in (20.0, 22.5, 22.6, 22.7)] == [20.0, 20.0, 22.6, 22.7]


def test_setpoint_is_not_filtered():
    coordinator = SimpleNamespace(controller_state={"actual_afterheat_setpoint": 22}, controller_client=None)
    sensor = ControllerStatusSensor(coordinator, "actual_afterheat_setpoint", "SP", UnitOfTemperature.CELSIUS, "mdi:x")
    assert sensor.native_value == 22 and sensor.state_class is None


def test_the_room_temperature_source_is_named_for_people():
    coordinator = SimpleNamespace(controller_state={}, controller_client=None)
    sensor = ControllerStatusSensor.__new__(ControllerStatusSensor)
    ControllerStatusSensor.__init__(sensor, coordinator, "afterheat_room_source_used", "Kilde", None, "mdi:x")
    for raw, shown in (("t3", "Udsugningsluft (T3)"), ("ha_average", "Gennemsnit af HA-rum"),
                       ("room:Stue", "Stue"), (None, None)):
        coordinator.controller_state = {"afterheat_room_source_used": raw}
        assert sensor.native_value == shown
