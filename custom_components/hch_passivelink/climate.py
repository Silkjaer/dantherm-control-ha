"""Climate entity for the HCH5/HAC1 after-heater.

Home Assistant exposes a thermostat UI, but the Raspberry Pi controller remains
source of truth. The Pi only writes the verified HAC1 supply-air setpoint; the
HCH5/HAC1 continues to regulate the water valve and safety functions itself.
"""
from __future__ import annotations

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import async_add_entities_when_ready, parent_link, sub_model
from .controller_entity import ControllerEntity


class AfterheatClimate(ControllerEntity, ClimateEntity):
    """Supply-air afterheat thermostat backed by the Pi controller API."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = 10
    _attr_max_temp = 35
    _attr_target_temperature_step = 1
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "afterheat_setpoint", "Eftervarme")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hch5_mk1_hac1_afterheat")},
            name="Eftervarme",
            manufacturer="Dantherm",
            model=sub_model(coordinator, "HAC1 eftervarme", "Eftervarme"),
            **parent_link(coordinator),
        )

    @property
    def target_temperature(self) -> float | None:
        value = self.coordinator.controller_state.get("afterheat_setpoint")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.coordinator.controller_state.get("afterheat_enabled") is not False else HVACMode.OFF

    @property
    def current_temperature(self) -> float | None:
        state = self.coordinator.controller_state
        value = state.get("actual_supply_air_temperature")
        if not isinstance(value, (int, float)):
            value = self.coordinator.data.get("heating_coil_after_temperature")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def hvac_action(self) -> HVACAction:
        state = self.coordinator.controller_state
        active = state.get("actual_afterheat")
        if active is None:
            active = self.coordinator.data.get("afterheat_active")
        return HVACAction.HEATING if active is True else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs) -> None:
        value = kwargs.get("temperature")
        if value is None:
            return
        await self.async_command({"afterheat_setpoint": int(round(float(value)))})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
            raise ValueError("Unsupported afterheat mode")
        await self.async_command({"afterheat_enabled": hvac_mode == HVACMode.HEAT})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.controller_client is not None:
        async_add_entities_when_ready(entry, coordinator, async_add_entities, [AfterheatClimate(coordinator)])
