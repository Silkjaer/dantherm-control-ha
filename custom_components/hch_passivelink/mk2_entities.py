"""Direct controls for an HCH5 MKII (used when it is connected over Modbus TCP without the Pi)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from .client import Mk2Client
from .entity import AFTERHEAT_KEYS, PassiveLinkEntity
from .mk2 import ALARMS, OPERATIONS

# Values only the MK1/HAC1 bus carries; an MKII never fills them, so no entity is made.
# (The derived fan/sensor fault flags are computed from the readings and stay.)
MK1_ONLY_KEYS = AFTERHEAT_KEYS | {
    "co2", "measured_relative_humidity", "extract_fan_percent", "supply_fan_percent",
    "fan_control_delta", "command_raw", "hac1_connected",
}


def is_mk2(coordinator) -> bool:
    return isinstance(coordinator.client, Mk2Client)


def drop_mk1_only(coordinator, entities: list) -> list:
    """On an MKII, leave out the entities that would stay unavailable forever."""
    if not is_mk2(coordinator):
        return entities
    return [entity for entity in entities if getattr(entity, "key", None) not in MK1_ONLY_KEYS]


def is_direct_mk2(coordinator) -> bool:
    """MKII connected directly: controls go to the unit, not through the Pi."""
    return isinstance(coordinator.client, Mk2Client) and getattr(coordinator, "controller_client", None) is None


class _Mk2Entity(PassiveLinkEntity):
    async def _command(self, method: str, *args) -> None:
        await self.coordinator.client.async_command(method, *args)


class Mk2OperationSelect(_Mk2Entity, SelectEntity):
    """Standby, manual, automatic, week program, away, summer or fireplace."""

    _attr_translation_key = "mk2_operation"
    _attr_icon = "mdi:hvac"
    _attr_options = list(OPERATIONS)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mk2_operation")

    @property
    def available(self) -> bool:
        return self.coordinator.available and "mk2_mode" in self.coordinator.data

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.data.get("mk2_mode")
        return mode if mode in OPERATIONS else None

    async def async_select_option(self, option: str) -> None:
        await self._command("set_operation", option)


class Mk2LevelSelect(_Mk2Entity, SelectEntity):
    """Fan step 0 (standby) to 4; choosing one puts the unit in manual."""

    _attr_translation_key = "mk2_fan_level"
    _attr_icon = "mdi:fan"
    _attr_options = ["0", "1", "2", "3", "4"]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mk2_fan_level")

    @property
    def available(self) -> bool:
        return self.coordinator.available and "mk2_level" in self.coordinator.data

    @property
    def current_option(self) -> str | None:
        level = self.coordinator.data.get("mk2_level")
        return str(level) if isinstance(level, int) and 0 <= level <= 4 else None

    async def async_select_option(self, option: str) -> None:
        await self._command("set_level", int(option))


class Mk2BypassSwitch(_Mk2Entity, SwitchEntity):
    """Manual bypass: open the damper now (the unit closes it again after its own timeout).

    On/off follows whether manual bypass is selected on the unit, which changes at
    once; the damper needs a minute or two to travel and is shown as an attribute.
    """

    _attr_translation_key = "mk2_bypass"
    _attr_icon = "mdi:valve"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mk2_bypass")

    @property
    def available(self) -> bool:
        return self.coordinator.available and "bypass_raw" in self.coordinator.data

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if "manual_bypass" in data:
            return data["manual_bypass"]
        return data.get("bypass_active")  # units that do not report the active mode

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"damper": self.coordinator.data.get("bypass_state")}

    async def async_turn_on(self, **kwargs) -> None:
        await self._command("set_bypass", True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._command("set_bypass", False)


class Mk2AlarmResetButton(_Mk2Entity, ButtonEntity):
    _attr_translation_key = "mk2_alarm_reset"
    _attr_icon = "mdi:restore-alert"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mk2_alarm_reset")

    @property
    def available(self) -> bool:
        return self.coordinator.available

    async def async_press(self) -> None:
        await self._command("reset_alarm")


class Mk2FilterResetButton(_Mk2Entity, ButtonEntity):
    """Reset the unit's own filter counter after a filter change."""

    _attr_translation_key = "filter_reset"
    _attr_icon = "mdi:air-filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "filter_reset")

    @property
    def available(self) -> bool:
        return self.coordinator.available

    async def async_press(self) -> None:
        await self._command("reset_filter")


class Mk2AlarmSensor(_Mk2Entity, SensorEntity):
    _attr_translation_key = "mk2_alarm"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(dict.fromkeys(ALARMS.values()))

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "alarm")

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("alarm")
        return value if value in self._attr_options else None


class Mk2OperatingHoursSensor(_Mk2Entity, SensorEntity):
    _attr_translation_key = "operating_hours"
    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "operating_hours")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("operating_hours")
