"""Binary sensors for Dantherm Control."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller_entity import ControllerEntity
from .entity import PI_KEYS, PREHEATER_KEYS, PassiveLinkEntity, async_add_entities_when_ready
from .mk2_entities import drop_mk1_only

DESCRIPTIONS = (
    BinarySensorEntityDescription(key="bypass_active", translation_key="bypass_active", device_class=BinarySensorDeviceClass.OPENING),
    BinarySensorEntityDescription(key="afterheat_active", translation_key="afterheat_active", device_class=BinarySensorDeviceClass.HEAT, icon="mdi:radiator"),
    BinarySensorEntityDescription(key="fireplace", translation_key="fireplace"),
    BinarySensorEntityDescription(key="standby", translation_key="standby"),
    BinarySensorEntityDescription(key="night_mode", translation_key="night_mode"),
    BinarySensorEntityDescription(key="hac1_connected", translation_key="hac1_connected", device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="bus_traffic", translation_key="bus_traffic", device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="filter_alarm", translation_key="filter_alarm", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="extract_fan_fault", translation_key="extract_fan_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="supply_fan_fault", translation_key="supply_fan_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="outdoor_temperature_sensor_fault", translation_key="outdoor_temperature_sensor_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="supply_temperature_sensor_fault", translation_key="supply_temperature_sensor_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="extract_temperature_sensor_fault", translation_key="extract_temperature_sensor_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="exhaust_temperature_sensor_fault", translation_key="exhaust_temperature_sensor_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="room_temperature_sensor_fault", translation_key="room_temperature_sensor_fault", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="outdoor_temperature_low", translation_key="outdoor_temperature_low", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="supply_temperature_low", translation_key="supply_temperature_low", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="fire_temperature_alarm", translation_key="fire_temperature_alarm", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="preheater_sensor_connected", translation_key="preheater_sensor_connected", device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_undervoltage_now", translation_key="pi_undervoltage_now", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_undervoltage_occurred", translation_key="pi_undervoltage_occurred", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_throttled_now", translation_key="pi_throttled_now", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_throttled_occurred", translation_key="pi_throttled_occurred", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_frequency_capped_now", translation_key="pi_frequency_capped_now", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_frequency_capped_occurred", translation_key="pi_frequency_capped_occurred", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_soft_temp_limit_now", translation_key="pi_soft_temp_limit_now", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="pi_soft_temp_limit_occurred", translation_key="pi_soft_temp_limit_occurred", device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
)


CONTROLLER_BINARY_SPECS = (
    ("hcp4_detected", "HCP4 registreret", BinarySensorDeviceClass.CONNECTIVITY, EntityCategory.DIAGNOSTIC, "mdi:remote"),
    ("hcp4_active", "HCP4 aktiv", None, EntityCategory.DIAGNOSTIC, "mdi:remote"),
    ("rs485_healthy", "RS485 sund", BinarySensorDeviceClass.CONNECTIVITY, EntityCategory.DIAGNOSTIC, "mdi:serial-port"),
    ("smart_inputs_online", "Smart Auto input online", BinarySensorDeviceClass.CONNECTIVITY, None, "mdi:home-assistant"),
    ("hardware_writes_allowed", "Pi hardware-writes tilladt", None, EntityCategory.DIAGNOSTIC, "mdi:shield-check-outline"),
    ("actual_afterheat", "Eftervarme faktisk aktiv", BinarySensorDeviceClass.HEAT, EntityCategory.DIAGNOSTIC, "mdi:radiator"),
)


class PassiveLinkBinarySensor(PassiveLinkEntity, BinarySensorEntity):
    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self):
        return bool(self.coordinator.data.get(self.key))


class ControllerStatusBinarySensor(ControllerEntity, BinarySensorEntity):
    def __init__(self, coordinator, key: str, name: str, device_class, category, icon: str) -> None:
        super().__init__(coordinator, key, name)
        self._attr_device_class = device_class
        self._attr_entity_category = category
        self._attr_icon = icon

    @property
    def is_on(self):
        value = self.controller_value
        return bool(value) if value is not None else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities = [
        PassiveLinkBinarySensor(coordinator, description)
        for description in DESCRIPTIONS
        if description.key not in PREHEATER_KEYS | PI_KEYS
        or coordinator._auxiliary_client is not None
    ]
    entities = drop_mk1_only(coordinator, entities)
    if coordinator.controller_client is not None:
        entities.extend(
            ControllerStatusBinarySensor(coordinator, key, name, device_class, category, icon)
            for key, name, device_class, category, icon in CONTROLLER_BINARY_SPECS
        )
    async_add_entities_when_ready(entry, coordinator, async_add_entities, entities)
