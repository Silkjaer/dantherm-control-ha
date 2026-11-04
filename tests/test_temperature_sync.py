"""Standalone regression tests; no Home Assistant runtime required."""
import ast
import asyncio
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))
sys.path.insert(0, str(Path(__file__).parents[1] / "gateway"))

from parser import DanthermDecoder, RtuStreamParser, TEMPERATURE_KEYS, crc16
from alarms import derive_alarm_values
from const import PI_DIAGNOSTIC_KEYS


def frame(body):
    return body + crc16(body).to_bytes(2, "little")


def snapshot(values=None):
    values = values or [1672, 2115, 2173, 1719, 2250] + [0] * 20 + [2126, 1717, 32768, 32768, 0]
    return frame(bytes.fromhex("400300b4001e")) + frame(
        bytes([64, 3, 60]) + b"".join(v.to_bytes(2, "big") for v in values)
    )


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.updates = []
        self.decoder = DanthermDecoder(self.updates.append)
        self.stream = RtuStreamParser(self.decoder.decode)

    def test_one_atomic_sample_even_when_fragmented(self):
        for byte in snapshot():
            self.stream.feed(bytes([byte]))
        samples = [u for u in self.updates if "temperature_sample_monotonic" in u]
        self.assertEqual(len(samples), 1)
        self.assertEqual([samples[0][k] for k in TEMPERATURE_KEYS],
                         [16.72, 21.15, 21.73, 17.19, 22.5, 21.26, 17.17])

    def test_legacy_cannot_overwrite_snapshot(self):
        self.stream.feed(snapshot())
        self.stream.feed(frame(bytes.fromhex("0104080576094c083705a2")))
        self.stream.feed(frame(bytes.fromhex("4003020bb8")))
        self.stream.feed(frame(bytes.fromhex("40030a09c40960800080000000")))
        self.assertEqual(self.decoder.data["supply_temperature"], 21.15)
        self.assertEqual(self.decoder.data["room_temperature"], 22.5)
        self.assertEqual(self.decoder.data["heating_coil_after_temperature"], 21.26)

    def test_no_request_no_snapshot(self):
        self.stream.feed(snapshot()[8:])
        self.assertNotIn("temperature_sample_monotonic", self.decoder.data)

    def test_fault_replaces_old_and_negative_is_signed(self):
        self.stream.feed(snapshot())
        values = [0xFE0C, 2115, 2173, 1719, 2250] + [0] * 20 + [0x7FFF, 0x8000, 32768, 32768, 0]
        self.stream.feed(snapshot(values))
        self.assertEqual(self.decoder.data["outdoor_temperature"], -5.0)
        self.assertIsNone(self.decoder.data["heating_coil_after_temperature"])
        self.assertIsNone(self.decoder.data["heating_coil_before_temperature"])

    def test_expired_sample_clears_all_channels(self):
        with patch("parser.time.monotonic", return_value=100):
            self.stream.feed(snapshot())
        with patch("parser.time.monotonic", return_value=131):
            self.stream.feed(frame(bytes.fromhex("010600420019")))
        self.assertTrue(all(self.decoder.data[k] is None for k in TEMPERATURE_KEYS))

    def test_legacy_main_frame_is_atomic(self):
        self.stream.feed(frame(bytes.fromhex("0104080576094c083705a2")))
        samples = [u for u in self.updates if "temperature_sample_monotonic" in u]
        self.assertEqual(len(samples), 1)
        self.assertTrue(all(k in samples[0] for k in TEMPERATURE_KEYS[:4]))

    def test_bad_crc_snapshot_does_not_publish(self):
        data = bytearray(snapshot())
        data[-1] ^= 1
        self.stream.feed(data)
        self.assertNotIn("temperature_sample_monotonic", self.decoder.data)

    def test_register_209_is_binary_afterheat_status(self):
        values = [1672, 2115, 2173, 1719, 2250] + [0] * 20 + [2126, 1717, 32768, 32768, 16]
        self.stream.feed(snapshot(values))
        self.assertTrue(self.decoder.data["afterheat_active"])
        values[29] = 0
        self.stream.feed(snapshot(values))
        self.assertFalse(self.decoder.data["afterheat_active"])


