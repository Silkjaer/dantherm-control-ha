import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))

from parser import DanthermDecoder, RtuStreamParser, crc16


def frame(body: bytes) -> bytes:
    return body + crc16(body).to_bytes(2, "little")


def test_bus_frame_rate_counts_recent_frames():
    decoder = DanthermDecoder(lambda _: None)
    for _ in range(3):
        decoder.decode(frame(bytes.fromhex("010600420019")))
    assert decoder.data["bus_frame_rate"] == 3
    assert decoder.data["bus_traffic"] is True


def test_fragmented_temperature_response():
    updates = []
    decoder = DanthermDecoder(updates.append)
    parser = RtuStreamParser(decoder.decode)
    body = bytes.fromhex("0104080576094c083705a2")
    message = frame(body)
    parser.feed(message[:4])
    parser.feed(message[4:])
    assert decoder.data["outdoor_temperature"] == 13.98
    assert decoder.data["supply_temperature"] == 23.80
    assert decoder.data["extract_temperature"] == 21.03
    assert decoder.data["exhaust_temperature"] == 14.42


def test_humidity_uses_input_register_4_byte_scaling():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("0104080831084909240869")))
    decoder.decode(frame(bytes.fromhex("01040a008d0a24090c00400002")))
    assert decoder.data["measured_relative_humidity"] == 55.3
    assert decoder.data["relative_humidity"] == 49.4


def test_disconnected_humidity_sensor_is_unavailable():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("01040a00000a24090c00ff0002")))
    assert decoder.data.get("relative_humidity") is None
    assert decoder.data.get("measured_relative_humidity") is None


def test_fan_registers_and_mode():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("010600420019")))
    decoder.decode(frame(bytes.fromhex("01060043000d")))
    assert decoder.data["extract_fan_percent"] == 25
    assert decoder.data["supply_fan_percent"] == 13
    assert decoder.data["current_level"] == "level_1"


def test_repeated_manual_command_does_not_reclassify_unchanged_auto_pair():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("010600420055")))
    decoder.decode(frame(bytes.fromhex("010600430049")))
    decoder.decode(frame(bytes.fromhex("0106008f00bd")))
    decoder.decode(frame(bytes.fromhex("010600420055")))
    decoder.decode(frame(bytes.fromhex("010600430049")))
    assert decoder.data["operating_mode"] == "auto_or_scheduled"


def test_manual_command_requires_changed_fan_pair():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("010600420037")))
    decoder.decode(frame(bytes.fromhex("01060043002b")))
    decoder.decode(frame(bytes.fromhex("0106008f00bd")))
    decoder.decode(frame(bytes.fromhex("010600420055")))
    decoder.decode(frame(bytes.fromhex("010600430049")))
    assert decoder.data["operating_mode"] == "manual_3"


def test_auto_sequence_overrides_manual_level_fan_pair():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("010600420037")))
    decoder.decode(frame(bytes.fromhex("01060043002b")))
    decoder.decode(frame(bytes.fromhex("0106008f00c8")))
    decoder.decode(frame(bytes.fromhex("0106008f00ac")))
    assert decoder.data["operating_mode"] == "auto_or_scheduled"


def test_lone_172_followed_by_level_2_pair_selects_manual_2():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("01060042003c")))
    decoder.decode(frame(bytes.fromhex("010600430030")))
    decoder.decode(frame(bytes.fromhex("0106008f00ac")))
    decoder.decode(frame(bytes.fromhex("010600420037")))
    decoder.decode(frame(bytes.fromhex("01060043002b")))
    assert decoder.data["operating_mode"] == "manual_2"


def test_hac1_connected_bitfield():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("010600920003")))
    assert decoder.data["hac1_connected"] is True


def test_night_mode_waits_for_both_fan_registers():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("0106008f00ac")))
    decoder.decode(frame(bytes.fromhex("010600420019")))
    assert "night_mode" not in decoder.data
    decoder.decode(frame(bytes.fromhex("01060043000d")))
    assert decoder.data["night_mode"] is True


