"""Bluetooth FTMS (Fitness Machine Service) protocol helpers.

Pure encode/decode functions, no BLE I/O, so everything here can be unit tested.
Reference: Bluetooth SIG "Fitness Machine Service 1.0" specification.
The Tacx Neo 2T (and most modern smart trainers) expose this service.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum


def uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# Services
FTMS_SERVICE = uuid16(0x1826)
HEART_RATE_SERVICE = uuid16(0x180D)
CYCLING_POWER_SERVICE = uuid16(0x1818)

# FTMS characteristics
FEATURE = uuid16(0x2ACC)
INDOOR_BIKE_DATA = uuid16(0x2AD2)
TRAINING_STATUS = uuid16(0x2AD3)
SUPPORTED_RESISTANCE_RANGE = uuid16(0x2AD6)
SUPPORTED_POWER_RANGE = uuid16(0x2AD8)
CONTROL_POINT = uuid16(0x2AD9)
MACHINE_STATUS = uuid16(0x2ADA)

# Heart rate
HEART_RATE_MEASUREMENT = uuid16(0x2A37)


class OpCode(IntEnum):
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_RESISTANCE = 0x04
    SET_TARGET_POWER = 0x05
    START_OR_RESUME = 0x07
    STOP_OR_PAUSE = 0x08
    SET_INDOOR_BIKE_SIMULATION = 0x11
    SET_WHEEL_CIRCUMFERENCE = 0x12
    RESPONSE = 0x80


class ResultCode(IntEnum):
    SUCCESS = 0x01
    NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    OPERATION_FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


@dataclass
class BikeData:
    """Snapshot of the Indoor Bike Data characteristic."""

    speed_kmh: float | None = None
    avg_speed_kmh: float | None = None
    cadence_rpm: float | None = None
    avg_cadence_rpm: float | None = None
    distance_m: int | None = None
    resistance: int | None = None
    power_w: int | None = None
    avg_power_w: int | None = None
    energy_kcal: int | None = None
    heart_rate: int | None = None
    elapsed_s: int | None = None
    remaining_s: int | None = None
    raw_flags: int = 0
    extra: dict = field(default_factory=dict)


def parse_indoor_bike_data(data: bytes | bytearray) -> BikeData:
    """Decode an Indoor Bike Data (0x2AD2) notification."""
    buf = bytes(data)
    if len(buf) < 2:
        raise ValueError("indoor bike data too short")
    flags = struct.unpack_from("<H", buf, 0)[0]
    off = 2
    out = BikeData(raw_flags=flags)

    def take(fmt: str):
        nonlocal off
        size = struct.calcsize(fmt)
        if off + size > len(buf):
            raise ValueError("indoor bike data truncated")
        val = struct.unpack_from(fmt, buf, off)[0]
        off += size
        return val

    def take_u24() -> int:
        nonlocal off
        if off + 3 > len(buf):
            raise ValueError("indoor bike data truncated")
        val = buf[off] | (buf[off + 1] << 8) | (buf[off + 2] << 16)
        off += 3
        return val

    # Bit 0 is "More Data": when it is 0, Instantaneous Speed IS present.
    if not flags & (1 << 0):
        out.speed_kmh = take("<H") / 100.0
    if flags & (1 << 1):
        out.avg_speed_kmh = take("<H") / 100.0
    if flags & (1 << 2):
        out.cadence_rpm = take("<H") / 2.0
    if flags & (1 << 3):
        out.avg_cadence_rpm = take("<H") / 2.0
    if flags & (1 << 4):
        out.distance_m = take_u24()
    if flags & (1 << 5):
        out.resistance = take("<h")
    if flags & (1 << 6):
        out.power_w = take("<h")
    if flags & (1 << 7):
        out.avg_power_w = take("<h")
    if flags & (1 << 8):
        out.energy_kcal = take("<H")
        take("<H")  # energy per hour
        take("<B")  # energy per minute
    if flags & (1 << 9):
        out.heart_rate = take("<B")
    if flags & (1 << 10):
        out.extra["met"] = take("<B") / 10.0
    if flags & (1 << 11):
        out.elapsed_s = take("<H")
    if flags & (1 << 12):
        out.remaining_s = take("<H")
    return out


def encode_request_control() -> bytes:
    return bytes([OpCode.REQUEST_CONTROL])


def encode_reset() -> bytes:
    return bytes([OpCode.RESET])


def encode_start_or_resume() -> bytes:
    return bytes([OpCode.START_OR_RESUME])


def encode_stop(pause: bool = False) -> bytes:
    return bytes([OpCode.STOP_OR_PAUSE, 0x02 if pause else 0x01])


def encode_set_target_power(watts: float) -> bytes:
    w = int(round(max(0, min(4000, watts))))
    return struct.pack("<Bh", OpCode.SET_TARGET_POWER, w)


def encode_set_target_resistance(level: float) -> bytes:
    """Resistance level, unitless 0.1 resolution (uint8 per FTMS 1.0)."""
    raw = int(round(max(0.0, min(25.5, level)) * 10))
    return struct.pack("<BB", OpCode.SET_TARGET_RESISTANCE, raw)


def encode_indoor_bike_simulation(
    grade_pct: float,
    wind_speed_mps: float = 0.0,
    crr: float = 0.004,
    cw: float = 0.51,
) -> bytes:
    """Set Indoor Bike Simulation Parameters (SIM / slope mode).

    grade_pct: road grade in percent (e.g. 5.0 = 5 %).
    crr: rolling resistance coefficient (resolution 0.0001).
    cw: wind resistance coefficient in kg/m (resolution 0.01).
    """
    grade = max(-40.0, min(40.0, grade_pct))
    return struct.pack(
        "<BhhBB",
        OpCode.SET_INDOOR_BIKE_SIMULATION,
        int(round(wind_speed_mps * 1000)),
        int(round(grade * 100)),
        int(round(max(0.0, min(0.0255, crr)) * 10000)),
        int(round(max(0.0, min(2.55, cw)) * 100)),
    )


@dataclass
class ControlPointResponse:
    request_opcode: int
    result: int

    @property
    def ok(self) -> bool:
        return self.result == ResultCode.SUCCESS

    def describe(self) -> str:
        try:
            op = OpCode(self.request_opcode).name
        except ValueError:
            op = hex(self.request_opcode)
        try:
            res = ResultCode(self.result).name
        except ValueError:
            res = hex(self.result)
        return f"{op}: {res}"


def parse_control_point_response(data: bytes | bytearray) -> ControlPointResponse | None:
    buf = bytes(data)
    if len(buf) < 3 or buf[0] != OpCode.RESPONSE:
        return None
    return ControlPointResponse(request_opcode=buf[1], result=buf[2])


def parse_supported_power_range(data: bytes | bytearray) -> tuple[int, int, int]:
    lo, hi, inc = struct.unpack_from("<hhH", bytes(data), 0)
    return lo, hi, inc


MACHINE_FEATURE_BITS = {
    0: "average speed",
    1: "cadence",
    2: "total distance",
    3: "inclination",
    4: "elevation gain",
    5: "pace",
    6: "step count",
    7: "resistance level",
    8: "stride count",
    9: "expended energy",
    10: "heart rate",
    11: "metabolic equivalent",
    12: "elapsed time",
    13: "remaining time",
    14: "power",
    15: "force on belt / power output",
    16: "user data retention",
}

TARGET_FEATURE_BITS = {
    0: "speed target",
    1: "inclination target",
    2: "resistance target",
    3: "power target (ERG)",
    4: "heart rate target",
    5: "targeted expended energy",
    6: "targeted step number",
    7: "targeted stride number",
    8: "targeted distance",
    9: "targeted training time",
    10: "targeted time in two HR zones",
    11: "targeted time in three HR zones",
    12: "targeted time in five HR zones",
    13: "indoor bike simulation (SIM)",
    14: "wheel circumference",
    15: "spin down control",
    16: "targeted cadence",
}


def parse_features(data: bytes | bytearray) -> tuple[list[str], list[str]]:
    machine, target = struct.unpack_from("<II", bytes(data), 0)
    m = [name for bit, name in MACHINE_FEATURE_BITS.items() if machine & (1 << bit)]
    t = [name for bit, name in TARGET_FEATURE_BITS.items() if target & (1 << bit)]
    return m, t


def parse_heart_rate(data: bytes | bytearray) -> int:
    buf = bytes(data)
    if not buf:
        raise ValueError("empty heart rate measurement")
    if buf[0] & 0x01:
        return struct.unpack_from("<H", buf, 1)[0]
    return buf[1]
