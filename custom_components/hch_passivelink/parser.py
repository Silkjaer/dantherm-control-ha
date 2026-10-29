"""Passive Modbus RTU stream parser for Dantherm HCH5 MK1 + HAC1."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

KNOWN_SLAVES = {1, 0x40}
TEMPERATURE_KEYS = (
    "outdoor_temperature", "supply_temperature", "extract_temperature",
    "exhaust_temperature", "room_temperature",
    "heating_coil_after_temperature", "heating_coil_before_temperature",
)
LEVEL_TARGETS = {
    "off": (0, 0), "level_1": (25, 13), "level_2": (55, 43),
    "level_3": (85, 73), "boost": (100, 88),
}
# T5 outside the HAC1 snapshot is renewed every few seconds by HCP4 (or every
# 15 s by a gateway reading register 184). One older than this is stale.
ROOM_TEMPERATURE_MAX_AGE = 300


def temperature_value(raw: int) -> float | None:
    """Decode signed hundredths, including sensor fault sentinels."""
    if raw in (0x7FFF, 0x8000):
        return None
    value = (raw - 65536 if raw >= 32768 else raw) / 100.0
    return value if -35 <= value <= 100 else None


def crc16(data: bytes) -> int:
    """Return Modbus CRC-16."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class RtuStreamParser:
    """Reassemble verified RTU frames from arbitrary TCP chunks."""

    def __init__(self, callback: Callable[[bytes], None]) -> None:
        self._callback = callback
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)
        offset = 0
        while offset + 5 <= len(self._buffer):
            if self._buffer[offset] not in KNOWN_SLAVES:
                offset += 1
                continue
            function = self._buffer[offset + 1]
            lengths: list[int] = []
            if function in (3, 4):
                lengths.append(8)
                byte_count = self._buffer[offset + 2]
                if byte_count and byte_count <= 250 and byte_count % 2 == 0:
                    lengths.insert(0, 5 + byte_count)
            elif function == 6:
                lengths.append(8)
            elif function == 16:
                # A write-multiple-registers response is 8 bytes. A request
                # carries a byte count at offset 6 and is 9 + byte_count bytes.
                # Try the longer request first so its payload cannot be
                # discarded while passively observing both bus directions.
                if offset + 7 <= len(self._buffer):
                    byte_count = self._buffer[offset + 6]
                    if byte_count and byte_count <= 250 and byte_count % 2 == 0:
                        lengths.append(9 + byte_count)
                lengths.append(8)
            else:
                offset += 1
                continue
            frame = None
            for length in lengths:
                if offset + length > len(self._buffer):
                    continue
                candidate = bytes(self._buffer[offset : offset + length])
                if crc16(candidate[:-2]) == int.from_bytes(candidate[-2:], "little"):
                    frame = candidate
                    break
            if frame is None:
                if any(offset + length > len(self._buffer) for length in lengths):
                    break
                offset += 1
                continue
            self._callback(frame)
            offset += len(frame)
        if offset:
            del self._buffer[:offset]
        if len(self._buffer) > 1024:
            del self._buffer[:-16]


