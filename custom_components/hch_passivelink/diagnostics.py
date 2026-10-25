"""Diagnostics for Dantherm Control."""

from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {"host"}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "connected": coordinator.client.connected,
        "decoded_values": dict(coordinator.data or {}),
    }

