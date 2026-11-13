"""Workout model and parsers.

Supported formats:
  * .zwo  -- Zwift workout XML (the de-facto standard; exported by many sites)
  * .erg  -- absolute watts over minutes
  * .mrc  -- percent of FTP over minutes
  * .txt / .wkt -- a simple human-friendly text format (see README)
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = (".zwo", ".erg", ".mrc", ".txt", ".wkt")


@dataclass
class Segment:
    duration: float  # seconds
    start: float | None  # power at segment start (fraction of FTP, or watts if absolute)
    end: float | None  # power at segment end; None start/end means free ride
    kind: str = "steady"  # steady | ramp | warmup | cooldown | on | off | free | max
    cadence: int | None = None
    label: str = ""
    absolute: bool = False  # True when start/end are watts instead of FTP fraction
    messages: list[tuple[float, str]] = field(default_factory=list)  # (offset_s, text)

    @property
    def is_free(self) -> bool:
        return self.start is None

    @property
    def is_ramp(self) -> bool:
        return not self.is_free and self.end is not None and abs(self.end - self.start) > 1e-9

    def value_at(self, offset: float) -> float | None:
        if self.is_free:
            return None
        if self.end is None or self.duration <= 0:
            return self.start
        frac = min(1.0, max(0.0, offset / self.duration))
        return self.start + (self.end - self.start) * frac

    def watts_at(self, offset: float, ftp: float) -> float | None:
        v = self.value_at(offset)
        if v is None:
            return None
        return v if self.absolute else v * ftp

    def fraction_at(self, offset: float, ftp: float) -> float | None:
        v = self.value_at(offset)
        if v is None:
            return None
        return v / ftp if self.absolute else v


@dataclass
class Workout:
    name: str
    segments: list[Segment]
    description: str = ""
    author: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        self._starts: list[float] = []
        t = 0.0
        for seg in self.segments:
            self._starts.append(t)
            t += seg.duration
        self._total = t

    @property
    def total_duration(self) -> float:
        return self._total

    def segment_start(self, index: int) -> float:
        return self._starts[index]

    def segment_index_at(self, t: float) -> int | None:
        """Index of the segment active at workout time t (None when finished)."""
        if t < 0 or not self.segments:
            return 0 if self.segments else None
        if t >= self._total:
            return None
        lo, hi = 0, len(self._starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._starts[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def fraction_at(self, t: float, ftp: float) -> float | None:
        idx = self.segment_index_at(t)
        if idx is None:
            return None
        return self.segments[idx].fraction_at(t - self._starts[idx], ftp)

    def watts_at(self, t: float, ftp: float) -> float | None:
        idx = self.segment_index_at(t)
        if idx is None:
            return None
        return self.segments[idx].watts_at(t - self._starts[idx], ftp)

    def stats(self, ftp: float) -> dict:
        """Estimated IF / TSS / average power of the planned workout."""
        secs = int(self._total)
        watts = []
        for s in range(secs):
            w = self.watts_at(s + 0.5, ftp)
            watts.append(w if w is not None else 0.5 * ftp)  # free ride ~ endurance
        from .metrics import normalized_power, tss

        np_ = normalized_power(watts)
        return {
            "duration": self._total,
            "avg_watts": sum(watts) / len(watts) if watts else 0.0,
            "np": np_,
            "if": np_ / ftp if ftp else 0.0,
            "tss": tss(secs, np_, ftp),
            "kj": sum(watts) / 1000.0,
        }


# --------------------------------------------------------------------------- ZWO


def _f(el: ET.Element, *names: str, default: float | None = None) -> float | None:
    for n in names:
        for key in (n, n.lower()):
            if key in el.attrib:
                return float(el.attrib[key])
    return default


def _text_events(el: ET.Element) -> list[tuple[float, str]]:
    out = []
    for child in el:
        if child.tag.lower() in ("textevent", "textnotification"):
            off = float(child.attrib.get("timeoffset", child.attrib.get("TimeOffset", 0)))
            msg = child.attrib.get("message", child.attrib.get("Message", child.text or ""))
            if msg:
                out.append((off, msg.strip()))
    return out


def parse_zwo(text: str, source: str = "") -> Workout:
    root = ET.fromstring(text)
    name = (root.findtext("name") or Path(source).stem or "Workout").strip()
    description = (root.findtext("description") or "").strip()
    author = (root.findtext("author") or "").strip()
    wk = root.find("workout")
    if wk is None:
        raise ValueError("ZWO sin elemento <workout>")

    segs: list[Segment] = []
    for el in wk:
        tag = el.tag.lower()
        dur = _f(el, "Duration", default=0.0) or 0.0
        cad = _f(el, "Cadence")
        cad_i = int(cad) if cad else None
        msgs = _text_events(el)
        if tag in ("warmup", "cooldown", "ramp"):
            lo = _f(el, "PowerLow", "Power", default=0.5)
            hi = _f(el, "PowerHigh", "Power", default=lo)
            segs.append(Segment(dur, lo, hi, kind=tag if tag != "ramp" else "ramp",
                                cadence=cad_i, messages=msgs))
        elif tag in ("steadystate", "solidstate"):
            p = _f(el, "Power")
            if p is None:
                lo = _f(el, "PowerLow", default=0.5)
                hi = _f(el, "PowerHigh", default=lo)
                p = (lo + hi) / 2
            segs.append(Segment(dur, p, p, kind="steady", cadence=cad_i, messages=msgs))
        elif tag == "intervalst":
            reps = int(_f(el, "Repeat", default=1) or 1)
            on_d = _f(el, "OnDuration", default=0.0) or 0.0
            off_d = _f(el, "OffDuration", default=0.0) or 0.0
            on_p = _f(el, "OnPower", "PowerHigh", default=1.0)
            off_p = _f(el, "OffPower", "PowerLow", default=0.5)
            cad_on = _f(el, "Cadence")
            cad_off = _f(el, "CadenceResting")
            for r in range(reps):
                # text events are relative to the whole IntervalsT block
                block_off = r * (on_d + off_d)
                on_msgs = [(o - block_off, m) for o, m in msgs if block_off <= o < block_off + on_d]
                off_msgs = [(o - block_off - on_d, m) for o, m in msgs
                            if block_off + on_d <= o < block_off + on_d + off_d]
                segs.append(Segment(on_d, on_p, on_p, kind="on", label=f"{r + 1}/{reps}",
                                    cadence=int(cad_on) if cad_on else None, messages=on_msgs))
                if off_d > 0:
                    segs.append(Segment(off_d, off_p, off_p, kind="off", label=f"{r + 1}/{reps}",
                                        cadence=int(cad_off) if cad_off else None,
                                        messages=off_msgs))
        elif tag == "freeride":
            segs.append(Segment(dur, None, None, kind="free", cadence=cad_i, messages=msgs))
        elif tag == "maxeffort":
            segs.append(Segment(dur, None, None, kind="max", messages=msgs))
        # unknown tags are ignored
    if not segs:
        raise ValueError("el workout no tiene segmentos")
    return Workout(name=name, segments=segs, description=description, author=author, source=source)


# ---------------------------------------------------------------------- ERG / MRC


def parse_erg_mrc(text: str, source: str = "", percent: bool | None = None) -> Workout:
    name = Path(source).stem or "Workout"
    description = ""
    points: list[tuple[float, float]] = []
    in_data = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        up = line.upper()
        if up.startswith("[COURSE DATA]"):
            in_data = True
            continue
        if up.startswith("[END COURSE DATA]"):
            in_data = False
            continue
        if up.startswith("["):
            continue
        if not in_data:
            if "=" in line:
                key, _, val = line.partition("=")
                k = key.strip().upper()
                if k in ("FILE NAME", "FILENAME"):
                    name = val.strip() or name
                elif k == "DESCRIPTION":
                    description = val.strip()
            elif percent is None and "MINUTES" in up:
                percent = "PERCENT" in up
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]) * 60.0, float(parts[1])))
        except ValueError:
            continue
    if percent is None:
        percent = source.lower().endswith(".mrc")
    if len(points) < 2:
        raise ValueError("archivo ERG/MRC sin datos suficientes")

    segs: list[Segment] = []
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        dur = t1 - t0
        if dur <= 0:
            continue  # vertical step
        a, b = (p0 / 100.0, p1 / 100.0) if percent else (p0, p1)
        kind = "steady" if abs(a - b) < 1e-9 else "ramp"
        segs.append(Segment(dur, a, b, kind=kind, absolute=not percent))
    return Workout(name=name, segments=segs, description=description, source=source)


# ----------------------------------------------------------------- text format

_DUR_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+)s)?$")


def parse_duration(tok: str) -> float:
    tok = tok.strip().lower()
    if ":" in tok:
        parts = [int(p) for p in tok.split(":")]
        secs = 0
        for p in parts:
            secs = secs * 60 + p
        return float(secs)
    if tok.endswith("min"):
        tok = tok[:-3] + "m"
    if tok.isdigit():
        return float(int(tok) * 60)  # bare number = minutes
    m = _DUR_RE.match(tok)
    if not m or not any(m.groups()):
        raise ValueError(f"duración inválida: {tok!r}")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


_POWER_RE = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*(%|w|ftp)?$", re.I)


def _parse_power(tok: str) -> tuple[float | None, float | None, bool, str]:
    t = tok.strip().lower()
    if t in ("free", "libre", "freeride"):
        return None, None, False, "free"
    m = _POWER_RE.match(t)
    if not m:
        raise ValueError(f"potencia inválida: {tok!r}")
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else a
    unit = (m.group(3) or "%").lower()
    if unit == "w":
        return a, b, True, "ramp" if a != b else "steady"
    if unit == "ftp":
        return a, b, False, "ramp" if a != b else "steady"
    return a / 100.0, b / 100.0, False, "ramp" if a != b else "steady"


def _parse_step(text: str) -> Segment:
    msg = ""
    q = re.search(r'"([^"]*)"', text)
    if q:
        msg = q.group(1)
        text = text[: q.start()] + text[q.end():]
    cadence = None
    c = re.search(r"@\s*(\d+)\s*(?:rpm)?", text, re.I)
    if c:
        cadence = int(c.group(1))
        text = text[: c.start()] + text[c.end():]
    toks = text.split()
    if len(toks) < 2:
        raise ValueError(f"paso inválido: {text.strip()!r} (esperado: <duración> <potencia>)")
    dur = parse_duration(toks[0])
    power = "".join(toks[1:])
    a, b, absolute, kind = _parse_power(power)
    msgs = [(0.0, msg)] if msg else []
    return Segment(dur, a, b, kind=kind, cadence=cadence, absolute=absolute, messages=msgs)


def _split_steps(body: str) -> list[str]:
    # split on commas that are outside quotes
    out, cur, inq = [], [], False
    for ch in body:
        if ch == '"':
            inq = not inq
        if ch == "," and not inq:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def parse_text(text: str, source: str = "") -> Workout:
    name = Path(source).stem or "Workout"
    description = ""
    author = ""
    segs: list[Segment] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        hm = re.match(r"^(name|nombre|description|descripcion|descripción|author|autor)\s*:\s*(.*)$",
                      line, re.I)
        if hm:
            key, val = hm.group(1).lower(), hm.group(2).strip()
            if key in ("name", "nombre"):
                name = val
            elif key in ("author", "autor"):
                author = val
            else:
                description = val
            continue
        try:
            rm = re.match(r"^(\d+)\s*x\s*\(?(.*?)\)?$", line, re.I)
            if rm:
                reps = int(rm.group(1))
                steps = [_parse_step(s) for s in _split_steps(rm.group(2))]
                peak = max((st.start for st in steps if not st.is_free), default=0.0)
                for r in range(reps):
                    for st in steps:
                        kind = st.kind
                        if kind == "steady" and len(steps) >= 2:
                            hard = (st.start >= 0.75 * peak) if st.absolute else st.start >= 0.76
                            kind = "on" if hard else "off"
                        segs.append(Segment(st.duration, st.start, st.end, kind=kind,
                                            cadence=st.cadence, absolute=st.absolute,
                                            label=f"{r + 1}/{reps}", messages=list(st.messages)))
            else:
                segs.append(_parse_step(line))
        except ValueError as exc:
            raise ValueError(f"línea {lineno}: {exc}") from None
    if not segs:
        raise ValueError("el workout no tiene pasos")
    return Workout(name=name, segments=segs, description=description, author=author, source=source)


# ----------------------------------------------------------------------- loader


def load_workout(path: str | Path) -> Workout:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    ext = p.suffix.lower()
    if ext == ".zwo":
        return parse_zwo(text, str(p))
    if ext == ".erg":
        return parse_erg_mrc(text, str(p), percent=False)
    if ext == ".mrc":
        return parse_erg_mrc(text, str(p), percent=True)
    if ext in (".txt", ".wkt"):
        return parse_text(text, str(p))
    # sniff
    if text.lstrip().startswith("<"):
        return parse_zwo(text, str(p))
    if "[COURSE DATA]" in text.upper():
        return parse_erg_mrc(text, str(p))
    return parse_text(text, str(p))


def format_duration(secs: float) -> str:
    secs = int(math.floor(max(0, secs) + 1e-6))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def describe_segment(seg: Segment, ftp: float) -> str:
    if seg.kind == "max":
        return "ESFUERZO MÁXIMO"
    if seg.is_free:
        return "Libre"
    a = seg.start if seg.absolute else seg.start * ftp
    b = seg.end if seg.absolute else seg.end * ftp
    pa = seg.start / ftp if seg.absolute else seg.start
    pb = seg.end / ftp if seg.absolute else seg.end
    if seg.is_ramp:
        return f"{pa * 100:.0f}→{pb * 100:.0f}% ({a:.0f}→{b:.0f} W)"
    return f"{pa * 100:.0f}% ({a:.0f} W)"
