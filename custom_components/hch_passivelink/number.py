"""Number entities for Raspberry Pi HCH controller configuration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller_entity import ControllerEntity
from .entity import async_add_entities_when_ready


@dataclass(frozen=True)
class NumberSpec:
    key: str
    name: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None
    icon: str | None = None


SPECS = (
    NumberSpec("rh_setpoint", "RH setpunkt", 25, 80, 1, PERCENTAGE, "mdi:water-percent"),
    NumberSpec("rh_hysteresis", "RH hysterese", 1, 10, 1, PERCENTAGE, "mdi:arrow-expand-vertical"),
    # Bathrooms: drying starts above this RH (or on a fast rise) and runs up to the drying step.
    NumberSpec("bathroom_rh_setpoint", "Badeværelse RH start", 35, 90, 1, PERCENTAGE, "mdi:shower"),
    NumberSpec("bathroom_rh_hysteresis", "Badeværelse RH hysterese", 1, 20, 1, PERCENTAGE, "mdi:arrow-expand-vertical"),
    NumberSpec("bathroom_max_level", "Badeværelse udtørringstrin", 1, 6, 1, None, "mdi:fan-plus"),
    NumberSpec("co2_setpoint", "CO₂ setpunkt", 500, 2000, 50, "ppm", "mdi:molecule-co2"),
    NumberSpec("co2_hysteresis", "CO₂ hysterese", 25, 500, 25, "ppm", "mdi:arrow-expand-vertical"),
    NumberSpec("auto_step_rh", "RH pr. ventilationstrin", 2, 20, 1, PERCENTAGE, "mdi:stairs-up"),
    NumberSpec("auto_step_co2", "CO₂ pr. ventilationstrin", 50, 1000, 50, "ppm", "mdi:stairs-up"),
    NumberSpec("local_normal_level", "Auto normalniveau", 1, 6, 1, None, "mdi:fan-auto"),
    NumberSpec("local_min_level", "Auto minimumsniveau", 1, 6, 1, None, "mdi:fan-minus"),
    NumberSpec("local_max_level", "Auto maksimumsniveau", 1, 6, 1, None, "mdi:fan-plus"),
    NumberSpec("vacation_level", "Ferie trin", 1, 6, 1, None, "mdi:palm-tree"),
    NumberSpec("downshift_delay_seconds", "Nedreguleringsforsinkelse", 30, 3600, 30, UnitOfTime.SECONDS, "mdi:timer-sand"),
    NumberSpec("boost_hold_seconds", "Boost holdetid", 60, 3600, 60, UnitOfTime.SECONDS, "mdi:timer-outline"),
    NumberSpec("ha_timeout_seconds", "Home Assistant timeout", 60, 3600, 30, UnitOfTime.SECONDS, "mdi:lan-disconnect"),
    NumberSpec("balance_extract_excess_percent", "Luftbalance udsugning over indblæsning", 0, 20, 0.5, PERCENTAGE, "mdi:scale-unbalanced"),
    NumberSpec("balance_duct_ratio", "Luftbalance fast kanalforhold", 0.7, 1.5, 0.01, None, "mdi:pipe"),
)


# Step choices whose maximum follows the Pi's step control (4 or 6).
LEVEL_KEYS = {"local_normal_level", "local_min_level", "local_max_level", "vacation_level", "bathroom_max_level"}


class ControllerNumber(ControllerEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    @property
    def native_max_value(self) -> float:
        return float(self.max_level) if self.key in LEVEL_KEYS else self._attr_native_max_value

    def __init__(self, coordinator, spec: NumberSpec) -> None:
        super().__init__(coordinator, spec.key, spec.name)
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_icon = spec.icon

    @property
    def native_value(self) -> float | None:
        value = self.controller_value
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        if self._attr_native_step >= 1:
            value = int(round(value))
        await self.async_command({self.key: value})


# Dantherm commissioning (HCH5 Control 1.4.0+, four steps): key, name, min, max.
FAN_SETTINGS = (
    ("extract", "Trin 3 udsugning", 46, 91, "mdi:fan-chevron-up"),
    ("supply", "Trin 3 indblæsning", 46, 91, "mdi:fan-chevron-down"),
    ("offset", "Gearafstand trin 1-2", 10, 30, "mdi:stairs-down"),
    ("max_extract", "Trin 4 maks. udsugning", 46, 100, "mdi:fan-plus"),
    ("max_supply", "Trin 4 maks. indblæsning", 46, 100, "mdi:fan-plus"),
)


class FanSettingNumber(ControllerEntity, NumberEntity):
    """Step 3 per fan, the offset to steps 2 and 1 and the step-4 maximum, as on the HCP4."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "gear"

    def __init__(self, coordinator, field: str, name: str, minimum: int, maximum: int, icon: str) -> None:
        super().__init__(coordinator, f"fan_settings_{field}", name)
        self.field = field
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        state = self.coordinator.controller_state
        return bool(client and client.connected and state.get("fan_step_count") == 4 and isinstance(state.get("fan_settings"), dict))

    @property
    def native_value(self) -> float | None:
        value = (self.coordinator.controller_state.get("fan_settings") or {}).get(self.field)
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.async_command({"fan_settings": {self.field: int(round(value))}})


class FanProfileNumber(ControllerEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_step = 1

    def __init__(self, coordinator, level: int, kind: str) -> None:
        key = f"level_{level}_{kind}"
        label = "Udsugning" if kind == "extract" else "Indblæsning"
        super().__init__(coordinator, key, f"Niveau {level} {label}")
        self.level = level
        self.kind = kind
        self._attr_icon = "mdi:fan-chevron-up" if kind == "extract" else "mdi:fan-chevron-down"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 100

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and self.has_data)

    @property
    def defer_until_data(self) -> bool:
        # With four steps, levels 5 and 6 do not exist; steps 1-2 follow the
        # offset. A level is created once the Pi reports a profile for it.
        return True

    @property
    def has_data(self) -> bool:
        profiles = self.coordinator.controller_state.get("profiles") or {}
        return str(self.level) in profiles or self.level in profiles

    @property
    def native_value(self) -> float | None:
        profiles = self.coordinator.controller_state.get("profiles") or {}
        profile = profiles.get(str(self.level)) or profiles.get(self.level) or {}
        value = profile.get(self.kind)
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.async_command({
            "profiles": {str(self.level): {self.kind: int(round(value))}}
        })


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.controller_client is None:
        return
    entities = [ControllerNumber(coordinator, spec) for spec in SPECS]
    entities.extend(
        FanProfileNumber(coordinator, level, kind)
        for level in range(1, 7)
        for kind in ("extract", "supply")
    )
    entities.extend(FanSettingNumber(coordinator, *spec) for spec in FAN_SETTINGS)
    async_add_entities_when_ready(entry, coordinator, async_add_entities, entities)
