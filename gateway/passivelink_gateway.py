#!/usr/bin/env python3
"""Read-only RS485 to raw TCP mirror for Dantherm Control."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path

import serial
from temperature_snapshot import read_temperature_snapshot

LOGGER = logging.getLogger("passivelink-gateway")


class Gateway:
    """Mirror serial bytes, optionally adding fixed read-only temperature polls."""

    def __init__(self, device: str, bind: str, port: int, *, temperature_snapshots: bool = False) -> None:
        self.device = device
        self.bind = bind
        self.port = port
        self.clients: set[asyncio.StreamWriter] = set()
        self.stopping = False
        self.temperature_snapshots = temperature_snapshots

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        self.clients.add(writer)
        LOGGER.info("TCP client connected: %s", peer)
        try:
            # PassiveLink never sends. Disconnect clients that try to turn the
            # gateway into a transmitter.
            data = await reader.read(1)
            if data:
                LOGGER.warning("Disconnected client that attempted to send: %s", peer)
        finally:
            self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def broadcast(self, data: bytes) -> None:
        failed: list[asyncio.StreamWriter] = []
        for writer in tuple(self.clients):
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionError, OSError):
                failed.append(writer)
        for writer in failed:
            self.clients.discard(writer)
            writer.close()

    async def serial_loop(self) -> None:
        while not self.stopping:
            connection: serial.Serial | None = None
            try:
                if not Path(self.device).exists():
                    raise FileNotFoundError(self.device)
                connection = serial.Serial(
                    port=self.device,
                    baudrate=19200,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_EVEN,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.05 if self.temperature_snapshots else 1,
                    exclusive=True,
                )
                connection.rts = False
                connection.dtr = False
                LOGGER.info("Listening read-only on %s at 19200 8E1", self.device)
                last_snapshot = float("-inf")
                while not self.stopping:
                    data = await asyncio.to_thread(connection.read, 4096)
                    if data:
                        await self.broadcast(data)
                    if self.temperature_snapshots and time.monotonic() - last_snapshot >= 10:
                        snapshot = await asyncio.to_thread(read_temperature_snapshot, connection)
                        last_snapshot = time.monotonic()
                        if snapshot is not None:
                            await self.broadcast(snapshot)
                        else:
                            LOGGER.debug("Temperature snapshot skipped: busy bus or no valid reply")
            except (FileNotFoundError, OSError, serial.SerialException) as error:
                LOGGER.warning("RS485 unavailable (%s); retrying in 3 seconds", error)
                await asyncio.sleep(3)
            finally:
                if connection is not None and connection.is_open:
                    connection.close()

    async def run(self) -> None:
        server = await asyncio.start_server(self.handle_client, self.bind, self.port)
        LOGGER.info("Raw read-only TCP stream listening on %s:%d", self.bind, self.port)
        async with server:
            await asyncio.gather(server.serve_forever(), self.serial_loop())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4196)
    parser.add_argument(
        "--temperature-snapshots", action="store_true",
        default=os.environ.get("TEMPERATURE_SNAPSHOTS", "0") == "1",
        help="Opt in to a fixed HAC1 FC03 read every 10 seconds (not strictly passive)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    gateway = Gateway(args.device, args.bind, args.port, temperature_snapshots=args.temperature_snapshots)
    asyncio.run(gateway.run())


if __name__ == "__main__":
    main()
