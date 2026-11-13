"""Rich-based terminal dashboard."""

from __future__ import annotations

from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from .metrics import zone_for
from .session import Session
from .workout import Workout, describe_segment, format_duration

# 3x3 "big digit" font made of half blocks
_FONT = {
    "0": ["█▀█", "█ █", "▀▀▀"],
    "1": ["▀█ ", " █ ", "▀▀▀"],
    "2": ["▀▀█", "█▀▀", "▀▀▀"],
    "3": ["▀▀█", " ▀█", "▀▀▀"],
    "4": ["█ █", "▀▀█", "  ▀"],
    "5": ["█▀▀", "▀▀█", "▀▀▀"],
    "6": ["█▀▀", "█▀█", "▀▀▀"],
    "7": ["▀▀█", "  █", "  ▀"],
    "8": ["█▀█", "█▀█", "▀▀▀"],
    "9": ["█▀█", "▀▀█", "▀▀▀"],
    "-": ["   ", "▀▀▀", "   "],
    ":": [" ", "▀", "▀"],
    " ": ["  ", "  ", "  "],
}


def big(text: str, style: str = "bold") -> Text:
    rows = ["", "", ""]
    for ch in text:
        glyph = _FONT.get(ch, _FONT[" "])
        for i in range(3):
            rows[i] += glyph[i] + " "
    return Text("\n".join(r.rstrip() for r in rows), style=style, justify="center")


def workout_profile(workout: Workout, ftp: float, width: int, height: int,
                    position: float | None = None, bias: float = 1.0) -> Text:
    """Bar chart of the workout coloured by zone; columns before `position` are dimmed."""
    width = max(10, width)
    total = workout.total_duration or 1
    fracs: list[float | None] = []
    for i in range(width):
        t = (i + 0.5) / width * total
        f = workout.fraction_at(t, ftp)
        fracs.append(None if f is None else f * bias)
    top = max([1.25] + [f * 1.05 for f in fracs if f is not None])
    blocks = " ▁▂▃▄▅▆▇█"
    cursor = None if position is None else min(width - 1, int(position / total * width))

    text = Text(no_wrap=True, overflow="crop")
    for row in range(height):
        row_base = (height - 1 - row) * 8
        for i, f in enumerate(fracs):
            level = (0.5 if f is None else f) / top * height * 8
            fill = int(max(0, min(8, round(level - row_base))))
            ch = blocks[fill]
            _, color = zone_for(f)
            if f is None and fill:
                ch = "░"
            style = color
            if cursor is not None and i < cursor:
                style = f"{color} dim"
            if i == cursor:
                style = "bold white" if fill else "white"
                ch = ch if fill else "│"
            text.append(ch, style=style)
        if row < height - 1 or cursor is not None:
            text.append("\n")
    if cursor is not None:
        text.append(" " * cursor + "▲", style="bold white")
    return text


def _metric(label: str, value: str, unit: str, style: str = "bold white",
            sub: str = "") -> Panel:
    body = Group(
        Text(label, style="grey62", justify="center"),
        big(value, style),
        Text(unit + (f"  {sub}" if sub else ""), style="grey62", justify="center"),
    )
    return Panel(body, padding=(0, 1), border_style="grey35")


