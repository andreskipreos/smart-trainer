"""Command line interface: `trainer <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from importlib import resources
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table

from . import __version__, ftms
from .config import Config
from .workout import SUPPORTED_EXTENSIONS, Workout, describe_segment, format_duration, load_workout

console = Console()


# ----------------------------------------------------------------- workout library


def library_dirs(cfg: Config) -> list[Path]:
    dirs = [cfg.workouts_path]
    bundled = Path(str(resources.files("smart_trainer") / "workouts"))
    if bundled.is_dir():
        dirs.append(bundled)
    return dirs


def list_library(cfg: Config) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for d in library_dirs(cfg):
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file():
                if p.name.lower() not in seen:
                    seen.add(p.name.lower())
                    files.append(p)
    return files


def resolve_workout(ref: str, cfg: Config) -> Path:
    p = Path(ref).expanduser()
    if p.is_file():
        return p
    lib = list_library(cfg)
    if ref.isdigit() and 1 <= int(ref) <= len(lib):
        return lib[int(ref) - 1]
    def norm(x: str) -> str:
        return " ".join(x.lower().replace("-", " ").replace("_", " ").split())

    def names(f: Path) -> list[str]:
        out = [norm(f.stem)]
        try:
            out.append(norm(load_workout(f).name))
        except Exception:
            pass
        return out

    needle = norm(ref)
    indexed = [(f, names(f)) for f in lib]
    exact = [f for f, ns in indexed if needle in ns]
    if exact:
        return exact[0]
    partial = [f for f, ns in indexed if any(needle in n for n in ns)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(f.stem for f in partial[:8])
        raise SystemExit(f"'{ref}' es ambiguo: {names}")
    raise SystemExit(f"No encontré el workout '{ref}'. Usa `trainer workouts` para ver la lista.")


# ------------------------------------------------------------------------ commands


def cmd_scan(args, cfg: Config) -> int:
    from .devices import scan

    with console.status(f"Buscando dispositivos Bluetooth ({args.timeout:.0f}s)…"):
        try:
            devices = asyncio.run(scan(args.timeout))
        except Exception as exc:
            console.print(f"[red]Error de Bluetooth:[/] {exc}")
            return 1
    if not args.all:
        devices = [d for d in devices if d.is_trainer or d.is_hrm or d.name]
    t = Table(title="Dispositivos encontrados")
    t.add_column("Nombre")
    t.add_column("Dirección")
    t.add_column("RSSI", justify="right")
    t.add_column("Tipo")
    for d in devices:
        kind = []
        if d.is_trainer:
            kind.append("[green]Rodillo FTMS[/]")
        if ftms.CYCLING_POWER_SERVICE in d.services:
            kind.append("Potencia")
        if d.is_hrm:
            kind.append("[red]Frec. cardíaca[/]")
        t.add_row(d.name or "?", d.address, str(d.rssi or ""), ", ".join(kind))
    console.print(t)
    trainers = [d for d in devices if d.is_trainer]
    if trainers:
        console.print(f"\nPara guardar tu rodillo: [bold]trainer config --trainer "
                      f"{trainers[0].address}[/]")
    else:
        console.print("\n[yellow]No vi rodillos FTMS.[/] Asegúrate de que el Neo esté encendido "
                      "(pedalea un poco para despertarlo) y que ninguna otra app esté conectada.")
    return 0


def cmd_info(args, cfg: Config) -> int:
    from .devices import FtmsTrainer

    async def run():
        tr = FtmsTrainer(address=args.trainer or cfg.trainer_address,
                         name_filter=None if (args.trainer or cfg.trainer_address)
                         else (cfg.trainer_name or None))
        await tr.connect()
        try:
            await asyncio.sleep(2)
            return tr
        finally:
            await tr.disconnect()

    with console.status("Conectando al rodillo…"):
        try:
            tr = asyncio.run(run())
        except Exception as exc:
            console.print(f"[red]{exc}[/]")
            return 1
    console.print(f"[bold green]Conectado a {tr.name}[/] ({tr.address})")
    if tr.features:
        console.print("Datos:", ", ".join(tr.features[0]))
        console.print("Control:", ", ".join(tr.features[1]))
    if tr.power_range:
        lo, hi, inc = tr.power_range
        console.print(f"Rango de potencia ERG: {lo}–{hi} W (paso {inc} W)")
    d = tr.data
    console.print(f"Lectura: {d.power_w} W, {d.cadence_rpm} rpm, {d.speed_kmh} km/h")
    return 0


def cmd_config(args, cfg: Config) -> int:
    changed = False
    for attr, val in (("ftp", args.ftp), ("weight_kg", args.weight),
                      ("trainer_address", args.trainer), ("trainer_name", args.trainer_name),
                      ("hrm_address", args.hrm), ("workouts_dir", args.workouts_dir),
                      ("rides_dir", args.rides_dir)):
        if val is not None:
            setattr(cfg, attr, val)
            changed = True
    if args.auto_pause is not None:
        cfg.auto_pause = args.auto_pause == "on"
        changed = True
    if changed:
        path = cfg.save()
        console.print(f"[green]Guardado en {path}[/]")
    t = Table(show_header=False, box=None)
    t.add_column(style="grey62")
    t.add_column(style="bold")
    t.add_row("FTP", f"{cfg.ftp} W")
    t.add_row("Peso", f"{cfg.weight_kg} kg")
    t.add_row("Rodillo", cfg.trainer_address or cfg.trainer_name or "(auto: primer rodillo FTMS)")
    t.add_row("Banda FC", cfg.hrm_address or "(ninguna)")
    t.add_row("Workouts", str(cfg.workouts_path))
    t.add_row("Rodadas", str(cfg.rides_path))
    t.add_row("Auto-pausa", "sí" if cfg.auto_pause else "no")
    console.print(t)
    return 0


def cmd_workouts(args, cfg: Config) -> int:
    files = list_library(cfg)
    if not files:
        console.print(f"No hay workouts. Copia archivos .zwo/.erg/.mrc/.txt a {cfg.workouts_path}")
        return 0
    ftp = args.ftp or cfg.ftp
    t = Table(title=f"Workouts (FTP {ftp} W)")
    t.add_column("#", justify="right")
    t.add_column("Nombre")
    t.add_column("Duración", justify="right")
    t.add_column("IF", justify="right")
    t.add_column("TSS", justify="right")
    t.add_column("Archivo", style="grey50")
    for i, f in enumerate(files, 1):
        try:
            w = load_workout(f)
            st = w.stats(ftp)
            t.add_row(str(i), w.name, format_duration(w.total_duration),
                      f"{st['if']:.2f}", f"{st['tss']:.0f}", f.name)
        except Exception as exc:
            t.add_row(str(i), f"[red]error: {exc}[/]", "", "", "", f.name)
    console.print(t)
    console.print("\nCorre uno con: [bold]trainer ride <número|nombre|archivo>[/]")
    return 0


def print_workout(w: Workout, ftp: float) -> None:
    from .ui import workout_profile

    st = w.stats(ftp)
    console.rule(f"[bold]{w.name}")
    if w.author:
        console.print(f"[grey62]Autor:[/] {w.author}")
    if w.description:
        console.print(w.description, style="grey70")
    console.print(f"Duración [bold]{format_duration(w.total_duration)}[/]   "
                  f"IF [bold]{st['if']:.2f}[/]   TSS [bold]{st['tss']:.0f}[/]   "
                  f"Potencia media [bold]{st['avg_watts']:.0f} W[/]   "
                  f"Trabajo [bold]{st['kj']:.0f} kJ[/]   (FTP {ftp:.0f} W)")
    console.print(workout_profile(w, ftp, min(console.width, 120) - 2, 8))
    t = Table(box=None, padding=(0, 2))
    t.add_column("#", justify="right", style="grey50")
    t.add_column("Inicio", justify="right", style="grey62")
    t.add_column("Duración", justify="right")
    t.add_column("Objetivo")
    t.add_column("Cadencia", justify="right")
    t.add_column("Mensajes", style="grey62")
    for i, seg in enumerate(w.segments):
        t.add_row(str(i + 1), format_duration(w.segment_start(i)),
                  format_duration(seg.duration), describe_segment(seg, ftp),
                  str(seg.cadence or ""), " | ".join(m for _, m in seg.messages)[:60])
    console.print(t)


def cmd_show(args, cfg: Config) -> int:
    path = resolve_workout(args.workout, cfg)
    try:
        w = load_workout(path)
    except Exception as exc:
        console.print(f"[red]No pude leer {path}: {exc}[/]")
        return 1
    print_workout(w, args.ftp or cfg.ftp)
    return 0


def cmd_import(args, cfg: Config) -> int:
    dest = cfg.workouts_path
    dest.mkdir(parents=True, exist_ok=True)
    for f in args.files:
        p = Path(f).expanduser()
        try:
            w = load_workout(p)
        except Exception as exc:
            console.print(f"[red]✗ {p.name}: {exc}[/]")
            continue
        shutil.copy2(p, dest / p.name)
        console.print(f"[green]✓[/] {w.name} ({format_duration(w.total_duration)}) → {dest / p.name}")
    return 0


def cmd_ride(args, cfg: Config) -> int:
    workout = None
    if getattr(args, "workout", None):
        path = resolve_workout(args.workout, cfg)
        workout = load_workout(path)
    ftp = args.ftp or cfg.ftp
    try:
        return asyncio.run(_ride(args, cfg, workout, ftp))
    except KeyboardInterrupt:
        return 130


async def _ride(args, cfg: Config, workout: Workout | None, ftp: float) -> int:
    from .devices import Clock, FtmsTrainer, HeartRateMonitor, SimulatedTrainer
    from .keyboard import KeyReader
    from .recorder import Recorder
    from .session import Session
    from .ui import render, summary_table

    clock = Clock(args.time_scale if args.sim else 1.0)
    if args.sim:
        trainer = SimulatedTrainer(clock, ftp=ftp, rider_kg=cfg.weight_kg)
    else:
        addr = args.trainer or cfg.trainer_address
        trainer = FtmsTrainer(address=addr, name_filter=None if addr else cfg.trainer_name)

    hrm = None
    hrm_addr = args.hrm or cfg.hrm_address
    try:
        with console.status("Conectando al rodillo…"):
            await trainer.connect()
        if hrm_addr and not args.sim:
            hrm = HeartRateMonitor(address=hrm_addr)
            try:
                with console.status("Conectando banda de FC…"):
                    await hrm.connect()
            except Exception as exc:
                console.print(f"[yellow]Sin banda de FC: {exc}[/]")
                hrm = None
    except Exception as exc:
        console.print(f"[red]{exc}[/]")
        return 1

    if not args.sim and isinstance(trainer, FtmsTrainer) and trainer.address \
            and trainer.address != cfg.trainer_address:
        cfg.trainer_address = trainer.address
        cfg.save()

    recorder = Recorder()
    session = Session(trainer, ftp, workout=workout, hrm=hrm, recorder=recorder,
                      bias=args.bias / 100.0, auto_pause=cfg.auto_pause and not args.no_auto_pause,
                      start_grade=args.grade)
    session.update(clock.now())
    name = trainer.name
    try:
        with KeyReader() as keys, Live(console=console, screen=True, auto_refresh=False,
                                       transient=True) as live:
            while not session.quit:
                for k in keys.drain():
                    session.handle_key(k)
                session.update(clock.now())
                w, h = console.size
                live.update(render(session, w, h, name), refresh=True)
                if args.sim and args.auto_quit and session.finished:
                    break
                await asyncio.sleep(0.25)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await trainer.disconnect()
        if hrm:
            await hrm.disconnect()

    summary = recorder.summary(ftp)
    if not summary:
        console.print("No se registraron datos.")
        return 0
    console.print(summary_table(summary, ftp))
    title = workout.name if workout else "Rodada libre"
    saved = recorder.save(cfg.rides_path, title)
    if saved:
        console.print(f"\n[green]Guardado:[/] {saved[0]}  (súbelo a Strava / Garmin Connect / "
                      f"TrainingPeaks / intervals.icu)\n         {saved[1]}")
    return 0


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trainer",
        description="Trainer de rodillo para la terminal (Tacx Neo 2T y rodillos FTMS).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="logs de depuración (stderr)")
    sub = p.add_subparsers(dest="cmd", metavar="<comando>")

    s = sub.add_parser("scan", help="buscar rodillos y bandas de FC por Bluetooth")
    s.add_argument("-t", "--timeout", type=float, default=8.0)
    s.add_argument("-a", "--all", action="store_true", help="mostrar también dispositivos sin nombre")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("info", help="conectar al rodillo y mostrar sus capacidades")
    s.add_argument("--trainer", help="dirección BLE del rodillo")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("config", help="ver / cambiar configuración (FTP, rodillo, …)")
    s.add_argument("--ftp", type=int)
    s.add_argument("--weight", type=float, help="peso en kg")
    s.add_argument("--trainer", help="dirección BLE del rodillo (de `trainer scan`)")
    s.add_argument("--trainer-name", help="parte del nombre del rodillo, p.ej. 'Neo'")
    s.add_argument("--hrm", help="dirección BLE de la banda de FC")
    s.add_argument("--workouts-dir")
    s.add_argument("--rides-dir")
    s.add_argument("--auto-pause", choices=["on", "off"])
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("workouts", aliases=["ls"], help="listar la biblioteca de workouts")
    s.add_argument("--ftp", type=int)
    s.set_defaults(func=cmd_workouts)

    s = sub.add_parser("show", help="ver el detalle y perfil de un workout")
    s.add_argument("workout", help="número, nombre o archivo")
    s.add_argument("--ftp", type=int)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("import", help="copiar workouts (.zwo/.erg/.mrc/.txt) a la biblioteca")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_import)

    def ride_opts(sp, with_workout: bool):
        if with_workout:
            sp.add_argument("workout", help="número, nombre o archivo del workout")
        sp.add_argument("--ftp", type=int, help="FTP para esta sesión")
        sp.add_argument("--bias", type=float, default=100, help="intensidad inicial en %% (def. 100)")
        sp.add_argument("--grade", type=float, default=0.0, help="pendiente inicial en modo SIM")
        sp.add_argument("--trainer", help="dirección BLE del rodillo")
        sp.add_argument("--hrm", help="dirección BLE de la banda de FC")
        sp.add_argument("--sim", action="store_true", help="usar un rodillo simulado (sin hardware)")
        sp.add_argument("--time-scale", type=float, default=1.0,
                        help="acelerar el tiempo en modo --sim (demo)")
        sp.add_argument("--auto-quit", action="store_true", help=argparse.SUPPRESS)
        sp.add_argument("--no-auto-pause", action="store_true", help="desactivar auto-pausa")
        sp.set_defaults(func=cmd_ride)

    ride_opts(sub.add_parser("ride", help="correr un workout en ERG"), True)
    ride_opts(sub.add_parser("free", help="rodada libre en modo pendiente (SIM)"), False)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfg = Config.load()
    return args.func(args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
