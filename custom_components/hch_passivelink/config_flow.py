"""Config flow for Dantherm Control."""

from __future__ import annotations

import asyncio
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import Mk2Client, PassiveLinkClient, PassiveSerialClient
from .controller_api import ControllerApiClient
from .sensor_support import (
    KIND_CO2,
    KIND_ENERGY,
    KIND_HUMIDITY,
    KIND_PM25,
    KIND_POWER,
    KIND_PRICE,
    KIND_TEMPERATURE,
    supported_entity_ids,
)
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_SERIAL_PORT,
    CONNECTION_MK2,
    CONNECTION_SERIAL,
    CONF_UNIT_ID,
    DEFAULT_MK2_PORT,
    CONNECTION_TCP,
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
    ENERGY_SIGNAL_ENTITIES,
    CONF_SMART_ROOMS,
    DEFAULT_CONTROLLER_PORT,
    DEFAULT_FILTER_NOTIFY_DAYS,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PREHEATER_SENSOR_PORT,
    DEFAULT_SMART_INPUT_VALID_FOR,
    MAX_SMART_ROOMS,
    SMART_ROOM_PRIORITIES,
    SMART_ROOM_TYPES,
    ROOM_SLOT_COUNT,
    room_name_key,
    room_temperature_key,
    room_humidity_key,
    room_co2_key,
    DOMAIN,
)

ROOM_SENSOR_KINDS = {
    "temperature": KIND_TEMPERATURE,
    "humidity": KIND_HUMIDITY,
    "co2": KIND_CO2,
    "pm25": KIND_PM25,
}
ENERGY_SIGNAL_KINDS = {
    CONF_UNIT_POWER_ENTITY: KIND_POWER,
    CONF_UNIT_ENERGY_TODAY_ENTITY: KIND_ENERGY,
    CONF_ELECTRICITY_PRICE_ENTITY: KIND_PRICE,
    CONF_HEAT_PRICE_ENTITY: KIND_PRICE,
}


# Passive RS485 is for an HCH5 MK1 + HAC1; units with the Dantherm Residential
# Modbus map are read and controlled over Modbus TCP.
CONNECTION_LABELS = {
    CONNECTION_TCP: "HCH5 MK1 + HAC1 – RS485 over TCP",
    CONNECTION_SERIAL: "HCH5 MK1 + HAC1 – USB-RS485",
    CONNECTION_MK2: "Dantherm Modbus TCP (HCH5 MKII, RCV, HCV, HCC)",
}


class PassiveLinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_SERIAL:
                return await self.async_step_serial()
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_MK2:
                return await self.async_step_mk2()
            return await self.async_step_tcp()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TCP): vol.In(CONNECTION_LABELS)
            }),
        )

    async def async_step_mk2(self, user_input: dict | None = None) -> FlowResult:
        """A Dantherm unit (HCH5 MKII, RCV, HCV, HCC) over Modbus TCP – readings and controls without the Pi."""
        errors = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT])
            unit_id = int(user_input.get(CONF_UNIT_ID, 1))
            client = Mk2Client(host, port, unit_id, lambda _: None)
            try:
                await client.probe()
            except (OSError, ConnectionError, ValueError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"mk2:{host}:{port}:{unit_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{DEFAULT_NAME} ({client.unit.info.model})",
                    data={CONF_CONNECTION_TYPE: CONNECTION_MK2, CONF_HOST: host, CONF_PORT: port, CONF_UNIT_ID: unit_id},
                )
            finally:
                await client.stop()
        return self.async_show_form(
            step_id="mk2",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_MK2_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(CONF_UNIT_ID, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
            }),
            errors=errors,
        )

    async def async_step_tcp(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST], user_input[CONF_PORT]
            try:
                await PassiveLinkClient(host, port, lambda _: None).probe()
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_CONNECTION_TYPE: CONNECTION_TCP, **user_input},
                )
        return self.async_show_form(
            step_id="tcp",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }),
            errors=errors,
        )

    async def async_step_serial(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            port = user_input[CONF_SERIAL_PORT]
            try:
                await PassiveSerialClient(port, lambda _: None).probe()
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"serial:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_CONNECTION_TYPE: CONNECTION_SERIAL, **user_input},
                )
        return self.async_show_form(
            step_id="serial",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_PORT): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return PassiveLinkOptionsFlow(config_entry)


