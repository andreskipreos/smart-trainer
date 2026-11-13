"""Trainer / sensor devices: real BLE FTMS trainer, BLE heart rate strap and a simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass

from . import ftms
from .ftms import BikeData

log = logging.getLogger(__name__)


class Clock:
    """Monotonic clock with an optional speed-up factor (used by the simulator / demos)."""

    def __init__(self, speed: float = 1.0) -> None:
        self.speed = max(0.01, speed)
        self._t0 = time.monotonic()

    def now(self) -> float:
        return (time.monotonic() - self._t0) * self.speed

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.speed)


# --------------------------------------------------------------------- scanning


@dataclass
class FoundDevice:
    address: str
    name: str
    rssi: int | None
    services: list[str]
    ble_device: object = None

    @property
    def is_trainer(self) -> bool:
        return ftms.FTMS_SERVICE in self.services

    @property
    def is_hrm(self) -> bool:
        return ftms.HEART_RATE_SERVICE in self.services


async def scan(timeout: float = 8.0) -> list[FoundDevice]:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        services = [s.lower() for s in (adv.service_uuids or [])]
        out.append(FoundDevice(
            address=dev.address,
            name=adv.local_name or dev.name or "",
            rssi=adv.rssi,
            services=services,
            ble_device=dev,
        ))
    out.sort(key=lambda d: -(d.rssi or -999))
    return out


async def find_device(
    *, address: str | None, name: str | None, service: str, timeout: float = 10.0
) -> FoundDevice | None:
    """Find a device by address, or by name substring + advertised service."""
    from bleak import BleakScanner

    if address:
        dev = await BleakScanner.find_device_by_address(address, timeout=timeout)
        if dev is None:
            return None
        return FoundDevice(dev.address, dev.name or "", None, [], dev)

    needle = (name or "").lower()

    def match(dev, adv) -> bool:
        uuids = [s.lower() for s in (adv.service_uuids or [])]
        nm = (adv.local_name or dev.name or "").lower()
        if needle:
            return needle in nm
        return service in uuids

    dev = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    if dev is None:
        return None
    return FoundDevice(dev.address, dev.name or "", None, [], dev)


# --------------------------------------------------------------------- trainers


class Trainer:
    """Common interface for trainers.

    Commands are "latest wins": set_erg()/set_sim() return immediately and a background
    worker sends the most recent desired state to the device. This keeps the UI loop
    responsive even if BLE writes are slow.
    """

    name = "trainer"

    def __init__(self) -> None:
        self.data = BikeData()
        self.last_update = 0.0
        self.connected = False
        self.status = "desconectado"
        self.mode: str | None = None  # "erg" | "sim"
        self.target_power: float | None = None
        self.grade: float = 0.0
        self._desired: tuple[str, float] | None = None
        self._wake = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._sent: tuple[str, float] | None = None

    def set_erg(self, watts: float) -> None:
        self.mode = "erg"
        self.target_power = watts
        self._desired = ("erg", float(watts))
        self._wake.set()

    def set_sim(self, grade_pct: float) -> None:
        self.mode = "sim"
        self.grade = grade_pct
        self.target_power = None
        self._desired = ("sim", float(grade_pct))
        self._wake.set()

    async def _send(self, mode: str, value: float) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    async def _command_worker(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            desired = self._desired
            if desired is None or desired == self._sent or not self.connected:
                continue
            try:
                ok = await self._send(*desired)
                if ok:
                    self._sent = desired
            except Exception as exc:  # BLE errors must never kill the ride
                log.warning("command failed: %s", exc)
                self.status = f"error: {exc}"

    def _start_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._command_worker())

    def resend(self) -> None:
        """Force re-sending the desired state (e.g. after a reconnect)."""
        self._sent = None
        self._wake.set()

    async def connect(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def disconnect(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._worker = None


class FtmsTrainer(Trainer):
    """A real smart trainer (e.g. Tacx Neo 2T) controlled over Bluetooth FTMS."""

    def __init__(self, address: str | None = None, name_filter: str | None = None) -> None:
        super().__init__()
        self.address = address
        self.name_filter = name_filter
        self.name = name_filter or address or "FTMS"
        self._client = None
        self._pending: tuple[int, asyncio.Future] | None = None
        self._cp_lock = asyncio.Lock()
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self.power_range: tuple[int, int, int] | None = None
        self.features: tuple[list[str], list[str]] | None = None

    # -- connection -----------------------------------------------------------
    async def connect(self) -> None:
        from bleak import BleakClient

        self.status = "buscando…"
        found = await find_device(address=self.address, name=self.name_filter,
                                  service=ftms.FTMS_SERVICE, timeout=15.0)
        if found is None:
            raise RuntimeError(
                "No encontré el rodillo. ¿Está encendido, sin otra app conectada (Zwift, Tacx app, "
                "Garmin) y cerca? Prueba `trainer scan`.")
        self.address = found.address
        self.name = found.name or found.address
        self.status = "conectando…"
        client = BleakClient(found.ble_device or found.address,
                             disconnected_callback=self._on_disconnect, timeout=20.0)
        await client.connect()
        self._client = client
        await self._setup()
        self.connected = True
        self.status = "conectado"
        self._start_worker()

    async def _setup(self) -> None:
        c = self._client
        try:
            self.features = ftms.parse_features(await c.read_gatt_char(ftms.FEATURE))
        except Exception as exc:
            log.info("could not read features: %s", exc)
        try:
            self.power_range = ftms.parse_supported_power_range(
                await c.read_gatt_char(ftms.SUPPORTED_POWER_RANGE))
        except Exception as exc:
            log.info("could not read power range: %s", exc)
        await c.start_notify(ftms.CONTROL_POINT, self._on_control_point)
        await c.start_notify(ftms.INDOOR_BIKE_DATA, self._on_bike_data)
        res = await self._control(ftms.encode_request_control())
        if res is not None and not res.ok:
            raise RuntimeError(f"El rodillo no cedió el control ({res.describe()}). "
                               "Cierra otras apps conectadas al rodillo.")
        await self._control(ftms.encode_start_or_resume())

    def _on_disconnect(self, _client) -> None:
        self.connected = False
        if self._closing:
            return
        self.status = "desconectado — reconectando…"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = loop.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        delay = 1.0
        while not self._closing and not self.connected:
            try:
                await self.connect()
                self.resend()
                return
            except Exception as exc:
                self.status = f"reconectando… ({exc.__class__.__name__})"
                await asyncio.sleep(delay)
                delay = min(10.0, delay * 2)

    async def disconnect(self) -> None:
        self._closing = True
        await super().disconnect()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._client is not None:
            try:
                if self._client.is_connected:
                    try:
                        await self._control(ftms.encode_reset(), timeout=1.5)
                    except Exception:
                        pass
                    await self._client.disconnect()
            except Exception:
                pass
        self.connected = False
        self.status = "desconectado"

    # -- notifications ----------------------------------------------------------
    def _on_bike_data(self, _sender, data: bytearray) -> None:
        try:
            parsed = ftms.parse_indoor_bike_data(data)
        except ValueError as exc:
            log.debug("bad bike data %s: %s", data.hex(), exc)
            return
        # The Neo may split fields across several notifications; merge them.
        d = self.data
        for fname in ("speed_kmh", "cadence_rpm", "distance_m", "power_w", "heart_rate",
                      "resistance", "avg_power_w", "avg_speed_kmh"):
            v = getattr(parsed, fname)
            if v is not None:
                setattr(d, fname, v)
        self.last_update = time.monotonic()

    def _on_control_point(self, _sender, data: bytearray) -> None:
        resp = ftms.parse_control_point_response(data)
        if resp is None:
            return
        if self._pending and self._pending[0] == resp.request_opcode:
            fut = self._pending[1]
            if not fut.done():
                fut.set_result(resp)

    # -- commands ----------------------------------------------------------------
    async def _control(self, payload: bytes, timeout: float = 3.0):
        async with self._cp_lock:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._pending = (payload[0], fut)
            await self._client.write_gatt_char(ftms.CONTROL_POINT, payload, response=True)
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                log.info("no control point response for %s", payload.hex())
                return None
            finally:
                self._pending = None

    async def _send(self, mode: str, value: float) -> bool:
        payload = (ftms.encode_set_target_power(value) if mode == "erg"
                   else ftms.encode_indoor_bike_simulation(value))
        res = await self._control(payload)
        if res is not None and res.result == ftms.ResultCode.CONTROL_NOT_PERMITTED:
            await self._control(ftms.encode_request_control())
            res = await self._control(payload)
        if res is not None and not res.ok:
            self.status = f"rodillo respondió {res.describe()}"
            return res.result != ftms.ResultCode.CONTROL_NOT_PERMITTED
        self.status = "conectado"
        return True


class SimulatedTrainer(Trainer):
    """A fake trainer so you can try workouts without hardware (`--sim`)."""

    name = "Simulador"

    def __init__(self, clock: Clock, ftp: float = 250, rider_kg: float = 75) -> None:
        super().__init__()
        self.clock = clock
        self.ftp = ftp
        self.rider_kg = rider_kg
        self._task: asyncio.Task | None = None
        self._power = 0.0
        self._cadence = 0.0
        self._hr = 65.0
        self._dist = 0.0
        self._speed = 0.0
        self.rider_stopped = False  # simulate the rider not pedaling

    async def connect(self) -> None:
        self.connected = True
        self.status = "simulado"
        self._task = asyncio.create_task(self._loop())
        self._start_worker()

    async def disconnect(self) -> None:
        await super().disconnect()
        if self._task:
            self._task.cancel()
        self.connected = False

    async def _send(self, mode: str, value: float) -> bool:
        return True

    async def _loop(self) -> None:
        last = self.clock.now()
        warmup = 3.0  # seconds before the virtual rider starts pedaling
        while True:
            await self.clock.sleep(0.25)
            now = self.clock.now()
            dt = now - last
            last = now
            if now < warmup or self.rider_stopped:
                target_p, target_c = 0.0, 0.0
            elif self.mode == "erg" and self.target_power is not None:
                target_p = self.target_power
                target_c = 88 + 6 * math.sin(now / 40)
            else:
                target_p = max(60.0, 0.65 * self.ftp + self.grade * 0.06 * self.ftp)
                target_c = 85 - self.grade * 1.5
            k = min(1.0, dt * 1.5)
            self._power += (target_p - self._power) * k
            self._cadence += (target_c - self._cadence) * k
            power = max(0.0, self._power + (random.gauss(0, 6) if self._power > 5 else 0))
            cad = max(0.0, self._cadence + (random.gauss(0, 1.2) if self._cadence > 5 else 0))
            hr_target = 60 + 110 * (power / self.ftp) ** 0.8 if power > 5 else 70
            self._hr += (min(195, hr_target) - self._hr) * min(1.0, dt / 25)
            self._speed = speed_from_power(power, self.grade if self.mode == "sim" else 0.0,
                                           self.rider_kg + 9)
            self._dist += self._speed * dt
            self.data = BikeData(
                speed_kmh=self._speed * 3.6,
                cadence_rpm=round(cad),
                power_w=int(round(power)),
                distance_m=int(self._dist),
                heart_rate=int(round(self._hr)),
            )
            self.last_update = time.monotonic()


def speed_from_power(power: float, grade_pct: float = 0.0, mass_kg: float = 84.0,
                     cda: float = 0.32, crr: float = 0.004, rho: float = 1.226) -> float:
    """Steady-state road speed (m/s) for a given power, solved with Newton's method."""
    if power <= 0:
        return 0.0
    g = 9.81
    slope = math.atan(grade_pct / 100.0)
    f_const = mass_kg * g * (math.sin(slope) + crr * math.cos(slope))
    v = 8.0
    for _ in range(30):
        f = 0.5 * cda * rho * v ** 3 + f_const * v - power
        df = 1.5 * cda * rho * v ** 2 + f_const
        if df <= 0:
            break
        nv = v - f / df
        if nv <= 0:
            nv = v / 2
        if abs(nv - v) < 1e-4:
            v = nv
            break
        v = nv
    return max(0.0, v)