def render(session: Session, width: int, height: int, trainer_name: str) -> RenderableType:
    s = session
    d = s.trainer.data
    power = d.power_w or 0
    cadence = int(d.cadence_rpm or 0)
    hr = s.heart_rate
    speed = d.speed_kmh or 0.0
    target = s.target_watts if s.erg_active else None

    # ---- header
    title = s.workout.name if s.workout else "Rodada libre"
    head = Table.grid(expand=True)
    head.add_column(ratio=2)
    head.add_column(justify="center", ratio=1)
    head.add_column(justify="right", ratio=2)
    state = s.state_label
    state_style = {"EN MARCHA": "bold green", "PAUSA": "bold yellow",
                   "AUTO-PAUSA": "bold yellow"}.get(state, "bold cyan")
    head.add_row(Text(title, style="bold"), Text(state, style=state_style),
                 Text(f"⏱ {s.progress_text()}   🏁 {format_duration(s.ride_time)}",
                      style="bold"))

    # ---- metrics row
    pz = zone_for(power / s.ftp if s.ftp else 0)[1] if power else "white"
    if target is not None:
        diff = power - target
        sub = f"{diff:+.0f} W"
        p_style = f"bold {pz}"
    else:
        sub = f"{power / s.ftp * 100:.0f}% FTP" if s.ftp and power else ""
        p_style = f"bold {pz}"
    metrics = Table.grid(expand=True)
    for _ in range(5):
        metrics.add_column(ratio=1)
    tgt_txt = f"{target:.0f}" if target is not None else "--"
    tgt_label = "OBJETIVO" if target is not None else (f"PENDIENTE {s.grade:+.1f}%")
    seg = s.segment
    cad_sub = f"obj {seg.cadence}" if seg and seg.cadence else ""
    metrics.add_row(
        _metric("POTENCIA", f"{power}", "W", p_style, sub),
        _metric(tgt_label, tgt_txt, "W" if target is not None else "",
                f"bold {zone_for(target / s.ftp)[1]}" if target else "grey50",
                f"×{s.bias:.2f}" if target is not None and s.bias != 1 else ""),
        _metric("CADENCIA", f"{cadence}", "rpm", "bold white", cad_sub),
        _metric("FREC. CARD.", f"{hr}" if hr else "--", "bpm", "bold red1"),
        _metric("VELOCIDAD", f"{speed:.0f}", "km/h", "bold white",
                f"{(s.recorder.samples[-1].distance_m / 1000 if s.recorder.samples else 0):.2f} km"),
    )

    parts: list[RenderableType] = [head, metrics]

    # ---- interval info
    if s.workout and seg is not None:
        idx = s.segment_index
        rem = s.segment_remaining
        kind = {"warmup": "Calentamiento", "cooldown": "Vuelta a la calma", "on": "Intervalo",
                "off": "Recuperación", "ramp": "Rampa", "steady": "Bloque", "free": "Libre",
                "max": "Máximo esfuerzo"}.get(seg.kind, seg.kind)
        label = f"{kind} {seg.label}".strip()
        info = Table.grid(expand=True)
        info.add_column(ratio=3)
        info.add_column(justify="center", ratio=2)
        info.add_column(justify="right", ratio=3)
        _, zc = zone_for(seg.fraction_at(s.segment_offset, s.ftp))
        nxt = s.next_segment
        nxt_txt = (f"Siguiente: {describe_segment(nxt, s.ftp)} · {format_duration(nxt.duration)}"
                   if nxt else "Siguiente: fin 🎉")
        info.add_row(
            Text(f"[{idx + 1}/{len(s.workout.segments)}] {label} — "
                 f"{describe_segment(seg, s.ftp)}", style=f"bold {zc}"),
            big(format_duration(rem), f"bold {zc}"),
            Text(nxt_txt, style="grey70"),
        )
        bar = ProgressBar(total=seg.duration or 1, completed=s.segment_offset,
                          complete_style=zc, finished_style=zc, width=None)
        parts += [Panel(Group(info, bar), border_style=zc, padding=(0, 1))]

    msg = s.current_message
    parts.append(Align.center(Text(msg or " ", style="bold black on bright_yellow" if msg else "")))

    # ---- profile chart
    if s.workout:
        chart_h = max(3, min(10, height - 26))
        # outer panel (border+padding = 4) + inner panel (border+padding = 4)
        chart = workout_profile(s.workout, s.ftp, width - 9, chart_h,
                                s.workout_time, s.bias)
        parts.append(Panel(chart, title="Perfil", title_align="left",
                           border_style="grey35", padding=(0, 1)))

    # ---- status / keys
    mode = ("[bold green]ERG[/]" if s.erg_active else
            f"[bold cyan]SIM {s.grade:+.1f}%[/]")
    conn = ("[green]●[/]" if s.trainer.connected else "[red]●[/]")
    hrm = ""
    if s.hrm is not None:
        hrm = f"   FC: {'[green]●[/]' if s.hrm.connected else '[red]●[/]'} {s.hrm.name}"
    status = Text.from_markup(
        f"{conn} {trainer_name} ({s.trainer.status})   Modo: {mode}   FTP {s.ftp:.0f} W{hrm}")
    keys = Text.from_markup(
        "[bold]espacio[/] pausa  [bold]←/b →/n[/] intervalo  "
        "[bold]↑/+ ↓/-[/] intensidad/pendiente  [bold]e[/] ERG on/off  [bold]q[/] terminar",
        style="grey62")
    parts += [status, keys]
    return Panel(Group(*parts), border_style="bright_black", padding=(0, 1),
                 title="[bold]smart-trainer[/]", title_align="left")


def summary_table(summary: dict, ftp: float) -> Table:
    t = Table(title="Resumen de la rodada", show_header=False, box=None, padding=(0, 2))
    t.add_column(style="grey62")
    t.add_column(style="bold")
    t.add_row("Duración", format_duration(summary["duration"]))
    t.add_row("Distancia", f"{summary['distance_km']:.2f} km")
    t.add_row("Potencia media", f"{summary['avg_power']:.0f} W")
    t.add_row("Potencia normalizada", f"{summary['np']:.0f} W")
    t.add_row("Potencia máx.", f"{summary['max_power']:.0f} W")
    t.add_row("IF / TSS", f"{summary['if']:.2f} / {summary['tss']:.0f}  (FTP {ftp:.0f})")
    t.add_row("Trabajo", f"{summary['kj']:.0f} kJ")
    if summary.get("avg_cadence"):
        t.add_row("Cadencia media", f"{summary['avg_cadence']:.0f} rpm")
    if summary.get("avg_hr"):
        t.add_row("FC media / máx.", f"{summary['avg_hr']:.0f} / {summary['max_hr']} bpm")
    return t
