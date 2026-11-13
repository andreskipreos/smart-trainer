"""Ride session: advances the workout clock, drives the trainer and records the ride.

Contains no terminal / UI code, so it can be tested with a fake clock and trainer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .devices import HeartRateMonitor, Trainer
from .recorder import Recorder
from .workout import Segment, Workout, format_duration

AUTO_PAUSE_AFTER = 3.0  # seconds without pedaling
MESSAGE_SECONDS = 10.0
NOTICE_SECONDS = 3.0
QUIT_CONFIRM_SECONDS = 3.0
GRADE_STEP = 0.5
BIAS_STEP = 0.05


@dataclass
class Notice:
    text: str
    until: float


class Session:
    def __init__(
        self,
        trainer: Trainer,
        ftp: float,
        workout: Workout | None = None,
        hrm: HeartRateMonitor | None = None,
        recorder: Recorder | None = None,
        bias: float = 1.0,
        auto_pause: bool = True,
        start_grade: float = 0.0,
    ) -> None:
        self.trainer = trainer
        self.ftp = ftp
        self.workout = workout
        self.hrm = hrm
        self.recorder = recorder or Recorder()
        self.bias = bias
        self.auto_pause = auto_pause
        self.grade = start_grade
        self.erg_enabled = workout is not None

        self.workout_time = 0.0
        self.ride_time = 0.0
        self.started = False
        self.paused = False
        self.auto_paused = False
        self.finished = False
        self.quit = False
        self._quit_armed_until = -1.0
        self._last_now: float | None = None
        self._last_pedal = -1e9
        self._sample_acc = 0.0
        self._last_cmd: tuple[str, int] | None = None
        self._last_cmd_time = -1e9
        self._last_seg_idx: int | None = None
        self.notice: Notice | None = None
        self.now = 0.0

    # ------------------------------------------------------------------ queries
    @property
    def segment_index(self) -> int | None:
        if not self.workout or self.finished:
            return None
        return self.workout.segment_index_at(self.workout_time)

    @property
    def segment(self) -> Segment | None:
        idx = self.segment_index
        return None if idx is None else self.workout.segments[idx]

    @property
    def segment_offset(self) -> float:
        idx = self.segment_index
        if idx is None:
            return 0.0
        return self.workout_time - self.workout.segment_start(idx)

    @property
    def segment_remaining(self) -> float:
        seg = self.segment
        return 0.0 if seg is None else max(0.0, seg.duration - self.segment_offset)

    @property
    def next_segment(self) -> Segment | None:
        idx = self.segment_index
        if idx is None or idx + 1 >= len(self.workout.segments):
            return None
        return self.workout.segments[idx + 1]

    @property
    def target_watts(self) -> float | None:
        seg = self.segment
        if seg is None:
            return None
        w = seg.watts_at(self.segment_offset, self.ftp)
        return None if w is None else w * self.bias

    @property
    def erg_active(self) -> bool:
        return (self.erg_enabled and not self.paused and self.target_watts is not None)

    @property
    def heart_rate(self) -> int | None:
        if self.hrm is not None and self.hrm.bpm:
            return self.hrm.bpm
        return self.trainer.data.heart_rate

    @property
    def running(self) -> bool:
        return self.started and not self.paused and not self.auto_paused

    @property
    def current_message(self) -> str | None:
        if self.notice and self.now < self.notice.until:
            return self.notice.text
        seg = self.segment
        if seg is None:
            return None
        off = self.segment_offset
        for t, msg in seg.messages:
            if t <= off < t + MESSAGE_SECONDS:
                return msg
        return None

    @property
    def state_label(self) -> str:
        if self.paused:
            return "PAUSA"
        if not self.started:
            return "¡Empieza a pedalear!"
        if self.auto_paused:
            return "AUTO-PAUSA"
        if self.workout and self.finished:
            return "¡WORKOUT COMPLETADO!"
        return "EN MARCHA"

    # ----------------------------------------------------------------- control
    def _notify(self, text: str) -> None:
        self.notice = Notice(text, self.now + NOTICE_SECONDS)

    def handle_key(self, key: str) -> None:
        k = key.lower() if len(key) == 1 else key
        if k == "q" or k == "ESC":
            if self.now <= self._quit_armed_until or (self.workout and self.finished) \
                    or not self.started:
                self.quit = True
            else:
                self._quit_armed_until = self.now + QUIT_CONFIRM_SECONDS
                self._notify("Presiona q otra vez para terminar y guardar")
            return
        if k == " " or k == "p":
            self.paused = not self.paused
            self._notify("Pausa" if self.paused else "Reanudando")
        elif k in ("n", "RIGHT"):
            self.skip(1)
        elif k in ("b", "LEFT"):
            self.skip(-1)
        elif k in ("+", "=", "UP"):
            self.adjust(+1)
        elif k in ("-", "_", "DOWN"):
            self.adjust(-1)
        elif k == "e" and self.workout:
            self.erg_enabled = not self.erg_enabled
            self._notify("ERG activado" if self.erg_enabled
                         else "ERG desactivado — modo pendiente (usa +/-)")
        self._apply_control(force=True)

    def adjust(self, direction: int) -> None:
        if self.erg_active:
            self.bias = round(max(0.5, min(2.0, self.bias + direction * BIAS_STEP)), 2)
            self._notify(f"Intensidad {self.bias * 100:.0f}%")
        else:
            self.grade = max(-10.0, min(25.0, self.grade + direction * GRADE_STEP))
            self._notify(f"Pendiente {self.grade:+.1f}%")

    def skip(self, direction: int) -> None:
        if not self.workout or self.finished:
            return
        idx = self.segment_index
        if idx is None:
            return
        if direction > 0:
            nxt = idx + 1
            self.workout_time = (self.workout.segment_start(nxt)
                                 if nxt < len(self.workout.segments)
                                 else self.workout.total_duration)
            self._notify("Saltando intervalo ⏭")
        else:
            if self.segment_offset > 5 or idx == 0:
                self.workout_time = self.workout.segment_start(idx)
            else:
                self.workout_time = self.workout.segment_start(idx - 1)
            self._notify("Volviendo ⏮")
        if self.workout_time >= self.workout.total_duration:
            self._finish()

    def _finish(self) -> None:
        self.finished = True
        self.erg_enabled = False
        self.grade = 0.0
        self._notify("¡Workout completado! Sigue rodando libre o presiona q para guardar")

    def _apply_control(self, force: bool = False) -> None:
        target = self.target_watts
        if self.erg_active:
            cmd = ("erg", int(round(target)))
        else:
            cmd = ("sim", int(round(self.grade * 10)))
        if cmd == self._last_cmd:
            return
        idx = self.segment_index
        seg_changed = idx != self._last_seg_idx
        mode_changed = self._last_cmd is None or cmd[0] != self._last_cmd[0]
        # rate limit ramps to 1 command per second; mode/segment changes go out immediately
        if not (force or seg_changed or mode_changed or self.now - self._last_cmd_time >= 1.0):
            return
        if cmd[0] == "erg":
            self.trainer.set_erg(cmd[1])
        else:
            self.trainer.set_sim(cmd[1] / 10.0)
        self._last_cmd = cmd
        self._last_cmd_time = self.now
        self._last_seg_idx = idx

    def update(self, now: float) -> None:
        """Advance the session to time `now` (seconds, monotonic)."""
        self.now = now
        dt = 0.0 if self._last_now is None else max(0.0, now - self._last_now)
        self._last_now = now

        d = self.trainer.data
        pedaling = (d.power_w or 0) > 0 or (d.cadence_rpm or 0) > 0
        if pedaling:
            self._last_pedal = now
            if not self.started:
                self.started = True
                self._notify("¡Vamos!")
        self.auto_paused = (self.auto_pause and self.started and not self.paused
                            and now - self._last_pedal > AUTO_PAUSE_AFTER)

        if self.running:
            self.ride_time += dt
            if self.workout and not self.finished:
                self.workout_time += dt
                if self.workout_time >= self.workout.total_duration:
                    self.workout_time = self.workout.total_duration
                    self._finish()
            self._sample_acc += dt
            while self._sample_acc >= 1.0:
                self._sample_acc -= 1.0
                self.recorder.add(
                    elapsed=self.ride_time - self._sample_acc,
                    power=d.power_w,
                    cadence=d.cadence_rpm,
                    speed_kmh=d.speed_kmh,
                    heart_rate=self.heart_rate,
                    target=self.target_watts if self.erg_active else None,
                )

        self._apply_control()

    def progress_text(self) -> str:
        if not self.workout:
            return format_duration(self.ride_time)
        return (f"{format_duration(self.workout_time)} / "
                f"{format_duration(self.workout.total_duration)}")
