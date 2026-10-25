"""Dantherm HCH5 MKII over Modbus (the "Dantherm Residential" register map).

Shared by the Home Assistant integration and the HCH5 Control gateway; uses
only the standard library. MK1/HAC1 installations are not affected.

Register addresses, data types, unit modes and alarm codes follow the
Tvalley71/dantherm integration (https://github.com/Tvalley71/dantherm),
Apache License 2.0. See NOTICE.

Values are 32-bit: two holding registers, low word first. Temperatures and
fan speeds are float32, everything else uint32.
"""

from __future__ import annotations

from dataclasses import dataclass
import socket
import struct
import threading
import time
from typing import Any, Protocol

# --- register map -------------------------------------------------------------
REG_SYSTEM_ID = 2
REG_SERIAL_NUMBER = 4
REG_FIRMWARE_VERSION = 24
REG_FAN1_SPEED = 100  # rpm, float
REG_FAN2_SPEED = 102
REG_OUTDOOR_TEMP = 132  # float °C (T1)
REG_SUPPLY_TEMP = 134  # T2
REG_EXTRACT_TEMP = 136  # T3
REG_EXHAUST_TEMP = 138  # T4
REG_ROOM_TEMP = 140  # T5 (room sensor / HRC)
REG_PREHEATER_DUTYCYCLE = 160
REG_ACTIVE_MODE = 168  # write: mode commands below; read: ACTIVE_* bits
REG_HUMIDITY = 196  # % RH
REG_BYPASS_DAMPER = 198  # BYPASS_* states
REG_MANUAL_BYPASS_DURATION = 264
REG_FAN_LEVEL = 324  # 0..4
REG_NIGHT_START_HOUR = 332
REG_NIGHT_START_MINUTE = 334
REG_NIGHT_END_HOUR = 336
REG_NIGHT_END_MINUTE = 338
REG_HUMIDITY_SETPOINT = 340
REG_AIR_QUALITY = 430
REG_AIR_QUALITY_LOW = 562
REG_AIR_QUALITY_MIDDLE = 564
REG_AIR_QUALITY_HIGH = 566
REG_WEEK_PROGRAM = 466
REG_CURRENT_MODE = 472  # read: MODE_* below
REG_ALARM_RESET = 514
REG_ALARM = 516
REG_FILTER_REMAIN = 554  # days
REG_FILTER_LIFETIME = 556  # days
REG_FILTER_RESET = 558
REG_SYSTEM_COMPONENTS = 610
REG_FILTER_DIRTINESS = 612
REG_WORK_TIME = 624  # hours

DEVICE_TYPE_HCH5_MK2 = 23

# Current unit mode (REG_CURRENT_MODE).
MODE_STANDBY = 0
MODE_MANUAL = 1
MODE_AUTOMATIC = 2
MODE_WEEK_PROGRAM = 3
MODE_AWAY = 5
MODE_SUMMER = 6
MODE_FIREPLACE = 9
MODE_NIGHT = 16
MODE_NAMES = {
    MODE_STANDBY: "standby",
    MODE_MANUAL: "manual",
    MODE_AUTOMATIC: "automatic",
    MODE_WEEK_PROGRAM: "week_program",
    MODE_AWAY: "away",
    MODE_SUMMER: "summer",
    MODE_FIREPLACE: "fireplace",
    MODE_NIGHT: "night",
}

# Commands written to REG_ACTIVE_MODE.
CMD_AUTOMATIC = 0x0002
CMD_MANUAL = 0x0004
CMD_WEEK_PROGRAM = 0x0008
CMD_START_AWAY = 0x0010
CMD_END_AWAY = 0x8010
CMD_NIGHT_ON = 0x0020
CMD_NIGHT_OFF = 0x8020
CMD_START_FIREPLACE = 0x0040
CMD_END_FIREPLACE = 0x8040
CMD_BYPASS_OPEN = 0x0080
CMD_BYPASS_CLOSE = 0x8080
CMD_START_SUMMER = 0x0800
CMD_END_SUMMER = 0x8800

