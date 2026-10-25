"""A simulated Dantherm HCH5 MKII: Modbus TCP server and RTU serial port with the unit's mode logic.

Used by the tests; not shipped. Behaviour follows the Dantherm Residential
register map: writes to the active-mode register change the current mode,
the active-mode register reads back as a bitmask (bit 0x0080 = manual bypass
selected), selecting manual bypass moves the damper (optionally over several
reads, like the real damper's travel), the fan level sets the step, filter and
alarm resets clear their counters.
"""

from __future__ import annotations

import socketserver
import struct
import threading

import mk2


class Mk2Simulator:
    def __init__(self, bypass_travel_reads: int = 0) -> None:
        self.regs: dict[int, int] = {}
        self.lock = threading.Lock()
        self.writes: list[tuple[int, list[int]]] = []
        self._previous_mode = mk2.MODE_AUTOMATIC
        # Damper reads spent OPENING/CLOSING before it arrives (0 = it moves at once).
        self.bypass_travel_reads = bypass_travel_reads
        self.failing_registers: set[int] = set()  # reads answer with a Modbus exception
        self._travel_left: int | None = None  # None: no simulated travel under way
        self._damper_next: int | None = None  # where the damper goes after the current travel
        self.set_u32(mk2.REG_SYSTEM_ID, mk2.DEVICE_TYPE_HCH5_MK2 << 24 | 0x0105)
        self.set_u32(mk2.REG_SERIAL_NUMBER, 123456)
        self.set_u32(mk2.REG_SERIAL_NUMBER + 2, 0)
        self.set_u32(mk2.REG_FIRMWARE_VERSION, (2 << 8) | 74)
        self.set_u32(mk2.REG_SYSTEM_COMPONENTS, mk2.COMPONENT_BYPASS | mk2.COMPONENT_RH | mk2.COMPONENT_VOC)
        for reg, value in (
            (mk2.REG_OUTDOOR_TEMP, 6.5), (mk2.REG_SUPPLY_TEMP, 19.8), (mk2.REG_EXTRACT_TEMP, 22.1),
            (mk2.REG_EXHAUST_TEMP, 8.9), (mk2.REG_ROOM_TEMP, 21.6), (mk2.REG_FAN1_SPEED, 1420.0),
            (mk2.REG_FAN2_SPEED, 1515.0),
        ):
            self.set_f32(reg, value)
        self.set_u32(mk2.REG_CURRENT_MODE, mk2.MODE_AUTOMATIC)
        self.set_u32(mk2.REG_ACTIVE_MODE, mk2.CMD_AUTOMATIC)
        self.set_u32(mk2.REG_FAN_LEVEL, 2)
        self.set_u32(mk2.REG_BYPASS_DAMPER, mk2.BYPASS_CLOSED)
        self.set_u32(mk2.REG_HUMIDITY, 46)
        self.set_u32(mk2.REG_AIR_QUALITY, 650)
        self.set_u32(mk2.REG_AIR_QUALITY_LOW, 600)
        self.set_u32(mk2.REG_AIR_QUALITY_MIDDLE, 1000)
        self.set_u32(mk2.REG_AIR_QUALITY_HIGH, 1400)
        self.set_u32(mk2.REG_ALARM, 0)
        self.set_u32(mk2.REG_FILTER_REMAIN, 45)
        self.set_u32(mk2.REG_FILTER_LIFETIME, 180)
        self.set_u32(mk2.REG_WORK_TIME, 8123)

    # registers
    def set_u32(self, reg: int, value: int) -> None:
        lo, hi = mk2.u32_to_regs(value)
        self.regs[reg], self.regs[reg + 1] = lo, hi

    def set_f32(self, reg: int, value: float) -> None:
        lo, hi = mk2.f32_to_regs(value)
        self.regs[reg], self.regs[reg + 1] = lo, hi

    def u32(self, reg: int) -> int:
        return mk2.regs_to_u32([self.regs.get(reg, 0), self.regs.get(reg + 1, 0)])

    def read(self, address: int, count: int) -> list[int] | None:
        with self.lock:
            if address not in self.regs or address in self.failing_registers:
                return None  # illegal address
            if address == mk2.REG_BYPASS_DAMPER:
                self._advance_damper()
            return [self.regs.get(address + i, 0) for i in range(count)]

    def write(self, address: int, values: list[int]) -> None:
        with self.lock:
            self.writes.append((address, list(values)))
            if address == mk2.REG_ACTIVE_MODE and len(values) == 2:
                self._command(mk2.regs_to_u32(values))  # reads back as the active-mode bits
                return
            for i, value in enumerate(values):
                self.regs[address + i] = value
            if address == mk2.REG_FAN_LEVEL:
                level = self.u32(mk2.REG_FAN_LEVEL)
                self.set_u32(mk2.REG_CURRENT_MODE, mk2.MODE_STANDBY if level == 0 else mk2.MODE_MANUAL)
            elif address == mk2.REG_FILTER_RESET:
                self.set_u32(mk2.REG_FILTER_REMAIN, self.u32(mk2.REG_FILTER_LIFETIME))
            elif address == mk2.REG_ALARM_RESET:
                self.set_u32(mk2.REG_ALARM, 0)

    def _command(self, cmd: int) -> None:
        current = self.u32(mk2.REG_CURRENT_MODE)
        active = self.u32(mk2.REG_ACTIVE_MODE)
        if cmd & 0x8000:
            active &= ~(cmd & 0x7FFF)
        elif cmd in (mk2.CMD_AUTOMATIC, mk2.CMD_MANUAL, mk2.CMD_WEEK_PROGRAM):
            active = active & ~0x000E | cmd
        else:
            active |= cmd
        self.set_u32(mk2.REG_ACTIVE_MODE, active)
        starts = {
            mk2.CMD_MANUAL: mk2.MODE_MANUAL, mk2.CMD_AUTOMATIC: mk2.MODE_AUTOMATIC,
            mk2.CMD_WEEK_PROGRAM: mk2.MODE_WEEK_PROGRAM, mk2.CMD_START_AWAY: mk2.MODE_AWAY,
            mk2.CMD_START_SUMMER: mk2.MODE_SUMMER, mk2.CMD_START_FIREPLACE: mk2.MODE_FIREPLACE,
            mk2.CMD_NIGHT_ON: mk2.MODE_NIGHT,
        }
        ends = {mk2.CMD_END_AWAY, mk2.CMD_END_SUMMER, mk2.CMD_END_FIREPLACE, mk2.CMD_NIGHT_OFF}
        if cmd in starts:
            if current in (mk2.MODE_MANUAL, mk2.MODE_AUTOMATIC, mk2.MODE_WEEK_PROGRAM, mk2.MODE_STANDBY):
                self._previous_mode = current
            self.set_u32(mk2.REG_CURRENT_MODE, starts[cmd])
        elif cmd in ends:
            self.set_u32(mk2.REG_CURRENT_MODE, self._previous_mode)
        elif cmd == mk2.CMD_BYPASS_OPEN:
            self._move_damper(mk2.BYPASS_OPENED)
        elif cmd == mk2.CMD_BYPASS_CLOSE:
            self._move_damper(mk2.BYPASS_CLOSED)

    # bypass damper travel
    def _move_damper(self, target: int) -> None:
        state = self.u32(mk2.REG_BYPASS_DAMPER)
        if self.bypass_travel_reads <= 0:
            self._travel_left, self._damper_next = None, None
            self.set_u32(mk2.REG_BYPASS_DAMPER, target)
        elif self._travel_left is not None:
            heading = mk2.BYPASS_OPENED if state == mk2.BYPASS_OPENING else mk2.BYPASS_CLOSED
            # Like the real unit, a damper on its way finishes that travel first.
            self._damper_next = None if target == heading else target
        elif state != target:
            self._start_travel(target)

    def _start_travel(self, target: int) -> None:
        moving = mk2.BYPASS_OPENING if target == mk2.BYPASS_OPENED else mk2.BYPASS_CLOSING
        self.set_u32(mk2.REG_BYPASS_DAMPER, moving)
        self._travel_left = self.bypass_travel_reads

    def _advance_damper(self) -> None:
        """One damper read: count down the travel, then arrive (and start any queued travel)."""
        if self._travel_left is None:
            return
        if self._travel_left > 0:
            self._travel_left -= 1
            return
        state = self.u32(mk2.REG_BYPASS_DAMPER)
        self.set_u32(mk2.REG_BYPASS_DAMPER, mk2.BYPASS_OPENED if state == mk2.BYPASS_OPENING else mk2.BYPASS_CLOSED)
        self._travel_left = None
        if self._damper_next is not None:
            target, self._damper_next = self._damper_next, None
            if target != self.u32(mk2.REG_BYPASS_DAMPER):
                self._start_travel(target)

    # Modbus PDU handling
    def handle_pdu(self, pdu: bytes) -> bytes:
        function = pdu[0]
        if function == 3:
            address, count = struct.unpack(">HH", pdu[1:5])
            regs = self.read(address, count)
            if regs is None:
                return bytes([0x83, 2])
            return bytes([3, 2 * count]) + b"".join(struct.pack(">H", r) for r in regs)
        if function == 16:
            address, count, _size = struct.unpack(">HHB", pdu[1:6])
            values = list(struct.unpack(f">{count}H", pdu[6 : 6 + 2 * count]))
            self.write(address, values)
            return struct.pack(">BHH", 16, address, count)
        return bytes([function | 0x80, 1])

    # TCP server
    def serve_tcp(self) -> tuple[socketserver.ThreadingTCPServer, int]:
        sim = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                while True:
                    header = self.request.recv(7)
                    if len(header) < 7:
                        return
                    tid, proto, length, unit = struct.unpack(">HHHB", header)
                    pdu = self.request.recv(length - 1)
                    reply = sim.handle_pdu(pdu)
                    self.request.sendall(struct.pack(">HHHB", tid, proto, len(reply) + 1, unit) + reply)

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1]


class FakeSerial:
    """Serial port with the simulator as the RTU slave on the other end."""

    def __init__(self, sim: Mk2Simulator, unit_id: int = 1) -> None:
        self.sim, self.unit_id = sim, unit_id
        self._rx = b""

    def reset_input_buffer(self) -> None:
        self._rx = b""

    def write(self, frame: bytes) -> int:
        body, crc = frame[:-2], struct.unpack("<H", frame[-2:])[0]
        assert crc == mk2.crc16(body), "bad CRC from master"
        if body[0] != self.unit_id:
            return len(frame)
        reply = bytes([self.unit_id]) + self.sim.handle_pdu(body[1:])
        self._rx = reply + struct.pack("<H", mk2.crc16(reply))
        return len(frame)

    def read(self, size: int) -> bytes:
        data, self._rx = self._rx[:size], self._rx[size:]
        return data