class PassiveLinkOptionsFlow(config_entries.OptionsFlow):
    """Configure transport, Pi controller API and dynamic HA room sources."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry
        self._pending: dict = {}
        self._rooms = self._load_rooms({**config_entry.data, **config_entry.options})
        self._selected_room: int | None = None
        self._controller_client: ControllerApiClient | None = None
        self._controller_state: dict[str, object] = {}

    @property
    def _current(self) -> dict:
        return {**self._config_entry.data, **self._config_entry.options}

    @staticmethod
    def _load_rooms(config: dict) -> list[dict[str, object]]:
        """Load new dynamic rooms, falling back to legacy fixed slots."""
        rooms: list[dict[str, object]] = []
        raw_rooms = config.get(CONF_SMART_ROOMS)
        if isinstance(raw_rooms, list):
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
                room = {
                    "name": name[:64],
                    "enabled": bool(raw.get("enabled", True)),
                    "control": bool(raw.get("control", True)),
                    "priority": priority,
                    "room_type": room_type,
                }
                for key in ROOM_SENSOR_KINDS:
                    value = str(raw.get(key, "")).strip()
                    if value:
                        room[key] = value
                rooms.append(room)
            return rooms

        # Transparent migration from the original 8 fixed room slots.
        for slot in range(1, ROOM_SLOT_COUNT + 1):
            name = str(config.get(room_name_key(slot), "")).strip()
            if not name:
                continue
            room: dict[str, object] = {
                "name": name[:64],
                "enabled": True,
                "control": True,
                "priority": "auto",
            }
            for kind, key_func in (
                ("temperature", room_temperature_key),
                ("humidity", room_humidity_key),
                ("co2", room_co2_key),
            ):
                value = str(config.get(key_func(slot), "")).strip()
                if value:
                    room[kind] = value
            if len(room) > 4:
                rooms.append(room)
        return rooms

    def _entity_selector(self, kind: str, selected: object = None):
        """Offer only sensors whose unit/device class the controller understands."""
        return selector.EntitySelector(selector.EntitySelectorConfig(
            domain="sensor",
            multiple=False,
            include_entities=supported_entity_ids(
                self.hass.states.async_all("sensor"), kind, keep=selected
            ),
        ))

    def _room_schema(self, room: dict[str, object] | None = None) -> vol.Schema:
        room = room or {}
        items: dict = {
            vol.Required("name", default=str(room.get("name", ""))): str,
            vol.Required("enabled", default=bool(room.get("enabled", True))): bool,
            vol.Required("control", default=bool(room.get("control", True))): bool,
            vol.Required("priority", default=str(room.get("priority", "auto"))): vol.In({
                "auto": "Auto",
                "low": "Lav",
                "normal": "Normal",
                "high": "Høj",
                "critical": "Kritisk",
            }),
            vol.Required("room_type", default=str(room.get("room_type", "auto"))): vol.In({
                "auto": "Auto (ud fra rumnavnet)",
                "normal": "Normalt rum",
                "bathroom": "Badeværelse",
            }),
        }
        for key, kind in ROOM_SENSOR_KINDS.items():
            existing = room.get(key)
            marker = vol.Optional(key, default=existing) if existing else vol.Optional(key)
            items[marker] = self._entity_selector(kind, existing)
        return vol.Schema(items)

    def _validate_room(self, user_input: dict, *, exclude_index: int | None = None) -> tuple[dict, dict]:
        errors: dict = {}
        name = str(user_input.get("name", "")).strip()
        if not name:
            errors["name"] = "required"
        elif any(
            index != exclude_index and str(room.get("name", "")).casefold() == name.casefold()
            for index, room in enumerate(self._rooms)
        ):
            errors["name"] = "room_name_duplicate"

        sensors = {
            key: str(user_input.get(key, "") or "").strip()
            for key in ROOM_SENSOR_KINDS
        }
        if not any(sensors.values()):
            errors["base"] = "room_requires_sensor"

        priority = str(user_input.get("priority", "auto"))
        if priority not in SMART_ROOM_PRIORITIES:
            errors["priority"] = "invalid_priority"
        room_type = str(user_input.get("room_type", "auto"))
        if room_type not in SMART_ROOM_TYPES:
            room_type = "auto"

        room: dict[str, object] = {
            "name": name[:64],
            "enabled": bool(user_input.get("enabled", True)),
            "control": bool(user_input.get("control", True)),
            "priority": priority,
            "room_type": room_type,
        }
        room.update({key: value for key, value in sensors.items() if value})
        return room, errors

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        current = self._current
        errors = {}
        if user_input is not None:
            connection_type = user_input[CONF_CONNECTION_TYPE]
            options = dict(user_input)
            if connection_type == CONNECTION_SERIAL:
                serial_port = str(user_input.get(CONF_SERIAL_PORT, "")).strip()
                if not serial_port:
                    errors[CONF_SERIAL_PORT] = "required"
                else:
                    try:
                        await PassiveSerialClient(serial_port, lambda _: None).probe()
                    except (OSError, ConnectionError, asyncio.TimeoutError):
                        errors["base"] = "cannot_connect"
                    options[CONF_SERIAL_PORT] = serial_port
            else:
                host = str(user_input.get(CONF_HOST, "")).strip()
                port = int(user_input[CONF_PORT])
                if not host:
                    errors[CONF_HOST] = "required"
                else:
                    try:
                        if connection_type == CONNECTION_MK2:
                            probe_client = Mk2Client(host, port, int(current.get(CONF_UNIT_ID, 1)), lambda _: None)
                            try:
                                await probe_client.probe()
                            finally:
                                await probe_client.stop()
                        else:
                            await PassiveLinkClient(host, port, lambda _: None).probe()
                    except (OSError, ConnectionError, ValueError, asyncio.TimeoutError):
                        errors["base"] = "cannot_connect"
                    options[CONF_HOST] = host
                    options[CONF_PORT] = port
                    if connection_type == CONNECTION_MK2:
                        options[CONF_UNIT_ID] = int(current.get(CONF_UNIT_ID, 1))
            if not errors:
                self._pending = options
                # Chosen on the separate sensor page; keep them unless changed there.
                for key in ENERGY_SIGNAL_ENTITIES:
                    if current.get(key):
                        self._pending[key] = current[key]
                if current.get(CONF_WEATHER_ENTITY):
                    self._pending[CONF_WEATHER_ENTITY] = current[CONF_WEATHER_ENTITY]
                if user_input.get(CONF_CONTROLLER_API_ENABLED, False):
                    return await self.async_step_controller()
                self._pending[CONF_SMART_ROOMS] = self._rooms
                return self.async_create_entry(title="", data=self._pending)

        schema = vol.Schema({
            vol.Required(
                CONF_CONNECTION_TYPE,
                default=current.get(CONF_CONNECTION_TYPE, CONNECTION_TCP),
            ): vol.In(CONNECTION_LABELS),
            vol.Optional(CONF_HOST, default=current.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_SERIAL_PORT, default=current.get(CONF_SERIAL_PORT, "")): str,
            vol.Required(
                CONF_FILTER_NOTIFY_ENABLED,
                default=current.get(CONF_FILTER_NOTIFY_ENABLED, True),
            ): bool,
            vol.Required(
                CONF_FILTER_NOTIFY_DAYS,
                default=current.get(CONF_FILTER_NOTIFY_DAYS, DEFAULT_FILTER_NOTIFY_DAYS),
            ): selector.NumberSelector(selector.NumberSelectorConfig(
                min=1, max=180, step=1, mode=selector.NumberSelectorMode.BOX
            )),
            vol.Optional(
                CONF_FILTER_NOTIFY_SERVICE,
                default=current.get(CONF_FILTER_NOTIFY_SERVICE, ""),
            ): str,
            vol.Required(
                CONF_PREHEATER_SENSORS_ENABLED,
                default=current.get(CONF_PREHEATER_SENSORS_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_PREHEATER_SENSOR_HOST,
                default=current.get(CONF_PREHEATER_SENSOR_HOST, current.get(CONF_HOST, "")),
            ): str,
            vol.Required(
                CONF_PREHEATER_SENSOR_PORT,
                default=current.get(CONF_PREHEATER_SENSOR_PORT, DEFAULT_PREHEATER_SENSOR_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_PREHEATER_SWAP_SENSORS,
                default=current.get(CONF_PREHEATER_SWAP_SENSORS, False),
            ): bool,
            vol.Required(
                CONF_CONTROLLER_API_ENABLED,
                default=current.get(CONF_CONTROLLER_API_ENABLED, True),
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_controller(self, user_input: dict | None = None) -> FlowResult:
        current = self._current
        errors = {}
        default_host = current.get(CONF_CONTROLLER_HOST) or self._pending.get(CONF_HOST) or current.get(CONF_HOST, "")
        if user_input is not None:
            host = str(user_input[CONF_CONTROLLER_HOST]).strip()
            token = str(user_input[CONF_CONTROLLER_TOKEN]).strip()
            port = int(user_input[CONF_CONTROLLER_PORT])
            if not host:
                errors[CONF_CONTROLLER_HOST] = "required"
            if not token:
                errors[CONF_CONTROLLER_TOKEN] = "required"
            if not errors:
                client = ControllerApiClient(
                    async_get_clientsession(self.hass), host, port, token, lambda _: None
                )
                try:
                    self._controller_state = await client.async_probe()
                except (OSError, ConnectionError, asyncio.TimeoutError):
                    errors["base"] = "controller_cannot_connect"
            if not errors:
                self._controller_client = client
                self._pending.update(user_input)
                return await self.async_step_controller_menu()

        schema = vol.Schema({
            vol.Required(CONF_CONTROLLER_HOST, default=default_host): str,
            vol.Required(
                CONF_CONTROLLER_PORT,
                default=current.get(CONF_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_CONTROLLER_TOKEN,
                default=current.get(CONF_CONTROLLER_TOKEN, ""),
            ): selector.TextSelector(selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD
            )),
            vol.Required(
                CONF_SMART_ROOMS_ENABLED,
                default=current.get(CONF_SMART_ROOMS_ENABLED, True),
            ): bool,
            vol.Required(
                CONF_SMART_INPUT_VALID_FOR,
                default=current.get(CONF_SMART_INPUT_VALID_FOR, DEFAULT_SMART_INPUT_VALID_FOR),
            ): selector.NumberSelector(selector.NumberSelectorConfig(
                min=30, max=900, step=30, mode=selector.NumberSelectorMode.BOX
            )),
        })
        return self.async_show_form(step_id="controller", data_schema=schema, errors=errors)

    async def async_step_controller_sensors(self, user_input: dict | None = None) -> FlowResult:
        """Choose which HA sensors are leased to the controller at the chosen IP."""
        if user_input is not None:
            # An emptied optional entity selector is absent from user_input.
            for key in (*ENERGY_SIGNAL_ENTITIES, CONF_WEATHER_ENTITY):
                self._pending.pop(key, None)
            self._pending.update(
                {key: user_input[key] for key in (*ENERGY_SIGNAL_ENTITIES, CONF_WEATHER_ENTITY) if user_input.get(key)}
            )
            return await self.async_step_controller_menu()

        fields = {}
        for key, kind in ENERGY_SIGNAL_KINDS.items():
            selected = self._pending.get(key)
            fields[vol.Optional(key, description={"suggested_value": selected})] = (
                self._entity_selector(kind, selected)
            )
        fields[vol.Optional(CONF_WEATHER_ENTITY, description={"suggested_value": self._pending.get(CONF_WEATHER_ENTITY)})] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather", multiple=False)
        )
        return self.async_show_form(
            step_id="controller_sensors",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "controller": f"{self._pending.get(CONF_CONTROLLER_HOST, '')}:"
                f"{self._pending.get(CONF_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT)}",
            },
        )

    async def async_step_controller_menu(self, user_input: dict | None = None) -> FlowResult:
        del user_input
        options = ["controller_sensors", "controller_settings", "fan_profiles"]
        if self._pending.get(CONF_SMART_ROOMS_ENABLED, False):
            options.append("rooms_menu")
        options.append("finish_controller")
        return self.async_show_menu(step_id="controller_menu", menu_options=options)

    async def async_step_controller_settings(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        state = self._controller_state
        if user_input is not None:
            try:
                if self._controller_client is None:
                    raise ConnectionError("Controller API is not connected")
                self._controller_state = await self._controller_client.async_command(dict(user_input))
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "controller_update_failed"
            else:
                return await self.async_step_controller_menu()

        # Highest step: 4 with Dantherm steps (HCH5 Control 1.4.0+), else 6.
        top = int(state.get("max_level") or 6)
        number = lambda minimum, maximum, step=1: selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=minimum, max=maximum, step=step,
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        schema = vol.Schema({
            vol.Required("mode", default=state.get("mode", "local_auto")): vol.In({
                "local_auto": "Local Auto", "smart_auto": "Smart Auto", "manual": "Manual",
            }),
            vol.Required("manual_level", default=state.get("manual_level", 3)): number(1, top),
            vol.Required("local_normal_level", default=state.get("local_normal_level", 3)): number(1, top),
            vol.Required("local_min_level", default=state.get("local_min_level", 1)): number(1, top),
            vol.Required("local_max_level", default=state.get("local_max_level", top)): number(1, top),
            vol.Required("rh_setpoint", default=state.get("rh_setpoint", 50)): number(25, 80),
            vol.Required("rh_hysteresis", default=state.get("rh_hysteresis", 3)): number(1, 10),
            vol.Required("co2_setpoint", default=state.get("co2_setpoint", 800)): number(500, 2000, 50),
            vol.Required("co2_hysteresis", default=state.get("co2_hysteresis", 100)): number(25, 500, 25),
            vol.Required("auto_step_rh", default=state.get("auto_step_rh", 5)): number(2, 20),
            vol.Required("auto_step_co2", default=state.get("auto_step_co2", 200)): number(50, 1000, 50),
            vol.Required("downshift_delay_seconds", default=state.get("downshift_delay_seconds", 300)): number(30, 3600, 30),
            vol.Required("boost_hold_seconds", default=state.get("boost_hold_seconds", 600)): number(60, 3600, 60),
            vol.Required("ha_timeout_seconds", default=state.get("ha_timeout_seconds", 300)): number(60, 3600, 30),
            vol.Required("afterheat_setpoint", default=state.get("afterheat_setpoint", 20)): number(18, 30),
        })
        return self.async_show_form(
            step_id="controller_settings", data_schema=schema, errors=errors
        )

    async def async_step_fan_profiles(self, user_input: dict | None = None) -> FlowResult:
        if self._controller_state.get("fan_step_count") == 4:
            return await self.async_step_fan_steps(user_input)
        errors = {}
        profiles = self._controller_state.get("profiles") or {}
        if user_input is not None:
            patch = {"profiles": {
                str(level): {
                    "name": str(user_input[f"profile_{level}_name"]).strip(),
                    "extract": int(user_input[f"profile_{level}_extract"]),
                    "supply": int(user_input[f"profile_{level}_supply"]),
                }
                for level in range(1, 7)
            }}
            try:
                if self._controller_client is None:
                    raise ConnectionError("Controller API is not connected")
                self._controller_state = await self._controller_client.async_command(patch)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "controller_update_failed"
            else:
                return await self.async_step_controller_menu()

        fields = {}
        defaults = (("Lav", 25, 13), ("Lav+", 40, 28), ("Normal", 55, 43),
                    ("Høj", 70, 58), ("Høj+", 85, 73), ("Boost", 100, 88))
        for level, (name, extract, supply) in enumerate(defaults, 1):
            current = profiles.get(str(level)) or profiles.get(level) or {}
            fields[vol.Required(
                f"profile_{level}_name", default=current.get("name", name)
            )] = str
            fields[vol.Required(
                f"profile_{level}_extract", default=current.get("extract", extract)
            )] = vol.All(vol.Coerce(int), vol.Range(min=1, max=100))
            fields[vol.Required(
                f"profile_{level}_supply", default=current.get("supply", supply)
            )] = vol.All(vol.Coerce(int), vol.Range(min=1, max=100))
        return self.async_show_form(
            step_id="fan_profiles", data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_fan_steps(self, user_input: dict | None = None) -> FlowResult:
        """Four Dantherm steps: commission step 3 per fan, the offset and step 4."""
        errors = {}
        settings = self._controller_state.get("fan_settings") or {}
        if user_input is not None:
            try:
                if self._controller_client is None:
                    raise ConnectionError("Controller API is not connected")
                self._controller_state = await self._controller_client.async_command(
                    {"fan_settings": {key: int(value) for key, value in user_input.items()}})
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "controller_update_failed"
            else:
                return await self.async_step_controller_menu()
        gear = lambda minimum, maximum: vol.All(vol.Coerce(int), vol.Range(min=minimum, max=maximum))
        fields = {
            vol.Required("extract", default=settings.get("extract", 64)): gear(46, 91),
            vol.Required("supply", default=settings.get("supply", 64)): gear(46, 91),
            vol.Required("offset", default=settings.get("offset", 25)): gear(10, 30),
            vol.Required("max_extract", default=settings.get("max_extract", 100)): gear(46, 100),
            vol.Required("max_supply", default=settings.get("max_supply", 100)): gear(46, 100),
        }
        return self.async_show_form(step_id="fan_steps", data_schema=vol.Schema(fields), errors=errors)

    async def async_step_rooms_menu(self, user_input: dict | None = None) -> FlowResult:
        del user_input
        options = []
        if len(self._rooms) < MAX_SMART_ROOMS:
            options.append("add_room")
        if self._rooms:
            options.extend(("edit_room", "remove_room"))
        options.append("finish_rooms")
        return self.async_show_menu(step_id="rooms_menu", menu_options=options)

    async def async_step_add_room(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            room, errors = self._validate_room(user_input)
            if not errors:
                self._rooms.append(room)
                return await self.async_step_rooms_menu()
        return self.async_show_form(
            step_id="add_room",
            data_schema=self._room_schema(),
            errors=errors,
        )

    async def async_step_edit_room(self, user_input: dict | None = None) -> FlowResult:
        if not self._rooms:
            return await self.async_step_rooms_menu()
        choices = {str(index): str(room["name"]) for index, room in enumerate(self._rooms)}
        if user_input is not None:
            self._selected_room = int(user_input["room"])
            return await self.async_step_room_detail()
        return self.async_show_form(
            step_id="edit_room",
            data_schema=vol.Schema({vol.Required("room"): vol.In(choices)}),
        )

    async def async_step_room_detail(self, user_input: dict | None = None) -> FlowResult:
        if self._selected_room is None or self._selected_room >= len(self._rooms):
            return await self.async_step_rooms_menu()
        current = self._rooms[self._selected_room]
        errors = {}
        if user_input is not None:
            room, errors = self._validate_room(user_input, exclude_index=self._selected_room)
            if not errors:
                self._rooms[self._selected_room] = room
                self._selected_room = None
                return await self.async_step_rooms_menu()
        return self.async_show_form(
            step_id="room_detail",
            data_schema=self._room_schema(current),
            errors=errors,
        )

    async def async_step_remove_room(self, user_input: dict | None = None) -> FlowResult:
        if not self._rooms:
            return await self.async_step_rooms_menu()
        choices = {str(index): str(room["name"]) for index, room in enumerate(self._rooms)}
        if user_input is not None:
            index = int(user_input["room"])
            if 0 <= index < len(self._rooms):
                self._rooms.pop(index)
            return await self.async_step_rooms_menu()
        return self.async_show_form(
            step_id="remove_room",
            data_schema=vol.Schema({vol.Required("room"): vol.In(choices)}),
        )

    async def async_step_finish_rooms(self, user_input: dict | None = None) -> FlowResult:
        del user_input
        return await self.async_step_controller_menu()

    async def async_step_finish_controller(self, user_input: dict | None = None) -> FlowResult:
        del user_input
        self._pending[CONF_SMART_ROOMS] = self._rooms
        # Do not persist the old fixed-slot representation when options are saved.
        for slot in range(1, ROOM_SLOT_COUNT + 1):
            for key_func in (room_name_key, room_temperature_key, room_humidity_key, room_co2_key):
                self._pending.pop(key_func(slot), None)
        return self.async_create_entry(title="", data=self._pending)
