"""Base entity for Dantherm Control."""

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PI_DIAGNOSTIC_KEYS, main_device_model, main_device_name
from .coordinator import PassiveLinkCoordinator

MAIN_DEVICE = (DOMAIN, "hch5_mk1_hac1")
# HA 2026.8 replaced DeviceInfo via_device (identifier) with via_device_id
# (registry id); older versions only know via_device.
_VIA_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__annotations__", {})


def parent_link(coordinator) -> dict:
    """Link a sub-device to the main unit device in the way this HA supports."""
    device_id = getattr(coordinator, "main_device_id", None)
    if _VIA_DEVICE_ID and device_id:
        return {"via_device_id": device_id}
    return {"via_device": MAIN_DEVICE}


def sub_model(coordinator, hac1: str, generic: str) -> str:
    """A sub-device model that names the HAC1 only on an MK1 + HAC1, not on a unit read over Modbus."""
    return generic if getattr(coordinator, "mk2_info", None) is not None else hac1


AFTERHEAT_KEYS = {
    "afterheat_setpoint",
    "afterheat_room_setpoint",
    "afterheat_extract_setpoint",
    "afterheat_supply_setpoint",
    "afterheat_temperature",
    "heating_coil_before_temperature",
    "heating_coil_after_temperature",
    "heating_coil_air_delta",
    "afterheat_active",
    "afterheat_raw",
}
INDOOR_CLIMATE_KEYS = {
    "co2",
    "room_temperature",
    "relative_humidity",
    "measured_relative_humidity",
    "air_quality",
    "air_quality_index",
}
FILTER_KEYS = {
    "filter_interval_days",
    "filter_days_remaining",
    "filter_life_percent",
    "filter_status",
    "filter_alarm",
    "filter_source",
    "filter_reset",
}
PREHEATER_KEYS = {
    "preheater_flow_temperature",
    "preheater_return_temperature",
    "preheater_water_delta",
    "preheater_activity",
    "preheater_sensor_connected",
}
PI_KEYS = set(PI_DIAGNOSTIC_KEYS)
ALARM_KEYS = {
    "extract_fan_fault", "supply_fan_fault", "outdoor_temperature_sensor_fault",
    "supply_temperature_sensor_fault", "extract_temperature_sensor_fault",
    "exhaust_temperature_sensor_fault", "room_temperature_sensor_fault",
    "outdoor_temperature_low", "supply_temperature_low", "fire_temperature_alarm",
}
# Values that only exist once an HCP4/HRC2 panel has written on the bus. With the
# Pi as master and no panel, nothing ever produces them, so they are created when
# the first value arrives instead of sitting unavailable from the start.
HCP4_KEYS = {
    "extract_fan_percent",
    "supply_fan_percent",
    "fan_control_delta",
    "afterheat_raw",
    "operating_mode",
    "current_level",
    "fireplace",
    "standby",
}
# Pi values that are reported only in some installations.
OPTIONAL_CONTROLLER_KEYS = {
    "hcp4_last_foreign_write_age",
    "actual_afterheat_valve_percent",
    # HCH5 Control 1.4.3+: the unit's filter counter and the clean-filter power reference.
    "filter_power_change_percent",
    "filter_hours_since_change",
    # HCH5 Control 1.4.8+: extract duct cooling, once an extract room is chosen.
    "extract_rooms_temperature",
    "extract_duct_loss_k",
    "extract_duct_loss_percent",
    # HCH5 Control 1.5.0+: Køl.
    "cool_boost_remaining_seconds",
}


class PassiveLinkEntity(CoordinatorEntity[PassiveLinkCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PassiveLinkCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"hch5_mk1_hac1_{key}"
        if key in INDOOR_CLIMATE_KEYS:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "hch5_mk1_hac1_indoor_climate")},
                name="Indeklima",
                manufacturer="Dantherm",
                model=sub_model(coordinator, "HAC1 indeklimasensor", "Indeklimasensor"),
                **parent_link(coordinator),
            )
        elif key in AFTERHEAT_KEYS or key in PREHEATER_KEYS:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "hch5_mk1_hac1_afterheat")},
                name="Eftervarme",
                manufacturer="Dantherm",
                model=sub_model(coordinator, "HAC1 eftervarme", "Eftervarme"),
                **parent_link(coordinator),
            )
        elif key in PI_KEYS:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "hch5_mk1_hac1_pi")},
                name="Raspberry Pi",
                manufacturer="Raspberry Pi Foundation",
                model="Dantherm Control gateway host",
                **parent_link(coordinator),
            )
        elif key in FILTER_KEYS:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "hch5_mk1_filter")},
                name="Filter",
                manufacturer="Dantherm",
                model="Filtertimer",
                **parent_link(coordinator),
            )
        elif key in ALARM_KEYS:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "hch5_mk1_hac1_alarms")},
                name="Alarmer",
                manufacturer="Dantherm",
                model="Derived fault monitoring",
                **parent_link(coordinator),
            )
        else:
            mk2_info = getattr(coordinator, "mk2_info", None)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, "hch5_mk1_hac1")},
                name=getattr(coordinator, "main_device_name", None) or main_device_name(mk2_info),
                manufacturer="Dantherm",
                model=main_device_model(mk2_info),
            )

    @property
    def available(self) -> bool:
        return self.coordinator.available and self.key in self.coordinator.data and self.coordinator.data.get(self.key) is not None

    @property
    def defer_until_data(self) -> bool:
        """Whether the entity is created only when its first value arrives."""
        return self.key in HCP4_KEYS and getattr(self.coordinator, "controller_client", None) is not None

    @property
    def has_data(self) -> bool:
        return self.coordinator.data.get(self.key) is not None


@callback
def async_add_entities_when_ready(entry, coordinator, async_add_entities, entities) -> None:
    """Add entities now, or as soon as their first value arrives when they defer."""
    pending = [entity for entity in entities if entity.defer_until_data]
    now = [entity for entity in entities if not entity.defer_until_data]
    if now:
        async_add_entities(now)
    if not pending:
        return

    @callback
    def _add_ready() -> None:
        ready = [entity for entity in pending if entity.has_data]
        if not ready:
            return
        for entity in ready:
            pending.remove(entity)
        async_add_entities(ready)

    entry.async_on_unload(coordinator.async_add_listener(_add_ready))
    _add_ready()