def test_night_mode_can_switch_off():
    decoder = DanthermDecoder(lambda _: None)
    decoder.decode(frame(bytes.fromhex("0106008f00ac")))
    decoder.decode(frame(bytes.fromhex("010600420019")))
    decoder.decode(frame(bytes.fromhex("01060043000d")))
    decoder._last_explicit_command = float("-inf")
    decoder.decode(frame(bytes.fromhex("0106008f00ac")))
    decoder.decode(frame(bytes.fromhex("010600420033")))
    decoder.decode(frame(bytes.fromhex("010600430027")))
    assert decoder.data["night_mode"] is False


def test_bad_crc_is_ignored():
    decoder = DanthermDecoder(lambda _: None)
    parser = RtuStreamParser(decoder.decode)
    parser.feed(bytes.fromhex("0106004200190000"))
    assert "extract_fan_percent" not in decoder.data


def test_write_multiple_request_is_kept_as_one_frame():
    frames = []
    parser = RtuStreamParser(frames.append)
    body = bytes.fromhex("401000b400050a00150016800080000000")
    message = frame(body)
    parser.feed(message[:8])
    parser.feed(message[8:])
    assert frames == [message]


def test_optional_afterheat_setpoints_decode_off():
    decoder = DanthermDecoder(lambda _: None)
    body = bytes.fromhex("401000b400050a00150016800080000000")
    decoder.decode(frame(body))
    assert decoder.data["afterheat_room_setpoint"] == "off"
    assert decoder.data["afterheat_extract_setpoint"] == "off"


def test_optional_afterheat_setpoints_decode_temperatures():
    decoder = DanthermDecoder(lambda _: None)
    body = bytes.fromhex("401000b400050a001500160014000f0000")
    decoder.decode(frame(body))
    assert decoder.data["afterheat_room_setpoint"] == "20"
    assert decoder.data["afterheat_extract_setpoint"] == "15"


def test_optional_afterheat_off_states_from_periodic_read():
    decoder = DanthermDecoder(lambda _: None)
    body = bytes.fromhex("40030a0aa20989800080000000")
    decoder.decode(frame(body))
    assert decoder.data["afterheat_room_setpoint"] == "off"
    assert decoder.data["afterheat_extract_setpoint"] == "off"


def test_hcp4_temperature_block_without_hac1_gives_room_temperature():
    # Captured from an HCH5 MK1 without HAC1: T1..T5 in hundredths, T5 from
    # the HRC2 remote (26.60 °C on its display).
    decoder = DanthermDecoder(lambda _: None)
    body = bytes.fromhex("401000b400050a050505690ab70a070a64")
    decoder.decode(frame(body))
    assert decoder.data["room_temperature"] == 26.6
    assert decoder.data["room_sensor_missing"] is False
    assert "afterheat_room_setpoint" not in decoder.data
    assert "afterheat_extract_setpoint" not in decoder.data


def test_hcp4_temperature_block_in_winter_still_gives_room_temperature():
    # Exhaust air (T4) can drop below 5 °C, extract air (T3) cannot.
    decoder = DanthermDecoder(lambda _: None)
    words = (0xFC18, 1650, 2150, 180, 2190)  # -10.00, 16.50, 21.50, 1.80, 21.90
    decoder.decode(frame(bytes.fromhex("401000b400050a") + b"".join(w.to_bytes(2, "big") for w in words)))
    assert decoder.data["room_temperature"] == 21.9


def test_hcp4_temperature_block_without_hrc2_is_not_a_room_temperature():
    from alarms import derive_alarm_values

    decoder = DanthermDecoder(lambda _: None)
    words = (1285, 1385, 2743, 2567, 0)
    decoder.decode(frame(bytes.fromhex("401000b400050a") + b"".join(w.to_bytes(2, "big") for w in words)))
    assert decoder.data.get("room_temperature") is None
    assert decoder.data["room_sensor_missing"] is True
    assert derive_alarm_values(decoder.data)["room_temperature_sensor_fault"] is False


