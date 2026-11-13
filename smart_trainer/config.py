"""Persistent user configuration (~/.config/smart-trainer/config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("SMART_TRAINER_HOME")
    if base:
        return Path(base).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "smart-trainer"


@dataclass
class Config:
    ftp: int = 200
    weight_kg: float = 75.0
    trainer_address: str | None = None
    trainer_name: str | None = None
    hrm_address: str | None = None
    workouts_dir: str = "~/smart-trainer/workouts"
    rides_dir: str = "~/smart-trainer/rides"
    auto_pause: bool = True

    @property
    def workouts_path(self) -> Path:
        return Path(self.workouts_dir).expanduser()

    @property
    def rides_path(self) -> Path:
        return Path(self.rides_dir).expanduser()

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> Path:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return p
