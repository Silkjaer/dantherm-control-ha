"""Constants for Dantherm Control (formerly Dantherm HCH5 Control)."""

DOMAIN = "hch_passivelink"
DEFAULT_PORT = 4196
DEFAULT_NAME = "Dantherm Control"
# Default entry titles of earlier versions; only these untouched defaults are renamed.
OLD_DEFAULT_NAMES = ("Dantherm HCH PassiveLink", "Dantherm HCH5 Control")
# The main device of a passive MK1 + HAC1 install keeps its name, so entities that
# appear in later versions get the same entity_id prefix as the existing ones.
MK1_DEVICE_NAME = "Dantherm HCH5 Control"
MK1_DEVICE_MODEL = "HCH5 MK1 + HAC1"
CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL_PORT = "serial_port"
CONNECTION_MK2 = "mk2_tcp"
CONF_UNIT_ID = "unit_id"
DEFAULT_MK2_PORT = 502
CONNECTION_TCP = "tcp"
CONNECTION_SERIAL = "serial"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_FILTER_NOTIFY_ENABLED = "filter_notify_enabled"
CONF_FILTER_NOTIFY_DAYS = "filter_notify_days"
CONF_FILTER_NOTIFY_SERVICE = "filter_notify_service"
CONF_PREHEATER_SENSORS_ENABLED = "preheater_sensors_enabled"
CONF_PREHEATER_SENSOR_HOST = "preheater_sensor_host"
CONF_PREHEATER_SENSOR_PORT = "preheater_sensor_port"
CONF_PREHEATER_SWAP_SENSORS = "preheater_swap_sensors"
DEFAULT_PREHEATER_SENSOR_PORT = 4197
DEFAULT_FILTER_NOTIFY_DAYS = 30

# Raspberry Pi controller API. This is separate from the receive-only RTU/TCP
# stream: HA sends intent to the Pi controller, never Modbus writes.
CONF_CONTROLLER_API_ENABLED = "controller_api_enabled"
CONF_CONTROLLER_HOST = "controller_host"
CONF_CONTROLLER_PORT = "controller_port"
CONF_CONTROLLER_TOKEN = "controller_token"
DEFAULT_CONTROLLER_PORT = 8080
CONF_SMART_ROOMS_ENABLED = "smart_rooms_enabled"
CONF_SMART_INPUT_VALID_FOR = "smart_input_valid_for"
CONF_SMART_ROOMS = "smart_rooms"
# Power meter on the unit (e.g. a Shelly), sent to the Pi for its WebUI.
CONF_UNIT_POWER_ENTITY = "unit_power_entity"
# Optional daily energy meter and prices, sent to the Pi for its WebUI energy tiles.
CONF_UNIT_ENERGY_TODAY_ENTITY = "unit_energy_today_entity"
CONF_ELECTRICITY_PRICE_ENTITY = "electricity_price_entity"
CONF_HEAT_PRICE_ENTITY = "heat_price_entity"
CONF_WEATHER_ENTITY = "weather_entity"
ENERGY_SIGNAL_ENTITIES = (
    CONF_UNIT_POWER_ENTITY,
    CONF_UNIT_ENERGY_TODAY_ENTITY,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_HEAT_PRICE_ENTITY,
)
DEFAULT_SMART_INPUT_VALID_FOR = 180
MAX_SMART_ROOMS = 32
SMART_ROOM_PRIORITIES = ("auto", "low", "normal", "high", "critical")
# "auto" lets the Pi guess from the room name (Bad, Bath, Brus).
SMART_ROOM_TYPES = ("auto", "normal", "bathroom")
# Room measurements the Pi controller accepts, with the range it validates.
# A value outside the range makes the Pi reject the whole message (HTTP 400).
ROOM_SENSOR_RANGES = {
    "temperature": (-30.0, 60.0),
    "humidity": (0.0, 100.0),
    "co2": (250.0, 10000.0),
    "pm25": (0.0, 1000.0),
}
ROOM_SENSOR_KEYS = tuple(ROOM_SENSOR_RANGES)

# Legacy fixed slots are kept only for migration/backwards compatibility.
ROOM_SLOT_COUNT = 8


def room_name_key(slot: int) -> str:
    return f"smart_room_{slot}_name"


def room_temperature_key(slot: int) -> str:
    return f"smart_room_{slot}_temperature"


def room_humidity_key(slot: int) -> str:
    return f"smart_room_{slot}_humidity"


def room_co2_key(slot: int) -> str:
    return f"smart_room_{slot}_co2"


def main_device_name(mk2_info=None) -> str:
    """The main device's name: "Dantherm <model>" over Modbus, unchanged on an MK1 + HAC1."""
    if mk2_info is None:
        return MK1_DEVICE_NAME
    model = str(mk2_info.model)
    return model if model.startswith("Dantherm") else f"Dantherm {model}"


CONTROLLER_DEVICE_NAME = "Dantherm Control controller"


def kept_device_name(existing_name: str | None, new_name: str) -> str:
    """Keep the registry name of a device that already exists, else use the new name.

    HA derives a new entity's entity_id from its device name, so renaming an
    existing device would give entities added later (deferred ones, or new in a
    release) a different prefix than the entities already there.
    """
    return existing_name or new_name


def main_device_model(mk2_info=None) -> str:
    return mk2_info.model if mk2_info is not None else MK1_DEVICE_MODEL


def renamed_entry_title(title: str, mk2_model: str | None = None) -> str | None:
    """The new title for an entry still carrying an old default title, else None.

    Titles the user has changed are left alone. A Modbus entry's default title
    also named the unit, e.g. "Dantherm HCH5 Control (Dantherm type 21)".
    """
    if title in OLD_DEFAULT_NAMES:
        return DEFAULT_NAME
    if mk2_model:
        for old in OLD_DEFAULT_NAMES:
            prefix = f"{old} ("
            if title.startswith(prefix) and title.endswith(")"):
                model = title[len(prefix):-1]
                if model == mk2_model or model.startswith("Dantherm type "):
                    return f"{DEFAULT_NAME} ({mk2_model})"
    return None


# Raspberry Pi host diagnostics - reported by the same optional endpoint
# (gateway/onewire_temperature_server.py) independently of whether DS18B20
# preheater sensors are configured.
PI_DIAGNOSTIC_KEYS = (
    "pi_model",
    "pi_kernel_version",
    "pi_cpu_temperature",
    "pi_uptime_seconds",
    "pi_load_average_1m",
    "pi_memory_used_percent",
    "pi_disk_used_percent",
    "pi_core_voltage",
    "pi_undervoltage_now",
    "pi_undervoltage_occurred",
    "pi_frequency_capped_now",
    "pi_frequency_capped_occurred",
    "pi_throttled_now",
    "pi_throttled_occurred",
    "pi_soft_temp_limit_now",
    "pi_soft_temp_limit_occurred",
)
