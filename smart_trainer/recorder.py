"""Ride recording and export (TCX for Strava / Garmin Connect / TrainingPeaks / intervals.icu, plus CSV)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from .metrics import normalized_power, tss


@dataclass
class Sample:
    time: datetime
    elapsed: float
    power: int
    cadence: int | None
    speed_kmh: float | None
    distance_m: float
    heart_rate: int | None
    target: int | None


class Recorder:
    def __init__(self) -> None:
        self.samples: list[Sample] = []
        self.start: datetime | None = None
        self._distance = 0.0

    def add(self, *, elapsed: float, power: float | None, cadence: float | None,
            speed_kmh: float | None, heart_rate: int | None, target: float | None,
            dt: float = 1.0, when: datetime | None = None) -> None:
        if self.start is None:
            # anchor so that elapsed == 0 corresponds to the ride start
            self.start = (when or datetime.now(timezone.utc)) - timedelta(seconds=elapsed)
        if speed_kmh:
            self._distance += speed_kmh / 3.6 * dt
        self.samples.append(Sample(
            time=when or (self.start + timedelta(seconds=elapsed)),
            elapsed=elapsed,
            power=int(round(power or 0)),
            cadence=int(round(cadence)) if cadence is not None else None,
            speed_kmh=speed_kmh,
            distance_m=self._distance,
            heart_rate=heart_rate,
            target=int(round(target)) if target is not None else None,
        ))

    def summary(self, ftp: float) -> dict:
        s = self.samples
        if not s:
            return {}
        watts = [x.power for x in s]
        hrs = [x.heart_rate for x in s if x.heart_rate]
        cads = [x.cadence for x in s if x.cadence]
        np_ = normalized_power(watts)
        secs = len(s)
        return {
            "duration": secs,
            "distance_km": s[-1].distance_m / 1000,
            "avg_power": sum(watts) / secs,
            "max_power": max(watts),
            "np": np_,
            "if": np_ / ftp if ftp else 0.0,
            "tss": tss(secs, np_, ftp),
            "kj": sum(watts) / 1000,
            "avg_hr": sum(hrs) / len(hrs) if hrs else None,
            "max_hr": max(hrs) if hrs else None,
            "avg_cadence": sum(cads) / len(cads) if cads else None,
        }

    def save(self, directory: Path, name: str) -> tuple[Path, Path] | None:
        if not self.samples:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.start.astimezone().strftime("%Y-%m-%d_%H%M")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")[:40]
        base = directory / f"{stamp}_{safe or 'ride'}"
        tcx, csvp = base.with_suffix(".tcx"), base.with_suffix(".csv")
        tcx.write_text(self.to_tcx(name), encoding="utf-8")
        self.to_csv(csvp)
        return tcx, csvp

    def to_csv(self, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "elapsed_s", "power_w", "target_w", "cadence_rpm",
                        "speed_kmh", "distance_m", "heart_rate"])
            for x in self.samples:
                w.writerow([x.time.isoformat(), f"{x.elapsed:.0f}", x.power,
                            "" if x.target is None else x.target,
                            "" if x.cadence is None else x.cadence,
                            "" if x.speed_kmh is None else f"{x.speed_kmh:.2f}",
                            f"{x.distance_m:.1f}",
                            "" if x.heart_rate is None else x.heart_rate])

    def to_tcx(self, name: str = "") -> str:
        def iso(d: datetime) -> str:
            return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        s = self.samples
        summ = self.summary(ftp=1)
        start = iso(self.start)
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"'
            ' xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">',
            "  <Activities>",
            '    <Activity Sport="Biking">',
            f"      <Id>{start}</Id>",
            f'      <Lap StartTime="{start}">',
            f"        <TotalTimeSeconds>{len(s)}</TotalTimeSeconds>",
            f"        <DistanceMeters>{s[-1].distance_m:.1f}</DistanceMeters>",
            f"        <Calories>{int(summ['kj'])}</Calories>",
        ]
        if summ.get("avg_hr"):
            lines.append(f"        <AverageHeartRateBpm><Value>{int(summ['avg_hr'])}</Value></AverageHeartRateBpm>")
            lines.append(f"        <MaximumHeartRateBpm><Value>{int(summ['max_hr'])}</Value></MaximumHeartRateBpm>")
        lines += [
            "        <Intensity>Active</Intensity>",
            "        <TriggerMethod>Manual</TriggerMethod>",
            "        <Track>",
        ]
        for x in s:
            tp = [f"            <Time>{iso(x.time)}</Time>",
                  f"            <DistanceMeters>{x.distance_m:.1f}</DistanceMeters>"]
            if x.heart_rate:
                tp.append(f"            <HeartRateBpm><Value>{x.heart_rate}</Value></HeartRateBpm>")
            if x.cadence is not None:
                tp.append(f"            <Cadence>{min(254, x.cadence)}</Cadence>")
            ext = f"<ns3:Watts>{x.power}</ns3:Watts>"
            if x.speed_kmh is not None:
                ext = f"<ns3:Speed>{x.speed_kmh / 3.6:.2f}</ns3:Speed>" + ext
            tp.append(f"            <Extensions><ns3:TPX>{ext}</ns3:TPX></Extensions>")
            lines.append("          <Trackpoint>")
            lines += tp
            lines.append("          </Trackpoint>")
        lines += [
            "        </Track>",
            "      </Lap>",
            f"      <Notes>{escape(name)}</Notes>",
            "    </Activity>",
            "  </Activities>",
            "</TrainingCenterDatabase>",
            "",
        ]
        return "\n".join(lines)
