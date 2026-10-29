import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink import binary_sensor, climate, sensor  # noqa: E402
from hch_passivelink.entity import HAC1_KEYS, HCP4_KEYS, OPTIONAL_CONTROLLER_KEYS  # noqa: E402

PASSIVE_SENSOR_KEYS = {
    "extract_fan_percent", "supply_fan_percent", "fan_control_delta",
    "afterheat_raw", "operating_mode", "current_level",
}
PASSIVE_BINARY_KEYS = {"fireplace", "standby"}


class Coordinator:
    def __init__(self, *, controller: bool = True, hac1_seen: bool = True):
        self.available = True
        self.hac1_seen = hac1_seen
        self.client = None  # an MK1 bus client; mk2_entities checks its type
        self.data = {}
        self.controller_state = {}
        self.controller_client = SimpleNamespace(connected=True) if controller else None
        self._auxiliary_client = None
        self.listeners = []

    def async_add_listener(self, update_callback, context=None):
        self.listeners.append(update_callback)
        return lambda: self.listeners.remove(update_callback)

    def publish(self, **values):
        self.data = {**self.data, **values}
        self._notify()

    def publish_controller(self, **values):
        self.controller_state = {**self.controller_state, **values}
        self._notify()

    def _notify(self):
        for update_callback in list(self.listeners):
            update_callback()


def setup_platform(module, coordinator):
    unloaders = []
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=unloaders.append)
    batches = []
    asyncio.run(module.async_setup_entry(None, entry, lambda entities: batches.append(list(entities))))
    return batches, unloaders


def keys(batches):
    return [entity.key for batch in batches for entity in batch]


def test_hcp4_sensors_wait_for_first_value_when_pi_is_master():
    coordinator = Coordinator()
    batches, unloaders = setup_platform(sensor, coordinator)
    created = keys(batches)
    assert not PASSIVE_SENSOR_KEYS & set(created)
    assert not OPTIONAL_CONTROLLER_KEYS & set(created)
    assert "outdoor_temperature" in created and "active_master" in created

    coordinator.publish(extract_fan_percent=55, supply_fan_percent=43, fan_control_delta=-12.0)
    created = keys(batches)
    assert {"extract_fan_percent", "supply_fan_percent", "fan_control_delta"} <= set(created)
    assert not {"afterheat_raw", "operating_mode", "current_level"} & set(created)

    coordinator.publish(afterheat_raw=0, operating_mode="manual_2", current_level="level_2")
    coordinator.publish(extract_fan_percent=25, supply_fan_percent=13)
    created = keys(batches)
    assert PASSIVE_SENSOR_KEYS <= set(created)
    assert len(created) == len(set(created))
    assert len(unloaders) == 1


def test_hcp4_binary_sensors_wait_for_first_value_when_pi_is_master():
    coordinator = Coordinator()
    batches, _ = setup_platform(binary_sensor, coordinator)
    created = keys(batches)
    assert not PASSIVE_BINARY_KEYS & set(created)
    assert {"night_mode", "bypass_active", "hcp4_detected"} <= set(created)

    coordinator.publish(fireplace=False, standby=False)
    created = keys(batches)
    assert PASSIVE_BINARY_KEYS <= set(created)
    assert len(created) == len(set(created))


def test_values_already_present_at_setup_are_added_at_once():
    coordinator = Coordinator()
    coordinator.data = {"extract_fan_percent": 85, "supply_fan_percent": 73}
    batches, _ = setup_platform(sensor, coordinator)
    created = keys(batches)
    assert {"extract_fan_percent", "supply_fan_percent"} <= set(created)
    assert not {"fan_control_delta", "operating_mode"} & set(created)


def test_without_pi_everything_is_created_at_setup():
    coordinator = Coordinator(controller=False)
    sensors, unloaders = setup_platform(sensor, coordinator)
    binaries, _ = setup_platform(binary_sensor, coordinator)
    assert PASSIVE_SENSOR_KEYS <= set(keys(sensors))
    assert PASSIVE_BINARY_KEYS <= set(keys(binaries))
    assert unloaders == []
    assert not coordinator.listeners


def test_pi_optional_sensors_wait_for_a_reported_value():
    coordinator = Coordinator()
    batches, _ = setup_platform(sensor, coordinator)
    assert not OPTIONAL_CONTROLLER_KEYS & set(keys(batches))

    coordinator.publish_controller(hcp4_last_foreign_write_age=None, actual_afterheat_valve_percent=None)
    assert not OPTIONAL_CONTROLLER_KEYS & set(keys(batches))

    coordinator.publish_controller(actual_afterheat_valve_percent=40)
    created = keys(batches)
    assert "actual_afterheat_valve_percent" in created
    assert "hcp4_last_foreign_write_age" not in created

    coordinator.publish_controller(hcp4_last_foreign_write_age=12)
    created = keys(batches)
    assert "hcp4_last_foreign_write_age" in created
    # Filter values from HCH5 Control 1.4.3 arrive later on older Pis.
    coordinator.publish_controller(filter_power_change_percent=-1.5, filter_hours_since_change=2,
                                   extract_rooms_temperature=23.0, extract_duct_loss_k=3.0,
                                   extract_duct_loss_percent=23.1, cool_boost_remaining_seconds=0)
    created = keys(batches)
    assert OPTIONAL_CONTROLLER_KEYS <= set(created)
    assert len(created) == len(set(created))


def test_listener_is_removed_on_unload_and_stops_adding():
    coordinator = Coordinator()
    batches, unloaders = setup_platform(sensor, coordinator)
    assert len(coordinator.listeners) == 1
    unloaders[0]()
    assert not coordinator.listeners
    coordinator.publish(extract_fan_percent=55)
    assert "extract_fan_percent" not in keys(batches)