def coordinator_class():
    # Execute the actual class with small HA boundary stubs, not a copied method.
    tree = ast.parse((Path(__file__).parents[1] / "custom_components" / "hch_passivelink" / "coordinator.py").read_text())
    nodes = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    class Base:
        @classmethod
        def __class_getitem__(cls, _):
            return cls
        def async_set_updated_data(self, data):
            self.data = data
            self.updates.append(dict(data))
    import logging, time, datetime
    ns = dict(asyncio=asyncio, logging=logging, time=time, datetime=datetime.datetime,
              timedelta=datetime.timedelta, timezone=datetime.timezone,
              TEMPERATURE_KEYS=TEMPERATURE_KEYS, DataUpdateCoordinator=Base,
              HomeAssistant=object, derive_alarm_values=derive_alarm_values,
              PI_DIAGNOSTIC_KEYS=PI_DIAGNOSTIC_KEYS)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "coordinator.py", "exec"), ns)
    return ns["PassiveLinkCoordinator"]


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_water_and_air_commit_together_without_status_rollback(self):
        cls = coordinator_class()
        c = object.__new__(cls)
        c.data = dict(zip(TEMPERATURE_KEYS, [10.] * 7))
        c.updates = []
        c.hass = SimpleNamespace(async_create_task=asyncio.create_task)
        c._temperature_task = None
        c._temperature_sample = None
        c._night_mode = c._filter_reset_epoch = c._filter_interval_days = None
        c._efficiency_reference = None
        c._filter_reset_history = []
        c._schedule_notification_check = lambda: None
        c._update_health_issues = lambda: None
        gate = asyncio.Event()
        async def fetch():
            await gate.wait()
            return dict(preheater_flow_temperature=27., preheater_return_temperature=25.,
                        preheater_sensor_connected=True)
        c._auxiliary_client = SimpleNamespace(async_fetch=fetch)
        data = dict(zip(TEMPERATURE_KEYS, [20.] * 7), temperature_sample_monotonic=1.)
        c.async_handle_update(data)
        await asyncio.sleep(0)
        c.async_handle_update(dict(data, co2=900))
        self.assertTrue(all(u["supply_temperature"] == 10. for u in c.updates))
        # A faster second air sample must neither cancel the water request nor
        # leak partial temperatures; commit the newest complete sample instead.
        pending_task = c._temperature_task
        newer = dict(data, temperature_sample_monotonic=2., supply_temperature=21., co2=900)
        c.async_handle_update(newer)
        self.assertIs(c._temperature_task, pending_task)
        self.assertEqual(c.data["supply_temperature"], 10.)
        gate.set()
        await c._temperature_task
        self.assertEqual(c.data["supply_temperature"], 21.)
        self.assertEqual(c.data["preheater_water_delta"], 2.)
        self.assertEqual(c.data["co2"], 900)
        self.assertEqual(c.data["supply_extract_delta"], 1.)

    async def test_unit_filter_counter_takes_over_from_local_reset(self):
        cls = coordinator_class()
        c = object.__new__(cls)
        c.hass = SimpleNamespace(async_create_task=lambda coro: coro.close())
        c.unit_filter_counter = False
        c._filter_reset_epoch = c._filter_interval_days = None
        c._filter_notification_reset_epoch = None
        c._filter_reset_history = []
        c._sync_unit_filter({"filter_unit_hours": None, "filter_unit_period_months": 12})
        self.assertFalse(c.unit_filter_counter)
        c._sync_unit_filter({"filter_unit_hours": 4398, "filter_unit_period_months": 12})
        self.assertTrue(c.unit_filter_counter)
        unit_reset = c._filter_reset_epoch
        # A local reset is undone by the next frame from the unit; only the
        # false history entry would remain, hence the disabled button.
        c._filter_reset_epoch = time.time()
        c._sync_unit_filter({"filter_unit_hours": 4398, "filter_unit_period_months": 12})
        self.assertAlmostEqual(c._filter_reset_epoch, unit_reset, delta=5)
        self.assertEqual(len(c._filter_reset_history), 1)

    async def test_failed_water_fetch_publishes_air_and_clears_old_water(self):
        cls = coordinator_class()
        c = object.__new__(cls)
        c.data = {"preheater_flow_temperature": 30., "preheater_return_temperature": 29.}
        c.updates = []
        c._temperature_sample = 2.
        c._efficiency_reference = None
        async def fetch():
            return None
        c._auxiliary_client = SimpleNamespace(async_fetch=fetch)
        sample = dict(zip(TEMPERATURE_KEYS, [20.] * 7), temperature_sample_monotonic=2.,
                      temperature_source="hac1_snapshot_180_209")
        await c._async_update_auxiliary(sample)
        self.assertEqual(c.data["supply_temperature"], 20.)
        self.assertIsNone(c.data["preheater_flow_temperature"])
        self.assertFalse(c.data["preheater_sensor_connected"])
        self.assertEqual(c.data["heating_coil_air_delta"], 0.)

    async def test_pi_diagnostics_publish_without_preheater_sensors(self):
        cls = coordinator_class()
        c = object.__new__(cls)
        c.data = {"preheater_flow_temperature": 30., "preheater_return_temperature": 29.}
        c.updates = []
        c._temperature_sample = 2.
        c._efficiency_reference = None
        async def fetch():
            return {"pi_cpu_temperature": 41.2, "pi_undervoltage_occurred": True}
        c._auxiliary_client = SimpleNamespace(async_fetch=fetch)
        sample = dict(zip(TEMPERATURE_KEYS, [20.] * 7), temperature_sample_monotonic=2.,
                      temperature_source="hac1_snapshot_180_209")
        await c._async_update_auxiliary(sample)
        self.assertEqual(c.data["pi_cpu_temperature"], 41.2)
        self.assertTrue(c.data["pi_undervoltage_occurred"])
        # A Pi without DS18B20 sensors still clears preheater state, same as
        # a failed water fetch - the two are independent, not all-or-nothing.
        self.assertIsNone(c.data["preheater_flow_temperature"])
        self.assertFalse(c.data["preheater_sensor_connected"])

    async def test_obsolete_sample_cannot_commit(self):
        cls = coordinator_class()
        c = object.__new__(cls)
        c.data = {"supply_temperature": 25.}
        c.updates = []
        c._temperature_sample = 2.
        async def fetch():
            return None
        c._auxiliary_client = SimpleNamespace(async_fetch=fetch)
        await c._async_update_auxiliary({"temperature_sample_monotonic":1.,"supply_temperature":20.})
        self.assertEqual(c.data, {"supply_temperature":25.})
        self.assertEqual(c.updates, [])