class HeartRateMonitor:
    """Standard BLE heart rate strap (Garmin HRM, Polar H10, Wahoo TICKR, …)."""

    def __init__(self, address: str | None = None, name_filter: str | None = None) -> None:
        self.address = address
        self.name_filter = name_filter
        self.name = name_filter or address or "HRM"
        self.bpm: int | None = None
        self.last_update = 0.0
        self.connected = False
        self._client = None

    async def connect(self) -> None:
        from bleak import BleakClient

        found = await find_device(address=self.address, name=self.name_filter,
                                  service=ftms.HEART_RATE_SERVICE, timeout=10.0)
        if found is None:
            raise RuntimeError("No encontré la banda de frecuencia cardíaca.")
        self.name = found.name or found.address
        self._client = BleakClient(found.ble_device or found.address,
                                   disconnected_callback=self._on_disconnect)
        await self._client.connect()
        await self._client.start_notify(ftms.HEART_RATE_MEASUREMENT, self._on_hr)
        self.connected = True

    def _on_disconnect(self, _c) -> None:
        self.connected = False

    def _on_hr(self, _s, data: bytearray) -> None:
        try:
            self.bpm = ftms.parse_heart_rate(data)
            self.last_update = time.monotonic()
        except ValueError:
            pass

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self.connected = False
