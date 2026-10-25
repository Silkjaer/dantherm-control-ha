"""Base entities for the Raspberry Pi controller (Dantherm Control)."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import OPTIONAL_CONTROLLER_KEYS, parent_link


class ControllerEntity(CoordinatorEntity):
    """Entity backed by controller state while the Pi remains source of truth."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"hch5_pi_controller_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hch5_pi_controller")},
            name="Dantherm Control controller",
            manufacturer="Dantherm / Dantherm Control",
            model="Raspberry Pi controller",
            **parent_link(coordinator),
        )

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and self.key in self.coordinator.controller_state)

    @property
    def max_level(self) -> int:
        """Highest fan step: 4 with Dantherm steps, 6 otherwise (and before 1.4.0)."""
        try:
            return int(self.coordinator.controller_state.get("max_level") or 6)
        except (TypeError, ValueError):
            return 6

    @property
    def controller_value(self):
        return self.coordinator.controller_state.get(self.key)

    @property
    def defer_until_data(self) -> bool:
        """Whether the entity is created only when the Pi first reports a value."""
        return self.key in OPTIONAL_CONTROLLER_KEYS

    @property
    def has_data(self) -> bool:
        return self.controller_value is not None

    async def async_command(self, patch: dict[str, object]) -> None:
        await self.coordinator.async_controller_command(patch)
