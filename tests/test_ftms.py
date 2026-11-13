import struct

import pytest

from smart_trainer import ftms


def test_parse_indoor_bike_data_typical_neo_packet():
    # flags: speed present (bit0=0), cadence (bit2), power (bit6)
    flags = (1 << 2) | (1 << 6)
    data = struct.pack("<HHHh", flags, 3250, 180, 245)
    d = ftms.parse_indoor_bike_data(data)
    assert d.speed_kmh == pytest.approx(32.5)
    assert d.cadence_rpm == pytest.approx(90.0)
    assert d.power_w == 245
    assert d.heart_rate is None


def test_parse_indoor_bike_data_more_data_and_extras():
    # More data flag set => no speed; distance (uint24), HR, elapsed time
    flags = (1 << 0) | (1 << 4) | (1 << 9) | (1 << 11)
    data = struct.pack("<H", flags) + (123456).to_bytes(3, "little") + bytes([150]) + struct.pack("<H", 600)
    d = ftms.parse_indoor_bike_data(data)
    assert d.speed_kmh is None
    assert d.distance_m == 123456
    assert d.heart_rate == 150
    assert d.elapsed_s == 600


def test_parse_indoor_bike_data_truncated():
    with pytest.raises(ValueError):
        ftms.parse_indoor_bike_data(struct.pack("<HH", 1 << 6, 0)[:3])


def test_encode_target_power():
    assert ftms.encode_set_target_power(250) == bytes([0x05, 0xFA, 0x00])
    assert ftms.encode_set_target_power(-10) == bytes([0x05, 0, 0])


def test_encode_simulation():
    b = ftms.encode_indoor_bike_simulation(5.0)
    op, wind, grade, crr, cw = struct.unpack("<BhhBB", b)
    assert op == 0x11 and wind == 0 and grade == 500 and crr == 40 and cw == 51
    _, _, g, _, _ = struct.unpack("<BhhBB", ftms.encode_indoor_bike_simulation(-3.25))
    assert g == -325


def test_control_point_response():
    r = ftms.parse_control_point_response(bytes([0x80, 0x05, 0x01]))
    assert r.ok and r.request_opcode == 0x05
    r = ftms.parse_control_point_response(bytes([0x80, 0x00, 0x05]))
    assert not r.ok and "CONTROL_NOT_PERMITTED" in r.describe()
    assert ftms.parse_control_point_response(bytes([0x01])) is None


def test_heart_rate():
    assert ftms.parse_heart_rate(bytes([0x00, 72])) == 72
    assert ftms.parse_heart_rate(bytes([0x01, 0x2C, 0x01])) == 300


def test_features():
    m, t = ftms.parse_features(struct.pack("<II", (1 << 1) | (1 << 14), (1 << 3) | (1 << 13)))
    assert "cadence" in m and "power" in m
    assert "power target (ERG)" in t and "indoor bike simulation (SIM)" in t