# Bits read back from REG_ACTIVE_MODE.
ACTIVE_MANUAL_BYPASS = 0x0080  # manual bypass selected (set at once; the damper follows)

# Bypass damper state (REG_BYPASS_DAMPER) – 255 means open, as on the MK1.
BYPASS_CLOSED = 0
BYPASS_IN_PROGRESS = 1
BYPASS_CLOSING = 32
BYPASS_OPENING = 64
BYPASS_OPENED = 255
BYPASS_STATES = {
    BYPASS_CLOSED: "closed",
    BYPASS_IN_PROGRESS: "in_progress",
    BYPASS_CLOSING: "closing",
    BYPASS_OPENING: "opening",
    BYPASS_OPENED: "open",
}

# Component bits (REG_SYSTEM_COMPONENTS).
COMPONENT_BYPASS = 0x0004
COMPONENT_PREHEATER = 0x0010
COMPONENT_RH = 0x0040
COMPONENT_VOC = 0x0080
COMPONENT_HAC1 = 0x0200

ALARMS = {
    0: "none",
    1: "exhaust_air_fan",
    2: "supply_air_fan",
    3: "bypass_damper",
    4: "outdoor_air_sensor",
    5: "supply_air_sensor",
    6: "extract_air_sensor",
    7: "exhaust_air_sensor",
    8: "room_air_sensor",
    9: "humidity_sensor",
    10: "outdoor_temperature",
    11: "supply_temperature",
    12: "overtemperature",
    13: "communication_error",
    14: "fire",
    15: "high_water_level",
    16: "fire_protection",
}

# Operation modes a controller can choose (name -> command, and fan level for manual).
OPERATIONS = ("standby", "manual", "automatic", "week_program", "away", "summer", "fireplace")


# --- encoding -----------------------------------------------------------------
def u32_to_regs(value: int) -> list[int]:
    value = int(value) & 0xFFFFFFFF
    return [value & 0xFFFF, value >> 16]


def regs_to_u32(regs: list[int]) -> int:
    return (regs[0] & 0xFFFF) | ((regs[1] & 0xFFFF) << 16)


def f32_to_regs(value: float) -> list[int]:
    high, low = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [low, high]


def regs_to_f32(regs: list[int]) -> float:
    return struct.unpack(">f", struct.pack(">HH", regs[1] & 0xFFFF, regs[0] & 0xFFFF))[0]


def crc16(frame: bytes) -> int:
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusError(OSError):
    """The unit did not answer, or answered with an exception."""


class Transport(Protocol):
    def read(self, address: int, count: int) -> list[int]: ...
    def write(self, address: int, values: list[int]) -> None: ...
    def close(self) -> None: ...


def _pdu_read(address: int, count: int) -> bytes:
    return struct.pack(">BHH", 3, address, count)


def _pdu_write(address: int, values: list[int]) -> bytes:
    return struct.pack(">BHHB", 16, address, len(values), 2 * len(values)) + b"".join(
        struct.pack(">H", v & 0xFFFF) for v in values
    )


def _parse_pdu(pdu: bytes, function: int, count: int = 0) -> list[int]:
    if not pdu:
        raise ModbusError("empty response")
    if pdu[0] == function | 0x80:
        raise ModbusError(f"Modbus exception {pdu[1] if len(pdu) > 1 else '?'}")
    if pdu[0] != function:
        raise ModbusError(f"unexpected function {pdu[0]}")
    if function == 3:
        size = pdu[1]
        if size != 2 * count or len(pdu) < 2 + size:
            raise ModbusError("short read response")
        return list(struct.unpack(f">{count}H", pdu[2 : 2 + size]))
    return []