class DanthermDecoder:
    """Decode only values verified on the observed HCH5 MK1/HAC1 bus."""

    def __init__(self, on_update: Callable[[dict[str, object]], None]) -> None:
        self.data: dict[str, object] = {}
        self._on_update = on_update
        self._special_mode_flag: int | None = None
        self._last_explicit_command = float("-inf")
        self._night_transition_until = 0.0
        self._night_transition_registers: set[int] = set()
        self._filter_command_until = 0.0
        self._pending_auto_until = 0.0
        self._pending_manual3_until = 0.0
        self._pending_manual_until = 0.0
        self._pending_mode_start_pair: tuple[int, int] | None = None
        self._last_explicit_command_value: int | None = None
        self._external_mode: str | None = None
        self._frame_times: deque[float] = deque()
        self._snapshot_mode = False
        self._snapshot_at = 0.0
        self._snapshot_request_at: float | None = None
        self._room_temperature_at: float | None = None

    def _set(self, **values: object) -> None:
        changed = False
        for key, value in values.items():
            if self.data.get(key) != value:
                self.data[key] = value
                changed = True
        if changed:
            self._on_update(dict(self.data))

    @staticmethod
    def _room_relative_humidity(
        measured_rh: float, sensor_temperature: float, room_temperature: float
    ) -> float:
        """Compensate sensor RH to the extract/room-air temperature."""
        def saturation_pressure(temperature: float) -> float:
            return 6.112 * math.exp(
                17.62 * temperature / (243.12 + temperature)
            )

        return measured_rh * saturation_pressure(sensor_temperature) \
            / saturation_pressure(room_temperature)

    def decode(self, frame: bytes) -> None:
        now = time.monotonic()
        if self._snapshot_mode and now - self._snapshot_at > 30:
            self._set(**dict.fromkeys(TEMPERATURE_KEYS), temperature_sample_monotonic=now)
            self._snapshot_at = now
        if self._room_temperature_at is not None \
                and now - self._room_temperature_at > ROOM_TEMPERATURE_MAX_AGE:
            # The unit's own frames keep coming when HCP4/HRC2 goes quiet, so
            # without this the last T5 would stay a valid room temperature.
            self._room_temperature_at = None
            if not self._snapshot_mode:
                self._set(room_temperature=None)
        self._frame_times.append(now)
        while self._frame_times and now - self._frame_times[0] > 60:
            self._frame_times.popleft()
        self._set(
            last_frame_monotonic=now,
            bus_traffic=True,
            bus_frame_rate=len(self._frame_times),
        )
        slave, function = frame[0], frame[1]
        if slave == 0x40 and function == 3 and len(frame) == 8:
            self._snapshot_request_at = (
                now if frame[2:6] == bytes.fromhex("00b4001e") else None
            )
        if slave == 0x40 and function == 3 and len(frame) == 65 and frame[2] == 60:
            requested = self._snapshot_request_at
            self._snapshot_request_at = None
            if requested is None or now - requested > 2:
                return
            values = [int.from_bytes(frame[i:i + 2], "big") for i in range(3, 63, 2)]
            self._snapshot_mode = True
            self._snapshot_at = now
            temperatures = dict(zip(TEMPERATURE_KEYS, (
                temperature_value(values[i]) for i in (0, 1, 2, 3, 4, 25, 26)
            )))
            # T5 (register 184) is the HRC2 remote's sensor. Without an HRC2
            # the unit reports 0 after a power cut, which is no room value.
            room = temperatures.get("room_temperature")
            # Missing HRC2 is not a sensor fault: only the unit's fault words
            # (decoded to None by temperature_value) raise that alarm.
            hrc2_missing = room is not None and not 5 <= room <= 40
            if hrc2_missing:
                temperatures["room_temperature"] = None
            self._set(
                **temperatures,
                room_sensor_missing=hrc2_missing,
                # Register 209 is a verified binary afterheat flag: 0=off,
                # 16=on. It is not a valve-opening percentage.
                afterheat_active=values[29] == 16,
                temperature_sample_monotonic=now,
                temperature_source="hac1_snapshot_180_209",
            )
            return
        if slave == 0x40 and function == 16 and len(frame) >= 19:
            register = int.from_bytes(frame[2:4], "big")
            count = int.from_bytes(frame[4:6], "big")
            byte_count = frame[6]
            if register == 185 and count == 5 and byte_count == 10:
                values = [
                    int.from_bytes(frame[i:i + 2], "big")
                    for i in range(7, 17, 2)
                ]
                if values[0] & 0xFF == 1 and values[2] == 15 \
                        and values[1] % 256 == 0 \
                        and 5 <= values[1] // 256 <= 40:
                    self._set(afterheat_setpoint=values[1] // 256)
            elif register == 180 and count == 5 and byte_count == 10:
                values = [
                    int.from_bytes(frame[i:i + 2], "big")
                    for i in range(7, 17, 2)
                ]
                # Registers 182 and 183 are the two optional thermostat
                # setpoints. HCP4 writes 0x8000 when a setpoint is OFF.
                def thermostat_value(value: int) -> str:
                    return "off" if value == 0x8000 else str(value)

                if all(value == 0x8000 or 5 <= value <= 40 for value in values[2:4]):
                    self._set(
                        afterheat_room_setpoint=thermostat_value(values[2]),
                        afterheat_extract_setpoint=thermostat_value(values[3]),
                    )
                elif 500 <= values[2] <= 4500:
                    # Without HAC1, HCP4 writes T1..T5 here in hundredths of
                    # a degree instead (HCH5 MK1, verified 2026-10-28: T1..T4
                    # matched the unit's own readings within 0.1 °C and T5
                    # matched the HRC2 display). T3 is extract air, so it can
                    # never look like a thermostat setpoint. T5 is the HRC2
                    # room sensor, the only house temperature on such a bus.
                    room = values[4] / 100.0
                    hrc2_missing = not 5 <= room <= 40
                    if not self._snapshot_mode:
                        self._room_temperature_at = now
                        self._set(
                            room_temperature=None if hrc2_missing else room,
                            room_sensor_missing=hrc2_missing,
                        )
            return
        if function in (3, 4) and len(frame) == 8:
            return
        if slave == 1 and function == 3 and len(frame) == 17 and frame[2] == 12:
            # Block 1024 (the only six-word holding read from slave 1) is the
            # unit's own filter counter, verified 2026-10-02 with the reset
            # button on the unit: [1, period months, life (255 = 100 %),
            # hours since reset, 0, 0].
            values = [int.from_bytes(frame[i:i + 2], "big") for i in range(3, 15, 2)]
            if values[0] == 1 and 3 <= values[1] <= 12 and values[2] <= 255 and values[3] <= 20000:
                self._set(
                    filter_unit_period_months=values[1],
                    filter_unit_life_raw=values[2],
                    filter_unit_hours=values[3],
                )
            return
        if slave == 0x40 and function == 3 and len(frame) == 7 and frame[2] == 2:
            # Register 184 er HRC2-fjernbetjeningens egen temperaturføler
            # (T5). Verificeret 2026-08-28 mod HRC2-displayet (rå 2190 =
            # 21.90 stemte med skærmens 21 grader). Dette register sendes
            # aldrig passivt af sig selv - gatewayen forespørger det aktivt
            # hvert 15. sekund og genudsender svaret på den rå TCP-strøm.
            raw = int.from_bytes(frame[3:5], "big")
            if not self._snapshot_mode and 500 <= raw <= 4000:
                self._room_temperature_at = now
                self._set(room_temperature=raw / 100.0)
            return
        if slave == 0x40 and function == 3 and len(frame) == 15 and frame[2] == 10:
            values = [int.from_bytes(frame[i:i + 2], "big") for i in range(3, 13, 2)]
            if values[:3] == [0x3000, 0x1100, 0] and 300 <= values[3] <= 10000:
                self._set(co2=values[3], hac1_connected=True)
            elif values[0] & 0xFF == 1 and values[2] == 15 and values[1] % 256 == 0 \
                    and 5 <= values[1] // 256 <= 40:
                self._set(afterheat_setpoint=values[1] // 256)
            elif values[4] == 0 and all(
                value == 0x8000 or 5 <= value <= 40 for value in values[2:4]
            ):
                # The regularly read register 180 block repeats the two
                # optional thermostat states, so OFF remains observable even
                # when PassiveLink missed the original HCP4 write request.
                # values[1] ("before coil") blev fysisk verificeret 2026-08-29
                # ved at afbryde følerne enkeltvis: den er IKKE en luftføler
                # før varmefladen, men eftervarmefladens indbyggede
                # antifrost/frostsikringsføler på vandsiden (retur). Der
                # findes derfor ingen gyldig luft-ΔT over fladen fra disse
                # to registre.
                self._set(
                    **({} if self._snapshot_mode else {
                        "heating_coil_after_temperature": temperature_value(values[0]),
                        "heating_coil_before_temperature": temperature_value(values[1]),
                    }),
                    afterheat_room_setpoint=(
                        "off" if values[2] == 0x8000 else str(values[2])
                    ),
                    afterheat_extract_setpoint=(
                        "off" if values[3] == 0x8000 else str(values[3])
                    ),
                )
            return
        if slave != 1:
            return
        if function == 4:
            values = [int.from_bytes(frame[i:i + 2], "big") for i in range(3, len(frame) - 2, 2)]
            if len(values) == 4:
                if self._snapshot_mode:
                    return
                keys = ("outdoor_temperature", "supply_temperature", "extract_temperature", "exhaust_temperature")
                # Legacy gateways: publish the whole frame atomically too.
                self._set(
                    **{key: temperature_value(raw) for key, raw in zip(keys, values)},
                    temperature_sample_monotonic=now,
                )
            elif len(values) == 5:
                raw, extract_rpm, supply_rpm, bypass_raw, status_code = values
                measured_rh = round(raw * 100 / 255, 1) if 0 < raw <= 255 else None
                extract_temperature = self.data.get("extract_temperature")
                exhaust_temperature = self.data.get("exhaust_temperature")
                calculated_rh = None
                if measured_rh is not None \
                        and isinstance(extract_temperature, (int, float)) \
                        and isinstance(exhaust_temperature, (int, float)):
                    calculated_rh = round(self._room_relative_humidity(
                        measured_rh, exhaust_temperature, extract_temperature
                    ), 1)
                self._set(
                    # Danfoss keeps the RH measured at the sensor's local
                    # temperature and displays the room-equivalent RH after
                    # compensating it to the extract-air temperature.
                    measured_relative_humidity=measured_rh,
                    relative_humidity=calculated_rh,
                    heat_recovery_raw=raw, extract_fan_rpm=extract_rpm,
                    supply_fan_rpm=supply_rpm, bypass_raw=bypass_raw,
                    bypass_active=bypass_raw == 255, status_code=status_code,
                )
            return
        if function != 6:
            return
        register = int.from_bytes(frame[2:4], "big")
        value = int.from_bytes(frame[4:6], "big")
        now = time.monotonic()
        if register == 66:
            self._set(extract_fan_percent=value)
            if now <= self._night_transition_until:
                self._night_transition_registers.add(register)
        elif register == 67:
            self._set(supply_fan_percent=value)
            if now <= self._night_transition_until:
                self._night_transition_registers.add(register)
        elif register == 68:
            self._set(afterheat_raw=value)
        elif register == 76:
            self._special_mode_flag = value
        elif register == 143 and value:
            self._set(command_raw=value)
            if value in (189, 200, 205):
                self._last_explicit_command = now
                self._last_explicit_command_value = value
            elif value == 172 and now - self._last_explicit_command > 10:
                self._night_transition_until = now + 2
                self._night_transition_registers.clear()
            if value == 168:
                self._filter_command_until = now + 2
            if value == 172:
                explicit_auto = (
                    self._last_explicit_command_value in (200, 205)
                    and now - self._last_explicit_command <= 2
                )
                self._pending_auto_until = now + 5 if explicit_auto else 0
                self._pending_manual_until = 0 if explicit_auto else now + 5
                self._pending_manual3_until = 0
                self._pending_mode_start_pair = self._fan_pair()
                if explicit_auto:
                    self._external_mode = "auto"
            elif value == 189:
                self._pending_manual3_until, self._pending_auto_until = now + 5, 0
                self._pending_mode_start_pair = self._fan_pair()
        elif register == 168 and now <= self._filter_command_until and 3 <= value <= 12:
            self._set(filter_interval_days=value * 30)
        elif register == 146:
            # Observed connected states include both 1 and 3; zero is the
            # disconnected state.
            self._set(hac1_connected=value != 0)
        # NB: register 147 blev tidligere brugt til room_temperature, men er
        # verificeret 2026-08-28 til blot at spejle indblæsningstemperaturen
        # som fallback, når ingen ægte rumføler er tilsluttet. Den ægte kilde
        # er nu register 184 (slave 0x40, HRC2/T5) håndteret ovenfor.
        self._update_mode(now)

    def _update_mode(self, now: float) -> None:
        extract = self.data.get("extract_fan_percent")
        supply = self.data.get("supply_fan_percent")
        if not isinstance(extract, int) or not isinstance(supply, int):
            return
        if now <= self._night_transition_until and self._night_transition_registers == {66, 67}:
            self._set(night_mode=(extract, supply) == (25, 13))
            self._night_transition_until = 0
            self._night_transition_registers.clear()
        manual3_distance = (extract - 85) ** 2 + (supply - 73) ** 2
        pair_changed = self._pending_mode_start_pair is not None and (
            (extract - self._pending_mode_start_pair[0]) ** 2
            + (supply - self._pending_mode_start_pair[1]) ** 2 > 16
        )
        manual_pair_mode = {
            (25, 13): "manual_1", (55, 43): "manual_2"
        }.get((extract, supply))
        if now <= self._pending_manual_until and pair_changed and manual_pair_mode:
            self._external_mode = manual_pair_mode
            self._pending_manual_until = 0.0
            self._pending_mode_start_pair = None
        if now <= self._pending_manual3_until and pair_changed and manual3_distance <= 100:
            self._external_mode = "manual_3"
            self._pending_manual3_until = 0.0
            self._pending_mode_start_pair = None
        if self._external_mode == "manual_3" and now <= self._pending_auto_until \
                and pair_changed and manual3_distance > 100:
            self._external_mode = "auto"
            self._pending_auto_until = 0.0
            self._pending_mode_start_pair = None
        fireplace = extract == 0 and supply > 0 and self._special_mode_flag == 1
        standby = extract == 0 and supply == 0 and self._special_mode_flag == 1
        current_level = min(
            LEVEL_TARGETS,
            key=lambda name: (extract - LEVEL_TARGETS[name][0]) ** 2
            + (supply - LEVEL_TARGETS[name][1]) ** 2,
        )
        if fireplace:
            mode = "fireplace"
        elif standby:
            mode = "standby"
        elif (extract, supply) == (100, 88):
            mode = "auto_or_boost"
        elif self._external_mode in ("manual_1", "manual_2", "manual_3"):
            mode = self._external_mode
        elif self._external_mode == "auto":
            mode = "auto_or_scheduled"
        else:
            # 85/73 is also a normal automatic setpoint. Only label it
            # manual_3 after an observed command followed by a changed pair.
            mode = {(25, 13): "manual_1", (55, 43): "manual_2"}.get(
                (extract, supply), "auto_or_scheduled"
            )
        self._set(
            fireplace=fireplace, standby=standby, current_level=current_level,
            operating_mode=mode,
        )

    def _fan_pair(self) -> tuple[int, int] | None:
        extract = self.data.get("extract_fan_percent")
        supply = self.data.get("supply_fan_percent")
        if isinstance(extract, int) and isinstance(supply, int):
            return extract, supply
        return None
