import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink.client import PassiveLinkClient  # noqa: E402
from hch_passivelink.parser import DanthermDecoder, crc16  # noqa: E402


def frame(body: bytes) -> bytes:
    return body + crc16(body).to_bytes(2, "little")


FAN_WRITE = frame(bytes.fromhex("010600420019"))


async def until(predicate, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def stop_client(client, task, server):
    await client.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    server.close()


def test_old_values_wait_for_a_valid_frame_after_a_reconnect():
    async def scenario():
        sessions = []

        async def serve(reader, writer):
            sessions.append(writer)
            if len(sessions) == 1:
                # One frame, then the stream drops in the middle of the next.
                writer.write(FAN_WRITE + FAN_WRITE[:4])
                await writer.drain()
                writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        updates = []
        client = PassiveLinkClient("127.0.0.1", port, lambda data: updates.append((client.connected, data)))
        task = asyncio.create_task(client.run())
        try:
            await until(lambda: len(sessions) == 2)
            await asyncio.sleep(0.1)
            # The socket is open again, but nothing valid arrived on it yet.
            assert client.connected is False
            assert updates[-1][0] is False
            assert updates[-1][1]["extract_fan_percent"] == 25
            # The rest of the cut-off frame cannot complete it.
            sessions[1].write(FAN_WRITE[4:])
            await sessions[1].drain()
            await asyncio.sleep(0.1)
            assert client.connected is False
            sessions[1].write(FAN_WRITE)
            await sessions[1].drain()
            await until(lambda: client.connected)
            assert updates[-1][0] is True
        finally:
            await stop_client(client, task, server)
            for writer in sessions:
                writer.close()

    asyncio.run(scenario())


def test_bytes_without_a_valid_frame_are_no_traffic():
    async def scenario():
        stop = asyncio.Event()

        async def serve(reader, writer):
            writer.write(FAN_WRITE)
            # Noise keeps the socket busy, but never makes a valid frame.
            with contextlib.suppress(ConnectionError):
                while not stop.is_set() and not writer.is_closing():
                    writer.write(b"\x55" * 64)
                    await writer.drain()
                    await asyncio.sleep(0.02)
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = PassiveLinkClient("127.0.0.1", port, lambda _: None)
        client.NO_TRAFFIC_SECONDS = 0.3
        task = asyncio.create_task(client.run())
        try:
            await until(lambda: client.connected)
            await until(lambda: not client.connected)
            assert client.decoder.data["bus_traffic"] is False
        finally:
            stop.set()
            await stop_client(client, task, server)

    asyncio.run(scenario())


def test_a_request_from_before_a_reconnect_is_not_paired_with_a_later_answer():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("400300b4001e")))
    decoder.reset_session()
    words = [1300, 2100, 2200, 1500, 2150] + [0] * 20 + [2100, 2000, 0x8000, 0x8000, 0]
    decoder.decode(frame(bytes([0x40, 3, 60]) + b"".join(w.to_bytes(2, "big") for w in words)))
    assert "temperature_sample_monotonic" not in decoder.data
