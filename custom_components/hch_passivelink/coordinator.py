"""Coordinator for Dantherm Control."""

import asyncio
import logging
import time
from typing import Any
from datetime import datetime, timedelta, timezone

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify

from .alarms import derive_alarm_values
from .const import DEFAULT_NAME, DOMAIN, PI_DIAGNOSTIC_KEYS
from .filter import filter_values
from .parser import TEMPERATURE_KEYS

SAMPLE_KEYS = (*TEMPERATURE_KEYS, "temperature_sample_monotonic", "temperature_source")

AUXILIARY_KEYS = {
    "preheater_flow_temperature",
    "preheater_return_temperature",
    "preheater_water_delta",
    "preheater_activity",
    "preheater_sensor_connected",
    *PI_DIAGNOSTIC_KEYS,
}

STORE_VERSION = 1
DEFAULT_FILTER_INTERVAL_DAYS = 360
HEALTH_ISSUE_DELAY = 900
EFFICIENCY_DROP_WARNING = 7.5
EFFICIENCY_DROP_DEGRADED = 12.5
ISSUE_CONNECTION_LOST = "connection_lost"
ISSUE_HAC1_DISCONNECTED = "hac1_disconnected"
_LOGGER = logging.getLogger(__name__)

class PassiveLinkCoordinator(DataUpdateCoordinator[dict[str, object]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client,
        entry_id: str,
        *,
        notify_enabled: bool,
        notify_days: int,
        notify_service: str,
        auxiliary_client=None,
    ) -> None:
        super().__init__(hass, logger=__import__("logging").getLogger(__name__), name=DEFAULT_NAME)
        self.data = {}
        self.client = client
        self.mk2_info = None
        self.task = None
        self._filter_store = Store(
            hass, STORE_VERSION, f"hch_passivelink.{entry_id}.filter_state"
        )
        self._filter_reset_epoch: float | None = None
        self._filter_interval_days: int | None = None
        self._filter_notification_reset_epoch: float | None = None
        self._night_mode: bool | None = None
        self._remove_filter_timer = None
        self._notify_enabled = notify_enabled
        self._notify_days = notify_days
        self._notify_service = notify_service.strip()
        self._notification_check_pending = False
        self._auxiliary_client = auxiliary_client
        self._remove_auxiliary_timer = None
        self._temperature_task = None
        self._temperature_sample = None
        self._pending_temperature_sample = None
        self._disconnected_since: float | None = None
        self._hac1_lost_since: float | None = None
        self._efficiency_reference: float | None = None
        self._filter_reset_history: list[float] = []

    async def async_load_filter_state(self) -> None:
        """Load filter state before entities are created."""
        stored = await self._filter_store.async_load()
        if isinstance(stored, dict):
            reset = stored.get("reset_epoch")
            interval = stored.get("interval_days")
            notified_reset = stored.get("notification_reset_epoch")
            night_mode = stored.get("night_mode")
            efficiency_reference = stored.get("efficiency_reference")
            reset_history = stored.get("filter_reset_history")
            if isinstance(night_mode, bool):
                self._night_mode = night_mode
                self.data["night_mode"] = night_mode
            if isinstance(reset, (int, float)) and isinstance(interval, int) and interval > 0:
                self._filter_reset_epoch = float(reset)
                self._filter_interval_days = interval
                if isinstance(notified_reset, (int, float)):
                    self._filter_notification_reset_epoch = float(notified_reset)
                self.data.update(filter_values(float(reset), interval))
                if isinstance(reset_history, list):
                    self._filter_reset_history = [
                        float(value)
                        for value in reset_history[-20:]
                        if isinstance(value, (int, float))
                    ]
                if not self._filter_reset_history:
                    self._filter_reset_history = [float(reset)]
                self._update_filter_history_values(self.data)
            if isinstance(efficiency_reference, (int, float)):
                self._efficiency_reference = float(efficiency_reference)
        self._remove_filter_timer = async_track_time_interval(
            self.hass, self._async_update_filter_clock, timedelta(hours=1)
        )
        self._schedule_notification_check()
    async def _async_update_auxiliary(self, sample) -> None:
        """Fetch water temperatures and Pi diagnostics, then publish together.

        The two are independent: a Pi without DS18B20 sensors attached can
        still report host diagnostics, and vice versa.
        """
        values = await self._auxiliary_client.async_fetch() or {}
        if sample.get("temperature_sample_monotonic") != self._temperature_sample:
            # Fast legacy air frames must not repeatedly cancel a slow water
            # fetch. Pair its result with the newest complete air frame instead.
            sample = getattr(self, "_pending_temperature_sample", None)
            if sample is None or sample.get("temperature_sample_monotonic") != self._temperature_sample:
                return
        merged = dict(self.data)
        merged.update(sample)
        merged.update(values)
        if "preheater_flow_temperature" in values:
            flow = values["preheater_flow_temperature"]
            return_temp = values["preheater_return_temperature"]
            delta = round(abs(flow - return_temp), 2)
            merged["preheater_water_delta"] = delta
            if delta < 0.5:
                merged["preheater_activity"] = "inactive"
            elif delta < 2.0:
                merged["preheater_activity"] = "low"
            elif delta <= 8.0:
                merged["preheater_activity"] = "normal"
            else:
                merged["preheater_activity"] = "high"
        else:
            merged.update(
                preheater_sensor_connected=False,
                preheater_flow_temperature=None,
                preheater_return_temperature=None,
                preheater_water_delta=None,
                preheater_activity=None,
            )
        self._update_derived_temperatures(merged)
        self.async_set_updated_data(merged)
    def _update_derived_temperatures(self, data: dict[str, object]) -> None:
        # TFAH is water/frost temperature, never the air-before operand.
        data["heating_coil_air_delta"] = None
        supply = data.get("supply_temperature")
        afterheat = data.get("heating_coil_after_temperature")
        if data.get("temperature_source") == "hac1_snapshot_180_209" and all(
            isinstance(value, (int, float)) for value in (supply, afterheat)
        ):
            data["heating_coil_air_delta"] = round(afterheat - supply, 2)
        data["supply_extract_delta"] = None
        data["heat_recovery_efficiency"] = None
        data["heat_recovery_status"] = None
        data["heat_recovery_drop"] = None
        data["heat_recovery_trend"] = None
        supply = data.get("supply_temperature")
        extract = data.get("extract_temperature")
        exhaust = data.get("exhaust_temperature")
        outdoor = data.get("outdoor_temperature")
        if outdoor is not None:
            data["outdoor_temperature_source"] = "unit_sensor"
        if isinstance(supply, (int, float)) and isinstance(extract, (int, float)):
            data["supply_extract_delta"] = round(supply - extract, 1)
        if all(isinstance(value, (int, float)) for value in (outdoor, extract, exhaust)):
            span = extract - outdoor
            if data.get("bypass_active") or span < 2:
                data["heat_recovery_efficiency"] = None
            else:
                data["heat_recovery_efficiency"] = round((extract - exhaust) / span * 100, 1)
        efficiency = data.get("heat_recovery_efficiency")
        if isinstance(efficiency, (int, float)):
            if efficiency >= 85:
                data["heat_recovery_status"] = "good"
            elif efficiency >= 70:
                data["heat_recovery_status"] = "acceptable"
            else:
                data["heat_recovery_status"] = "low"
            if self._efficiency_reference is None:
                self._efficiency_reference = float(efficiency)
            data["heat_recovery_reference"] = round(self._efficiency_reference, 1)
            drop = self._efficiency_reference - efficiency
            data["heat_recovery_drop"] = round(max(0.0, drop), 1)
            if drop >= EFFICIENCY_DROP_DEGRADED:
                data["heat_recovery_trend"] = "degraded"
            elif drop >= EFFICIENCY_DROP_WARNING:
                data["heat_recovery_trend"] = "watch"
            else:
                data["heat_recovery_trend"] = "normal"
        extract_percent = data.get("extract_fan_percent")
        supply_percent = data.get("supply_fan_percent")
        if isinstance(extract_percent, (int, float)) and isinstance(supply_percent, (int, float)):
            data["fan_control_delta"] = round(supply_percent - extract_percent, 1)
        extract_rpm = data.get("extract_fan_rpm")
        supply_rpm = data.get("supply_fan_rpm")
        if isinstance(extract_rpm, (int, float)) and isinstance(supply_rpm, (int, float)):
            data["fan_rpm_delta"] = round(supply_rpm - extract_rpm)
        data.update(derive_alarm_values(data))

    def _update_filter_history_values(self, data: dict[str, object]) -> None:
        if not self._filter_reset_history:
            return
        data["filter_last_change"] = datetime.fromtimestamp(
            self._filter_reset_history[-1], timezone.utc
        ).isoformat()
        data["filter_change_count"] = len(self._filter_reset_history)

    def co2_offset(self) -> int:
        """CO2 calibration offset in ppm; the controller coordinator supplies it."""
        return 0

    def _apply_co2_calibration(self, data: dict[str, object]) -> None:
        # The raw RS485 stream carries the sensor's own reading. Show the value
        # corrected by the calibration set in HCH5 Control, as the Pi does.
        co2 = data.get("co2")
        offset = self.co2_offset()
        if offset and isinstance(co2, (int, float)) and not isinstance(co2, bool):
            data["co2"] = max(0, co2 + offset)

    def _update_air_quality(self, data: dict[str, object]) -> None:
        co2 = data.get("co2")
        if not isinstance(co2, (int, float)):
            return
        if co2 < 800:
            data["air_quality_index"] = "good"
        elif co2 <= 1200:
            data["air_quality_index"] = "moderate"
        else:
            data["air_quality_index"] = "poor"

    def _update_health_issues(self) -> None:
        now = time.monotonic()
        connected = self.client.connected
        if connected:
            self._disconnected_since = None
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_CONNECTION_LOST)
        else:
            self._disconnected_since = self._disconnected_since or now
            if now - self._disconnected_since > HEALTH_ISSUE_DELAY:
                ir.async_create_issue(
                    self.hass, DOMAIN, ISSUE_CONNECTION_LOST,
                    is_fixable=False, severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_CONNECTION_LOST,
                )
        hac1_connected = self.data.get("hac1_connected")
        if not connected or hac1_connected is not False:
            self._hac1_lost_since = None
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_HAC1_DISCONNECTED)
        else:
            self._hac1_lost_since = self._hac1_lost_since or now
            if now - self._hac1_lost_since > HEALTH_ISSUE_DELAY:
                ir.async_create_issue(
                    self.hass, DOMAIN, ISSUE_HAC1_DISCONNECTED,
                    is_fixable=False, severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_HAC1_DISCONNECTED,
                )

    async def _async_save_filter_state(self) -> None:
        data: dict[str, object] = {}
        if self._filter_reset_epoch is not None and self._filter_interval_days is not None:
            data.update(
                reset_epoch=self._filter_reset_epoch,
                interval_days=self._filter_interval_days,
            )
        if self._filter_notification_reset_epoch is not None:
            data["notification_reset_epoch"] = self._filter_notification_reset_epoch
        if self._night_mode is not None:
            data["night_mode"] = self._night_mode
        if self._efficiency_reference is not None:
            data["efficiency_reference"] = self._efficiency_reference
        if self._filter_reset_history:
            data["filter_reset_history"] = self._filter_reset_history[-20:]
        if data:
            await self._filter_store.async_save(data)

    def async_handle_update(self, data: dict[str, object]) -> None:
        """Merge traffic; publish temperature samples only as a complete batch."""
        if self._auxiliary_client is not None:
            sample_id = data.get("temperature_sample_monotonic")
            if sample_id is not None and sample_id != self._temperature_sample:
                self._temperature_sample = sample_id
                sample = {key: data[key] for key in SAMPLE_KEYS if key in data}
                self._pending_temperature_sample = sample
                if self._temperature_task is None or self._temperature_task.done():
                    self._temperature_task = self.hass.async_create_task(
                        self._async_update_auxiliary(sample)
                    )
            # Unrelated bus updates must not leak a pending temperature sample.
            data = dict(data)
            for key in SAMPLE_KEYS:
                if key in self.data:
                    data[key] = self.data[key]
                else:
                    data.pop(key, None)
        self._sync_unit_filter(data)
        interval = data.get("filter_interval_days")
        night_mode = data.get("night_mode")
        if isinstance(night_mode, bool) and night_mode != self._night_mode:
            self._night_mode = night_mode
            self.hass.async_create_task(self._async_save_filter_state())
        if isinstance(interval, int) and interval > 0:
            changed = interval != self._filter_interval_days
            self._filter_interval_days = interval
            if changed and self._filter_reset_epoch is not None:
                self.hass.async_create_task(self._async_save_filter_state())
        merged = dict(data)
        for key in AUXILIARY_KEYS:
            if key in self.data:
                merged[key] = self.data[key]
        if self._night_mode is not None:
            merged.setdefault("night_mode", self._night_mode)
        if merged.get("unit_generation") == "mk2":
            pass  # the MKII counts its own filter time; never overwrite it with the local timer
        elif self._filter_reset_epoch is not None and self._filter_interval_days is not None:
            merged.update(filter_values(self._filter_reset_epoch, self._filter_interval_days))
            self._apply_unit_filter(merged)
        self._apply_co2_calibration(merged)
        self._update_derived_temperatures(merged)
        self._update_filter_history_values(merged)
        self._update_air_quality(merged)
        self.async_set_updated_data(merged)
        self._schedule_notification_check()
        self._update_health_issues()

    def _sync_unit_filter(self, data: dict[str, object]) -> None:
        """Follow the unit's own filter counter (block 1024) when the bus carries it.

        The counter is reset with the button on the unit, so a new start of its
        hour count is a filter change: it joins the history and re-arms the
        reminder, exactly like the reset button in Home Assistant.
        """
        hours, months = data.get("filter_unit_hours"), data.get("filter_unit_period_months")
        if not isinstance(hours, int) or not isinstance(months, int):
            return
        reset = time.time() - hours * 3600
        interval = round(months * 730 / 24)
        changed = False
        if self._filter_reset_epoch is None or abs(reset - self._filter_reset_epoch) > 2 * 3600:
            if self._filter_reset_epoch is not None and reset > self._filter_reset_epoch:
                self._filter_reset_history.append(reset)
                self._filter_reset_history = self._filter_reset_history[-20:]
                self._filter_notification_reset_epoch = None
            self._filter_reset_epoch = reset
            changed = True
        if interval != self._filter_interval_days:
            self._filter_interval_days = interval
            changed = True
        if not self._filter_reset_history:
            self._filter_reset_history = [reset]
        if changed:
            self.hass.async_create_task(self._async_save_filter_state())

    def _apply_unit_filter(self, data: dict[str, object]) -> None:
        life = data.get("filter_unit_life_raw", self.data.get("filter_unit_life_raw"))
        if isinstance(life, int):
            data["filter_life_percent"] = max(0, min(100, round(life * 100 / 255)))
            data["filter_source"] = "unit"

    def async_reset_filter(self) -> None:
        """Reset the filter cycle to now, keeping the last known interval."""
        self._filter_reset_epoch = time.time()
        if self._filter_interval_days is None:
            self._filter_interval_days = DEFAULT_FILTER_INTERVAL_DAYS
        self._filter_notification_reset_epoch = None
        self._filter_reset_history.append(self._filter_reset_epoch)
        self._filter_reset_history = self._filter_reset_history[-20:]
        merged = dict(self.data)
        merged.update(filter_values(self._filter_reset_epoch, self._filter_interval_days))
        self._update_filter_history_values(merged)
        self.async_set_updated_data(merged)
        self.hass.async_create_task(self._async_save_filter_state())
        self._schedule_notification_check()

    async def _async_update_filter_clock(self, _now) -> None:
        if self._filter_reset_epoch is None or self._filter_interval_days is None:
            return
        if self.data.get("unit_generation") == "mk2":
            return
        merged = dict(self.data)
        merged.update(filter_values(self._filter_reset_epoch, self._filter_interval_days))
        self._apply_unit_filter(merged)
        efficiency = merged.get("heat_recovery_efficiency")
        if isinstance(efficiency, (int, float)):
            previous = self._efficiency_reference
            if previous is None:
                self._efficiency_reference = float(efficiency)
            elif efficiency > previous:
                self._efficiency_reference = previous * 0.98 + float(efficiency) * 0.02
            else:
                self._efficiency_reference = previous * 0.999 + float(efficiency) * 0.001
            if previous is None or abs(self._efficiency_reference - previous) >= 0.05:
                self.hass.async_create_task(self._async_save_filter_state())
            self._update_derived_temperatures(merged)
        self.async_set_updated_data(merged)
        self._schedule_notification_check()

    def _schedule_notification_check(self) -> None:
        if self._notification_check_pending:
            return
        self._notification_check_pending = True
        self.hass.async_create_task(self._async_maybe_notify_filter())

    def _mobile_app_service(self, entity_id: str) -> str | None:
        """The notify.mobile_app_* service behind a phone's notify entity."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != "mobile_app":
            return None
        names = [entity_id.split(".", 1)[1]]
        device = dr.async_get(self.hass).async_get(entry.device_id) if entry.device_id else None
        if device is not None:
            names.insert(0, slugify(device.name_by_user or device.name or ""))
        for name in names:
            if name and self.hass.services.has_service("notify", f"mobile_app_{name}"):
                return f"mobile_app_{name}"
        return None

    async def _async_maybe_notify_filter(self) -> None:
        try:
            if not self._notify_enabled or self._filter_reset_epoch is None:
                return
            remaining = self.data.get("filter_days_remaining")
            if not isinstance(remaining, int) or remaining > self._notify_days:
                return
            if self._filter_notification_reset_epoch == self._filter_reset_epoch:
                return
            if self.hass.config.language.startswith("da"):
                title = "Dantherm-filter skal snart skiftes"
                message = (
                    f"Der er {remaining} dage tilbage af filterintervallet. "
                    "Kontrollér eller udskift filteret i Dantherm-anlægget."
                )
            else:
                title = "Dantherm filter change due soon"
                message = (
                    f"The filter interval has {remaining} days remaining. "
                    "Check or replace the filter in the Dantherm unit."
                )
            persistent_notification.async_create(
                self.hass,
                message,
                title=title,
                notification_id="hch_passivelink_filter_change",
            )
            if self._notify_service:
                if "." not in self._notify_service:
                    _LOGGER.warning(
                        "Invalid filter notification service: %s",
                        self._notify_service,
                    )
                else:
                    domain, service = self._notify_service.split(".", 1)
                    if domain == "notify" and not self.hass.services.has_service(domain, service):
                        # A phone's notify entity (notify.<phone>): its mobile_app service.
                        service = self._mobile_app_service(self._notify_service) or service
                    service_data: dict[str, Any] = {"title": title, "message": message}
                    if domain == "notify" and service.startswith("mobile_app_"):
                        # The phone shows a filter icon (on iOS a communication
                        # notification) instead of the Home Assistant icon.
                        service_data["data"] = {
                            "tag": "hch_passivelink_filter_change",
                            "notification_icon": "mdi:air-filter",
                            "notification_icon_color": "white",
                            "color": "#0ea5e9",
                        }
                    if self.hass.services.has_service(domain, service):
                        await self.hass.services.async_call(
                            domain,
                            service,
                            service_data,
                            blocking=False,
                        )
                    else:
                        _LOGGER.warning(
                            "Filter notification service is unavailable: %s",
                            self._notify_service,
                        )
            self._filter_notification_reset_epoch = self._filter_reset_epoch
            await self._async_save_filter_state()
        finally:
            self._notification_check_pending = False

    async def async_shutdown(self) -> None:
        if self._temperature_task is not None:
            self._temperature_task.cancel()
            try:
                await self._temperature_task
            except asyncio.CancelledError:
                pass
        if self._remove_filter_timer is not None:
            self._remove_filter_timer()
            self._remove_filter_timer = None
        if self._remove_auxiliary_timer is not None:
            self._remove_auxiliary_timer()
            self._remove_auxiliary_timer = None

    @property
    def available(self) -> bool:
        return self.client.connected and bool(self.data)