class GatewayTests(unittest.TestCase):
    def test_read_request_only_and_valid_response(self):
        from temperature_snapshot import REQUEST, read_temperature_snapshot
        writes = []
        response = bytearray(b"noise" + snapshot()[8:])
        class Serial:
            in_waiting = 0
            def write(self, data):
                writes.append(data)
            def flush(self):
                pass
            def read(self, count):
                data = bytes(response[:count]); del response[:count]; return data
        self.assertEqual(read_temperature_snapshot(Serial()), snapshot())
        self.assertEqual(writes, [REQUEST])
        self.assertEqual(REQUEST[1], 3)

    def test_bad_crc_is_not_forwarded(self):
        from temperature_snapshot import read_temperature_snapshot
        response = bytearray(snapshot()[8:]); response[-1] ^= 1
        class Serial:
            in_waiting = 0
            def write(self, _):
                pass
            def flush(self):
                pass
            def read(self, count):
                data = bytes(response[:count]); del response[:count]; return data
        self.assertIsNone(read_temperature_snapshot(Serial()))

    def test_busy_bus_is_not_polled(self):
        from temperature_snapshot import read_temperature_snapshot
        class Serial:
            in_waiting = 1
            def read(self, _):
                return b'x'
            def write(self, _):
                raise AssertionError('Busy bus must not be polled')
        self.assertIsNone(read_temperature_snapshot(Serial()))

    def test_passive_mode_is_default(self):
        from passivelink_gateway import Gateway
        self.assertFalse(Gateway('/dev/test', '127.0.0.1', 4196).temperature_snapshots)


if __name__ == "__main__":
    unittest.main()
