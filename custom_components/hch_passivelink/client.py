"""Receive-only TCP client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import serial

from .parser import DanthermDecoder, RtuStreamParser

_LOGGER = logging.getLogger(__name__)


class PassiveLinkClient:
    """Continuously receive a raw RTU-over-TCP stream without ever writing."""

    def __init__(self, host: str, port: int, update: Callable[[dict[str, object]], None]) -> None:
        self.host, self.port, self._update = host, port, update
        self.decoder = DanthermDecoder(update)
        self.parser = RtuStreamParser(self.decoder.decode)
        self._stopped = False
        self.connected = False

    def set_update_callback(
        self, update: Callable[[dict[str, object]], None]
    ) -> None:
        """Attach the Home Assistant coordinator after client construction."""
        self._update = update
        self.decoder._on_update = update

    async def probe(self, timeout: float = 8) -> None:
        """Verify that the endpoint yields at least one valid decoded update."""
        event = asyncio.Event()
        decoder = DanthermDecoder(lambda _: event.set())
        parser = RtuStreamParser(decoder.decode)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=timeout
        )
        try:
            async with asyncio.timeout(timeout):
                while not event.is_set():
                    chunk = await reader.read(4096)
                    if not chunk:
                        raise ConnectionError("TCP stream closed")
                    parser.feed(chunk)
        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self) -> None:
        delay = 1
        while not self._stopped:
            writer = None
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                self.connected, delay = True, 1
                self._update(dict(self.decoder.data))
                while not self._stopped:
                    try:
                        chunk = await asyncio.wait_for(reader.read(4096), timeout=15)
                    except asyncio.TimeoutError as err:
                        self.decoder._frame_times.clear()
                        self.decoder._set(bus_traffic=False, bus_frame_rate=0)
                        raise ConnectionError("No RS485 traffic for 15 seconds") from err
                    if not chunk:
                        raise ConnectionError("TCP stream closed")
                    self.parser.feed(chunk)
            except (OSError, ConnectionError) as err:
                self.connected = False
                self._update(dict(self.decoder.data))
                _LOGGER.debug("PassiveLink reconnect: %s", err)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            finally:
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()

    async def stop(self) -> None:
        self._stopped = True


class PassiveSerialClient:
    """Receive-only USB-RS485 client using 19200 8E1."""

    def __init__(self, port: str, update: Callable[[dict[str, object]], None]) -> None:
        self.port = port
        self._update = update
        self.decoder = DanthermDecoder(self._threadsafe_update)
        self.parser = RtuStreamParser(self.decoder.decode)
        self._stopped = False
        self.connected = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_update_callback(
        self, update: Callable[[dict[str, object]], None]
    ) -> None:
        """Attach the Home Assistant coordinator after client construction."""
        self._update = update

    def _threadsafe_update(self, data: dict[str, object]) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._update, data)

    @staticmethod
    def _open(port: str, timeout: float = 1) -> serial.Serial:
        connection = serial.Serial(
            port=port,
            baudrate=19200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            exclusive=True,
        )
        try:
            connection.rts = False
            connection.dtr = False
        except (AttributeError, OSError):
            pass
        return connection

    def _probe_blocking(self, timeout: float) -> None:
        event = False

        def decoded(_frame: bytes) -> None:
            nonlocal event
            event = True

        parser = RtuStreamParser(decoded)
        deadline = __import__("time").monotonic() + timeout
        with self._open(self.port, min(timeout, 1)) as connection:
            while not event and __import__("time").monotonic() < deadline:
                parser.feed(connection.read(4096))
        if not event:
            raise ConnectionError("No valid Modbus RTU traffic received")

    async def probe(self, timeout: float = 8) -> None:
        await asyncio.to_thread(self._probe_blocking, timeout)

    def _read_blocking(self) -> None:
        with self._open(self.port) as connection:
            self.connected = True
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._update, dict(self.decoder.data))
            while not self._stopped:
                chunk = connection.read(4096)
                if chunk:
                    self.parser.feed(chunk)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        delay = 1
        while not self._stopped:
            try:
                await asyncio.to_thread(self._read_blocking)
            except (OSError, serial.SerialException) as err:
                self.connected = False
                self._update(dict(self.decoder.data))
                _LOGGER.debug("PassiveLink serial reconnect: %s", err)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            else:
                delay = 1

    async def stop(self) -> None:
        self._stopped = True


class Mk2Client:
    """Poll an HCH5 MKII over Modbus TCP and send it commands (direct, no Pi needed)."""

    POLL_SECONDS = 10

    def __init__(self, host: str, port: int, unit_id: int, update: Callable[[dict[str, object]], None]) -> None:
        from . import mk2

        self._mk2 = mk2
        self.unit = mk2.Mk2Unit(mk2.ModbusTcp(host, port, unit_id))
        self._update = update
        self._stopped = False
        self._wake = asyncio.Event()
        self.connected = False
        self.data: dict[str, object] = {}

    def set_update_callback(self, update: Callable[[dict[str, object]], None]) -> None:
        self._update = update

    async def probe(self, timeout: float = 8) -> None:
        """Identify the unit; raises when nothing answers."""
        info = await asyncio.wait_for(asyncio.to_thread(self.unit.identify), timeout)
        if info.device_type <= 0:
            raise ConnectionError("Not a Dantherm unit")

    async def run(self) -> None:
        delay = self.POLL_SECONDS
        while not self._stopped:
            # Clear before reading: a command sent while this poll runs must trigger
            # another read, or its result shows up only a full poll interval later.
            self._wake.clear()
            try:
                data = await asyncio.to_thread(self.unit.snapshot)
                self.connected, delay = True, self.POLL_SECONDS
                self.data = {**data, "bus_traffic": True}
            except (OSError, ValueError) as err:
                self.connected = False
                self.data = {**self.data, "bus_traffic": False}
                _LOGGER.debug("MKII poll failed: %s", err)
                delay = min(delay * 2, 60)
            self._update(dict(self.data))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def async_command(self, method: str, *args) -> None:
        """Run one Mk2Unit command, then read the unit again at once."""
        await asyncio.to_thread(getattr(self.unit, method), *args)
        self._wake.set()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        await asyncio.to_thread(self.unit.transport.close)