def test_hcp4_room_temperature_expires_when_no_longer_written():
    from unittest.mock import patch

    decoder = DanthermDecoder(lambda _: None)
    block = frame(bytes.fromhex("401000b400050a050505690ab70a070a64"))
    fan_write = frame(bytes.fromhex("010600420019"))
    with patch("parser.time.monotonic", return_value=100):
        decoder.decode(block)
    # HCP4/HRC2 went quiet while the unit's own traffic goes on.
    with patch("parser.time.monotonic", return_value=399):
        decoder.decode(fan_write)
    assert decoder.data["room_temperature"] == 26.6
    with patch("parser.time.monotonic", return_value=401):
        decoder.decode(fan_write)
    assert decoder.data["room_temperature"] is None
    with patch("parser.time.monotonic", return_value=402):
        decoder.decode(block)
    assert decoder.data["room_temperature"] == 26.6


def test_register_184_room_temperature_expires_too():
    from unittest.mock import patch

    decoder = DanthermDecoder(lambda _: None)
    with patch("parser.time.monotonic", return_value=100):
        decoder.decode(frame(bytes.fromhex("4003020bb8")))
    assert decoder.data["room_temperature"] == 30.0
    with patch("parser.time.monotonic", return_value=401):
        decoder.decode(frame(bytes.fromhex("010600420019")))
    assert decoder.data["room_temperature"] is None


def test_supply_air_setpoint_from_hac1_write_block():
    decoder = DanthermDecoder(lambda _: None)
    body = bytes.fromhex("401000b900050aff011600000f18fee201")
    decoder.decode(frame(body))
    assert decoder.data["afterheat_setpoint"] == 22


def test_tcp_client_callback_can_be_rebound():
    import types

    package = types.ModuleType("hch_passivelink")
    package.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink")]
    sys.modules.setdefault("hch_passivelink", package)
    from hch_passivelink.client import PassiveLinkClient

    initial = []
    rebound = []
    client = PassiveLinkClient("127.0.0.1", 4196, initial.append)
    client.set_update_callback(rebound.append)
    client.decoder.decode(frame(bytes.fromhex("010600420019")))
    assert initial == []
    assert rebound[-1]["extract_fan_percent"] == 25


def test_block_1024_is_the_units_filter_counter():
    updates = {}
    decoder = DanthermDecoder(updates.update)
    decoder.decode(frame(bytes([1, 3, 0x04, 0x00, 0x00, 0x06])))
    decoder.decode(frame(bytes([1, 3, 12]) + b"".join(v.to_bytes(2, "big") for v in (1, 12, 249, 235, 0, 0))))
    assert (updates["filter_unit_period_months"], updates["filter_unit_life_raw"], updates["filter_unit_hours"]) == (12, 249, 235)


def test_missing_hrc2_t5_is_not_a_room_temperature():
    updates = {}
    decoder = DanthermDecoder(updates.update)
    decoder.decode(frame(bytes.fromhex("400300b4001e")))
    words = [1300, 2100, 2200, 1500, 0] + [0] * 20 + [2100, 2000, 0x8000, 0x8000, 0]
    decoder.decode(frame(bytes([0x40, 3, 60]) + b"".join(w.to_bytes(2, "big") for w in words)))
    assert decoder.data.get("room_temperature") is None
    assert decoder.data["outdoor_temperature"] == 13.0
    from alarms import derive_alarm_values
    assert derive_alarm_values(decoder.data)["room_temperature_sensor_fault"] is False
    # A real fault word from the unit still raises the alarm.
    decoder.decode(frame(bytes.fromhex("400300b4001e")))
    words[4] = 0x7FFF
    decoder.decode(frame(bytes([0x40, 3, 60]) + b"".join(w.to_bytes(2, "big") for w in words)))
    assert derive_alarm_values(decoder.data)["room_temperature_sensor_fault"] is True