class ModbusTcp:
    """Minimal Modbus TCP client (FC03/FC16) – blocking, thread-safe."""

    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 5.0) -> None:
        self.host, self.port, self.unit_id, self.timeout = host, int(port), int(unit_id), timeout
        self._sock: socket.socket | None = None
        self._tid = 0
        self._lock = threading.Lock()

    def _connect(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
        return self._sock

    def _recv(self, sock: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ModbusError("connection closed")
            data += chunk
        return data

    def _transact(self, pdu: bytes) -> bytes:
        with self._lock:
            try:
                sock = self._connect()
                self._tid = (self._tid + 1) & 0xFFFF
                sock.sendall(struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit_id) + pdu)
                header = self._recv(sock, 7)
                tid, _proto, length, _unit = struct.unpack(">HHHB", header)
                body = self._recv(sock, length - 1)
                if tid != self._tid:
                    raise ModbusError("transaction id mismatch")
                return body
            except (OSError, struct.error) as err:
                self.close()
                raise ModbusError(str(err)) from err

    def read(self, address: int, count: int) -> list[int]:
        return _parse_pdu(self._transact(_pdu_read(address, count)), 3, count)

    def write(self, address: int, values: list[int]) -> None:
        _parse_pdu(self._transact(_pdu_write(address, values)), 16)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


class ModbusRtu:
    """Modbus RTU master over an open serial port (19200 8E1 on the HCH5 bus)."""

    def __init__(self, serial_port: Any, unit_id: int = 1, timeout: float = 1.0) -> None:
        self.port, self.unit_id, self.timeout = serial_port, int(unit_id), timeout
        self._lock = threading.Lock()

    def _transact(self, pdu: bytes, expected: int) -> bytes:
        frame = bytes([self.unit_id]) + pdu
        frame += struct.pack("<H", crc16(frame))
        with self._lock:
            self.port.reset_input_buffer()
            self.port.write(frame)
            deadline = time.monotonic() + self.timeout
            data = b""
            while time.monotonic() < deadline:
                data += self.port.read(max(1, expected - len(data)))
                if len(data) >= 5 and data[1] & 0x80:
                    expected = 5
                if len(data) >= expected:
                    break
        if len(data) < 5 or data[0] != self.unit_id:
            raise ModbusError("no answer from the unit")
        data = data[:expected]
        if struct.unpack("<H", data[-2:])[0] != crc16(data[:-2]):
            raise ModbusError("CRC error")
        return data[1:-2]

    def read(self, address: int, count: int) -> list[int]:
        return _parse_pdu(self._transact(_pdu_read(address, count), 5 + 2 * count), 3, count)

    def write(self, address: int, values: list[int]) -> None:
        _parse_pdu(self._transact(_pdu_write(address, values), 8), 16)

    def close(self) -> None:
        return None


# --- the unit -----------------------------------------------------------------
@dataclass
class Mk2Info:
    device_type: int
    model: str
    firmware: str
    serial_number: int
    components: int

    @property
    def has_bypass(self) -> bool:
        return bool(self.components & COMPONENT_BYPASS)

    @property
    def has_humidity(self) -> bool:
        return bool(self.components & COMPONENT_RH)

    @property
    def has_air_quality(self) -> bool:
        return bool(self.components & COMPONENT_VOC)


class Mk2Unit:
    """Read a snapshot and send commands to an HCH5 MKII."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.info: Mk2Info | None = None

    # reading
    def _u32(self, address: int) -> int:
        return regs_to_u32(self.transport.read(address, 2))

    def _f32(self, address: int) -> float:
        return round(regs_to_f32(self.transport.read(address, 2)), 2)

    def identify(self) -> Mk2Info:
        system_id = self._u32(REG_SYSTEM_ID)
        firmware = self._u32(REG_FIRMWARE_VERSION)
        serial_regs = self.transport.read(REG_SERIAL_NUMBER, 4)
        serial = regs_to_u32(serial_regs[:2]) | (regs_to_u32(serial_regs[2:]) << 32)
        try:
            components = self._u32(REG_SYSTEM_COMPONENTS)
        except ModbusError:
            components = 0
        device_type = system_id >> 24
        model = "HCH 5 MKII" if device_type == DEVICE_TYPE_HCH5_MK2 else f"Dantherm type {device_type}"
        self.info = Mk2Info(device_type, model, f"{(firmware >> 8) & 0xFF}.{firmware & 0xFF:02d}", serial, components)
        return self.info

    def snapshot(self) -> dict[str, object]:
        """Everything the integration and the gateway show, in their key names."""
        info = self.info or self.identify()
        mode = self._u32(REG_CURRENT_MODE)
        level = self._u32(REG_FAN_LEVEL)
        bypass = self._u32(REG_BYPASS_DAMPER) if info.has_bypass or info.components == 0 else BYPASS_CLOSED
        alarm = self._u32(REG_ALARM)
        remain = self._u32(REG_FILTER_REMAIN)
        lifetime = self._u32(REG_FILTER_LIFETIME)
        data: dict[str, object] = {
            "unit_model": info.model,
            "unit_generation": "mk2",
            "unit_firmware": info.firmware,
            "outdoor_temperature": self._f32(REG_OUTDOOR_TEMP),
            "supply_temperature": self._f32(REG_SUPPLY_TEMP),
            "extract_temperature": self._f32(REG_EXTRACT_TEMP),
            "exhaust_temperature": self._f32(REG_EXHAUST_TEMP),
            "supply_fan_rpm": round(self._f32(REG_FAN1_SPEED)),
            "extract_fan_rpm": round(self._f32(REG_FAN2_SPEED)),
            "mk2_level": level,
            "mk2_mode": MODE_NAMES.get(mode, f"mode_{mode}"),
            # The same words the MK1 decoder uses, so the existing sensors and cards fit.
            "current_level": "off" if level == 0 else "boost" if level >= 4 else f"level_{level}",
            "operating_mode": _mk1_operating_mode(mode, level),
            "standby": mode == MODE_STANDBY or level == 0,
            "fireplace": mode == MODE_FIREPLACE,
            "night_mode": mode == MODE_NIGHT,
            "away": mode == MODE_AWAY,
            "summer": mode == MODE_SUMMER,
            "bypass_raw": bypass,
            "bypass_active": bypass in (BYPASS_OPENED, BYPASS_OPENING),
            "bypass_state": BYPASS_STATES.get(bypass, f"raw_{bypass}"),
            "status_code": alarm,
            "alarm": ALARMS.get(alarm, f"alarm_{alarm}"),
            "filter_alarm": remain == 0,
            "filter_days_remaining": remain,
            "filter_interval_days": lifetime,
            "filter_life_percent": round(100 * remain / lifetime) if lifetime else None,
            "filter_source": "unit",
            "operating_hours": self._u32(REG_WORK_TIME),
            "hac1_connected": True,  # the MKII has its own controller; "connected" = answering
        }
        try:
            data["room_temperature"] = self._f32(REG_ROOM_TEMP)
        except ModbusError:
            pass
        if info.has_bypass or info.components == 0:
            # Whether manual bypass is selected changes at once; the damper takes minutes to follow.
            try:
                data["manual_bypass"] = bool(self._u32(REG_ACTIVE_MODE) & ACTIVE_MANUAL_BYPASS)
            except ModbusError:
                pass
        if info.has_humidity or info.components == 0:
            data["relative_humidity"] = self._u32(REG_HUMIDITY)
        if info.has_air_quality or info.components == 0:
            quality = self._u32(REG_AIR_QUALITY)
            data["air_quality_raw"] = quality
            try:
                middle, high = self._u32(REG_AIR_QUALITY_MIDDLE), self._u32(REG_AIR_QUALITY_HIGH)
            except ModbusError:
                middle, high = 1000, 1400
            data["air_quality_index"] = "good" if quality < middle else "moderate" if quality < high else "poor"
        return data

    # commands
    def command(self, value: int) -> None:
        self.transport.write(REG_ACTIVE_MODE, u32_to_regs(value))

    def set_level(self, level: int) -> None:
        """0 = standby, 1..4 = fan step; switches the unit to manual first."""
        level = max(0, min(4, int(level)))
        if self._u32(REG_CURRENT_MODE) not in (MODE_MANUAL, MODE_STANDBY):
            self.command(CMD_MANUAL)
        self.transport.write(REG_FAN_LEVEL, u32_to_regs(level))

    def set_operation(self, name: str) -> None:
        current = self._u32(REG_CURRENT_MODE)
        ends = {MODE_AWAY: CMD_END_AWAY, MODE_FIREPLACE: CMD_END_FIREPLACE, MODE_SUMMER: CMD_END_SUMMER}
        target = {
            "manual": CMD_MANUAL, "standby": CMD_MANUAL, "automatic": CMD_AUTOMATIC,
            "week_program": CMD_WEEK_PROGRAM, "away": CMD_START_AWAY, "summer": CMD_START_SUMMER,
            "fireplace": CMD_START_FIREPLACE,
        }
        if name not in target:
            raise ValueError(f"unknown operation {name}")
        if current in ends and MODE_NAMES.get(current) != name:
            self.command(ends[current])
        self.command(target[name])
        if name == "standby":
            self.transport.write(REG_FAN_LEVEL, u32_to_regs(0))

    def set_fireplace(self, on: bool) -> None:
        self.command(CMD_START_FIREPLACE if on else CMD_END_FIREPLACE)

    def set_away(self, on: bool) -> None:
        self.command(CMD_START_AWAY if on else CMD_END_AWAY)

    def set_night(self, on: bool) -> None:
        self.command(CMD_NIGHT_ON if on else CMD_NIGHT_OFF)

    def set_bypass(self, open_: bool) -> None:
        """Select or deselect manual bypass; only send when the unit's choice differs.

        Decided by the manual-bypass bit of the active mode (as Tvalley71/dantherm
        does), not by the damper, which needs a minute or two to travel. Units that
        do not answer that register fall back to the damper position.
        """
        try:
            selected = bool(self._u32(REG_ACTIVE_MODE) & ACTIVE_MANUAL_BYPASS)
        except ModbusError:
            state = self._u32(REG_BYPASS_DAMPER)
            selected = state in (BYPASS_OPENED, BYPASS_OPENING, BYPASS_IN_PROGRESS)
        if open_ != selected:
            self.command(CMD_BYPASS_OPEN if open_ else CMD_BYPASS_CLOSE)

    def reset_filter(self) -> None:
        self.transport.write(REG_FILTER_RESET, u32_to_regs(1))

    def reset_alarm(self) -> None:
        alarm = self._u32(REG_ALARM)
        self.transport.write(REG_ALARM_RESET, u32_to_regs(alarm or 1))

    def set_week_program(self, program: int) -> None:
        self.transport.write(REG_WEEK_PROGRAM, u32_to_regs(int(program)))

    def set_humidity_setpoint(self, percent: int) -> None:
        self.transport.write(REG_HUMIDITY_SETPOINT, u32_to_regs(int(percent)))


def _mk1_operating_mode(mode: int, level: int) -> str:
    """The MK1 decoder's operating-mode words for an MKII mode and fan level."""
    if mode == MODE_FIREPLACE:
        return "fireplace"
    if mode == MODE_STANDBY or level == 0:
        return "standby"
    if mode == MODE_MANUAL:
        return "auto_or_boost" if level >= 4 else f"manual_{level}"
    return "auto_or_scheduled"


def level_from_percent(percent: float, steps: list[float]) -> int:
    """Map a fan percentage to the nearest MKII level 1..4 (0 when off).

    ``steps`` are the percentages the controller uses for its own levels, so
    the result follows the controller's level, not the raw percentage.
    """
    if percent <= 0:
        return 0
    if not steps:
        return max(1, min(4, round(percent / 25)))
    nearest = min(range(len(steps)), key=lambda i: abs(steps[i] - percent))
    # Spread the controller's levels evenly over 1..4.
    return max(1, min(4, 1 + round(nearest * 3 / max(1, len(steps) - 1))))
