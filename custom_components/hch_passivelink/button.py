"""Filter-reset button for Dantherm Control."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PassiveLinkEntity
from .controller_entity import ControllerEntity
from .mk2_entities import Mk2AlarmResetButton, Mk2FilterResetButton, is_direct_mk2

DESCRIPTION = ButtonEntityDescription(
    key="filter_reset",
    translation_key="filter_reset",
    icon="mdi:air-filter",
    entity_category=EntityCategory.CONFIG,
)


class PassiveLinkFilterResetButton(PassiveLinkEntity, ButtonEntity):
    """Reset the locally tracked filter cycle to today.

    Purely local state — never writes to the Modbus bus.
    """

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, DESCRIPTION.key)
        self.entity_description = DESCRIPTION

    @property
    def available(self) -> bool:
        return self.coordinator.available

    async def async_press(self) -> None:
        self.coordinator.async_reset_filter()


class QuickBoostButton(ControllerEntity, ButtonEntity):
    _attr_icon = "mdi:fan-clock"

    def __init__(self, coordinator, minutes: int) -> None:
        super().__init__(coordinator, f"quick_boost_{minutes}", f"Hurtig boost {minutes} min" if minutes else "Stop hurtig boost")
        self.minutes = minutes

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected)

    async def async_press(self) -> None:
        await self.async_command({"quick_boost_minutes": self.minutes})


COOL_BOOST_NAMES = {30: "Køl 30 min", 60: "Køl 1 time", 120: "Køl 2 timer", 0: "Stop køl"}


class CoolBoostButton(ControllerEntity, ButtonEntity):
    """Køl: bypass open, top step and afterheat held down for a while (HCH5 Control 1.5.0+)."""

    _attr_icon = "mdi:snowflake-alert"

    def __init__(self, coordinator, minutes: int) -> None:
        super().__init__(coordinator, f"cool_boost_{minutes}", COOL_BOOST_NAMES[minutes])
        self.minutes = minutes

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected)

    async def async_press(self) -> None:
        await self.async_command({"cool_boost_minutes": self.minutes})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if is_direct_mk2(coordinator):
        entities = [Mk2FilterResetButton(coordinator), Mk2AlarmResetButton(coordinator)]
    else:
        entities = [PassiveLinkFilterResetButton(coordinator)]
    if coordinator.controller_client is not None:
        entities.extend(QuickBoostButton(coordinator, minutes) for minutes in (15, 30, 60, 0))
        entities.extend(CoolBoostButton(coordinator, minutes) for minutes in (30, 60, 120, 0))
    async_add_entities(entities)
