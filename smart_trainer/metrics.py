"""Training metrics: Normalized Power, IF, TSS and power zones."""

from __future__ import annotations


def normalized_power(watts: list[float], window: int = 30) -> float:
    if not watts:
        return 0.0
    if len(watts) < window:
        return sum(watts) / len(watts)
    acc = 0.0
    rolling = []
    s = sum(watts[:window])
    rolling.append(s / window)
    for i in range(window, len(watts)):
        s += watts[i] - watts[i - window]
        rolling.append(s / window)
    acc = sum(r ** 4 for r in rolling) / len(rolling)
    return acc ** 0.25


def tss(seconds: float, np_: float, ftp: float) -> float:
    if ftp <= 0:
        return 0.0
    intensity = np_ / ftp
    return seconds * np_ * intensity / (ftp * 3600) * 100


# (upper bound as fraction of FTP, name, rich color)
ZONES = [
    (0.55, "Z1", "grey62"),
    (0.75, "Z2", "dodger_blue1"),
    (0.90, "Z3", "green3"),
    (1.05, "Z4", "gold1"),
    (1.20, "Z5", "dark_orange"),
    (float("inf"), "Z6", "red1"),
]


def zone_for(fraction: float | None) -> tuple[str, str]:
    if fraction is None:
        return "Libre", "grey50"
    for upper, name, color in ZONES:
        if fraction < upper:
            return name, color
    return ZONES[-1][1], ZONES[-1][2]
