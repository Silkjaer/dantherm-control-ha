"""Dantherm Control integration (formerly Dantherm HCH5 Control)."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .client import Mk2Client, PassiveLinkClient, PassiveSerialClient
from .controller_api import ControllerApiClient
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_FILTER_NOTIFY_DAYS,
    CONF_FILTER_NOTIFY_ENABLED,
    CONF_FILTER_NOTIFY_SERVICE,
    CONF_PREHEATER_SENSORS_ENABLED,
    CONF_PREHEATER_SENSOR_HOST,
    CONF_PREHEATER_SENSOR_PORT,
    CONF_PREHEATER_SWAP_SENSORS,
    CONF_CONTROLLER_API_ENABLED,
    CONF_CONTROLLER_HOST,
    CONF_CONTROLLER_PORT,
    CONF_CONTROLLER_TOKEN,
    CONF_SMART_ROOMS_ENABLED,
    CONF_SMART_INPUT_VALID_FOR,
    CONF_UNIT_POWER_ENTITY,
    CONF_UNIT_ENERGY_TODAY_ENTITY,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_HEAT_PRICE_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_SMART_ROOMS,
    DEFAULT_CONTROLLER_PORT,
    DEFAULT_FILTER_NOTIFY_DAYS,
    DEFAULT_PREHEATER_SENSOR_PORT,
    DEFAULT_SMART_INPUT_VALID_FOR,
    MAX_SMART_ROOMS,
    SMART_ROOM_PRIORITIES,
    SMART_ROOM_TYPES,
    ROOM_SENSOR_KEYS,
    ROOM_SLOT_COUNT,
    room_name_key,
    room_temperature_key,
    room_humidity_key,
    room_co2_key,
    CONNECTION_MK2,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    CONF_UNIT_ID,
    DEFAULT_MK2_PORT,
    DOMAIN,
    main_device_model,
    main_device_name,
    kept_device_name,
    CONTROLLER_DEVICE_NAME,
    renamed_entry_title,
)
from .auxiliary import AuxiliaryTemperatureClient
from .coordinator import ISSUE_CONNECTION_LOST, ISSUE_HAC1_DISCONNECTED
from .smart_coordinator import SmartPassiveLinkCoordinator

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
]
PassiveLinkConfigEntry = ConfigEntry[SmartPassiveLinkCoordinator]


def _room_sources(config: dict) -> list[dict[str, object]]:
    """Return dynamic room sources, with fallback for old fixed-slot options."""
    if not config.get(CONF_SMART_ROOMS_ENABLED, False):
        return []

    raw_rooms = config.get(CONF_SMART_ROOMS)
    if isinstance(raw_rooms, list):
        result: list[dict[str, object]] = []
        for raw in raw_rooms[:MAX_SMART_ROOMS]:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            priority = str(raw.get("priority", "auto"))
            if priority not in SMART_ROOM_PRIORITIES:
                priority = "auto"
            room_type = str(raw.get("room_type", "auto"))
            if room_type not in SMART_ROOM_TYPES:
                room_type = "auto"
            source: dict[str, object] = {
                "name": name[:64],
                "enabled": bool(raw.get("enabled", True)),
                "control": bool(raw.get("control", True)),
                "priority": priority,
                "room_type": room_type,
            }
            for kind in ROOM_SENSOR_KEYS:
                entity_id = str(raw.get(kind, "")).strip()
                if entity_id:
                    source[kind] = entity_id
            if any(source.get(kind) for kind in ROOM_SENSOR_KEYS):
                result.append(source)
        return result

    # Backwards compatibility for beta installs using smart_room_1..8 fields.
    result = []
    for slot in range(1, ROOM_SLOT_COUNT + 1):
        name = str(config.get(room_name_key(slot), "")).strip()
        if not name:
            continue
        source: dict[str, object] = {
            "name": name,
            "enabled": True,
            "control": True,
            "priority": "auto",
        }
        for kind, key_func in (
            ("temperature", room_temperature_key),
            ("humidity", room_humidity_key),
            ("co2", room_co2_key),
        ):
            entity_id = str(config.get(key_func(slot), "")).strip()
            if entity_id:
                source[kind] = entity_id
        if any(source.get(kind) for kind in ROOM_SENSOR_KEYS):
            result.append(source)
    return result


async def _async_reload_entry(hass: HomeAssistant, entry: PassiveLinkConfigEntry) -> None:
    """Reload after connection/controller options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: PassiveLinkConfigEntry) -> bool:
    config = {**entry.data, **entry.options}
    connection_type = config.get(CONF_CONNECTION_TYPE, CONNECTION_TCP)
    mk2_info = None
    if connection_type == CONNECTION_SERIAL:
        client = PassiveSerialClient(config[CONF_SERIAL_PORT], lambda _: None)
        task_name = "Dantherm Control USB-RS485"
    elif connection_type == CONNECTION_MK2:
        client = Mk2Client(
            config[CONF_HOST], int(config.get(CONF_PORT, DEFAULT_MK2_PORT)), int(config.get(CONF_UNIT_ID, 1)), lambda _: None
        )
        task_name = "Dantherm Control Modbus TCP"
        try:
            await client.probe()
            mk2_info = client.unit.info
        except (OSError, ValueError, TimeoutError) as err:
            raise ConfigEntryNotReady(f"The Dantherm unit does not answer over Modbus TCP: {err}") from err
    else:
        client = PassiveLinkClient(config[CONF_HOST], config[CONF_PORT], lambda _: None)
        task_name = "Dantherm Control TCP"
    # The integration was renamed; only an untouched default title moves.
    new_title = renamed_entry_title(entry.title, mk2_info.model if mk2_info else None)
    if new_title:
        hass.config_entries.async_update_entry(entry, title=new_title)

    session = async_get_clientsession(hass)
    controller_client = None
    if config.get(CONF_CONTROLLER_API_ENABLED, False):
        controller_host = str(
            config.get(CONF_CONTROLLER_HOST)
            or config.get(CONF_HOST)
            or "127.0.0.1"
        ).strip()
        controller_token = str(config.get(CONF_CONTROLLER_TOKEN, "")).strip()
        if controller_host and controller_token:
            controller_client = ControllerApiClient(
                session,
                controller_host,
                int(config.get(CONF_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT)),
                controller_token,
                lambda _: None,
            )

    coordinator = SmartPassiveLinkCoordinator(
        hass,
        client,
        entry.entry_id,
        notify_enabled=config.get(CONF_FILTER_NOTIFY_ENABLED, True),
        notify_days=config.get(CONF_FILTER_NOTIFY_DAYS, DEFAULT_FILTER_NOTIFY_DAYS),
        notify_service=config.get(CONF_FILTER_NOTIFY_SERVICE, ""),
        auxiliary_client=(
            AuxiliaryTemperatureClient(
                session,
                config.get(CONF_PREHEATER_SENSOR_HOST)
                or config.get(CONF_HOST, "127.0.0.1"),
                config.get(CONF_PREHEATER_SENSOR_PORT, DEFAULT_PREHEATER_SENSOR_PORT),
                swap_sensors=config.get(CONF_PREHEATER_SWAP_SENSORS, False),
            )
            if config.get(CONF_PREHEATER_SENSORS_ENABLED, False)
            else None
        ),
        controller_client=controller_client,
        room_sources=_room_sources(config),
        smart_input_valid_for=config.get(
            CONF_SMART_INPUT_VALID_FOR, DEFAULT_SMART_INPUT_VALID_FOR
        ),
        unit_power_entity=config.get(CONF_UNIT_POWER_ENTITY) or None,
        weather_entity=config.get(CONF_WEATHER_ENTITY) or None,
        energy_entities={
            "unit_energy_measured_today_kwh": config.get(CONF_UNIT_ENERGY_TODAY_ENTITY) or None,
            "electricity_price_dkk_kwh": config.get(CONF_ELECTRICITY_PRICE_ENTITY) or None,
            "heat_price_dkk_kwh": config.get(CONF_HEAT_PRICE_ENTITY) or None,
        },
    )
    await coordinator.async_load_filter_state()
    client.set_update_callback(coordinator.async_handle_update)
    # Register the main device first so sub-devices can link to its id.
    coordinator.mk2_info = mk2_info
    device_registry = dr.async_get(hass)
    # Devices that already exist keep their names (entity_id prefixes stay
    # consistent); only new installations get the new names.
    existing_main = device_registry.async_get_device(identifiers={(DOMAIN, "hch5_mk1_hac1")})
    existing_controller = device_registry.async_get_device(identifiers={(DOMAIN, "hch5_pi_controller")})
    coordinator.main_device_name = kept_device_name(
        existing_main.name if existing_main else None, main_device_name(mk2_info)
    )
    coordinator.controller_device_name = kept_device_name(
        existing_controller.name if existing_controller else None, CONTROLLER_DEVICE_NAME
    )
    coordinator.main_device_id = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "hch5_mk1_hac1")},
        name=coordinator.main_device_name,
        manufacturer="Dantherm",
        model=main_device_model(mk2_info),
        sw_version=mk2_info.firmware if mk2_info else None,
    ).id
    entry.runtime_data = coordinator
    coordinator.task = entry.async_create_background_task(hass, client.run(), task_name)
    await coordinator.async_start_controller()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PassiveLinkConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device that no longer has any entities."""
    registry = er.async_get(hass)
    return not er.async_entries_for_device(
        registry, device_entry.id, include_disabled_entities=True
    )


async def async_unload_entry(hass: HomeAssistant, entry: PassiveLinkConfigEntry) -> bool:
    await entry.runtime_data.client.stop()
    await entry.runtime_data.async_shutdown()
    if entry.runtime_data.task:
        entry.runtime_data.task.cancel()
    ir.async_delete_issue(hass, DOMAIN, ISSUE_CONNECTION_LOST)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_HAC1_DISCONNECTED)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