def test_panel_command_waits_for_a_command_when_pi_is_master():
    coordinator = Coordinator()
    coordinator.client = None  # an MK1 bus client
    batches, _ = setup_platform(sensor, coordinator)
    assert "command_raw" not in keys(batches)
    coordinator.publish(command_raw=172)
    assert "command_raw" in keys(batches)


def test_fan_levels_the_pi_has_no_profile_for_are_not_created():
    from hch_passivelink import number

    coordinator = Coordinator()
    batches, _ = setup_platform(number, coordinator)
    assert not [key for key in keys(batches) if key.startswith("level_")]
    assert "rh_setpoint" in keys(batches)

    # Four Dantherm steps: the Pi reports profiles for levels 1-4 only.
    coordinator.publish_controller(profiles={str(level): {"extract": 50, "supply": 40} for level in range(1, 5)})
    created = keys(batches)
    assert {f"level_{level}_{kind}" for level in range(1, 5) for kind in ("extract", "supply")} <= set(created)
    assert not {"level_5_extract", "level_5_supply", "level_6_extract", "level_6_supply"} & set(created)
    assert len(created) == len(set(created))


def standby_sensor(state):
    coordinator = Coordinator()
    coordinator.controller_state = state
    return sensor.ControllerStatusSensor(coordinator, "standby_remaining_seconds", "x", "s", "mdi:power-sleep")


def test_standby_remaining_is_zero_while_the_unit_is_on():
    assert standby_sensor({"standby_active": False, "standby_remaining_seconds": None}).native_value == 0
    assert standby_sensor({"standby_active": False, "standby_remaining_seconds": 0}).native_value == 0


def test_standby_remaining_keeps_the_pi_value_when_it_is_off():
    assert standby_sensor({"standby_active": True, "standby_remaining_seconds": 120}).native_value == 120
    assert standby_sensor({"standby_active": True, "standby_remaining_seconds": None}).native_value is None
    assert standby_sensor({"standby_remaining_seconds": None}).native_value is None


def test_hcp4_key_sets_match_the_declared_descriptions():
    described = {description.key for description in sensor.DESCRIPTIONS}
    described |= {description.key for description in binary_sensor.DESCRIPTIONS}
    assert HCP4_KEYS <= described
    assert OPTIONAL_CONTROLLER_KEYS <= {spec[0] for spec in sensor.CONTROLLER_SENSOR_SPECS}


def test_hac1_entities_are_not_created_on_a_unit_without_hac1():
    # HCP4 reports register 146 = 0 on a unit without HAC1.
    coordinator = Coordinator(controller=False, hac1_seen=False)
    sensors, _ = setup_platform(sensor, coordinator)
    binaries, _ = setup_platform(binary_sensor, coordinator)
    created = set(keys(sensors)) | set(keys(binaries))
    assert not HAC1_KEYS & created
    assert {"outdoor_temperature", "room_temperature", "relative_humidity"} <= created

    coordinator.publish(hac1_connected=False, room_temperature=26.6)
    created = set(keys(sensors)) | set(keys(binaries))
    assert not HAC1_KEYS & created


def test_hac1_entities_appear_once_a_hac1_is_seen():
    coordinator = Coordinator(controller=False, hac1_seen=False)
    sensors, _ = setup_platform(sensor, coordinator)
    binaries, _ = setup_platform(binary_sensor, coordinator)

    coordinator.hac1_seen = True
    coordinator.publish(hac1_connected=True, co2=650, air_quality_index="good")
    created = keys(sensors) + keys(binaries)
    assert {"co2", "air_quality_index", "hac1_connected"} <= set(created)
    assert len(created) == len(set(created))


def test_afterheat_thermostat_waits_for_a_hac1():
    coordinator = Coordinator(hac1_seen=False)
    batches, _ = setup_platform(climate, coordinator)
    assert keys(batches) == []

    coordinator.publish(hac1_connected=False)
    assert keys(batches) == []

    coordinator.hac1_seen = True
    coordinator.publish_controller(afterheat_setpoint=20)
    assert keys(batches) == ["afterheat_setpoint"]


def test_afterheat_thermostat_is_created_at_once_with_a_known_hac1():
    coordinator = Coordinator()
    coordinator.controller_state = {"afterheat_setpoint": 20}
    batches, _ = setup_platform(climate, coordinator)
    assert keys(batches) == ["afterheat_setpoint"]


CONTROLLER_AFTERHEAT_SENSORS = {
    "actual_afterheat_setpoint", "actual_afterheat_selection", "afterheat_lift", "afterheat_power_w",
    "afterheat_energy_today_kwh", "actual_afterheat_frost_temperature", "actual_afterheat_outdoor_lockout",
}


def test_controller_afterheat_sensors_wait_for_a_hac1():
    coordinator = Coordinator(hac1_seen=False)
    values = {key: 1 for key in CONTROLLER_AFTERHEAT_SENSORS | {"actual_afterheat", "afterheat_energy_kwh"}}
    coordinator.controller_state = dict(values)
    sensors, _ = setup_platform(sensor, coordinator)
    binaries, _ = setup_platform(binary_sensor, coordinator)
    created = set(keys(sensors)) | set(keys(binaries))
    assert not (CONTROLLER_AFTERHEAT_SENSORS | {"actual_afterheat", "afterheat_energy_kwh"}) & created
    assert "effective_level" in created

    coordinator.hac1_seen = True
    coordinator.publish_controller(**values)
    created = set(keys(sensors)) | set(keys(binaries))
    assert CONTROLLER_AFTERHEAT_SENSORS | {"actual_afterheat", "afterheat_energy_kwh"} <= created
