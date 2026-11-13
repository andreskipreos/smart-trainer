# smart-trainer 🚴

Un trainer de rodillo **estilo Zwift para la terminal**. Se conecta por Bluetooth a tu
**Tacx Neo 2T** (o cualquier rodillo con el protocolo estándar **FTMS**: Wahoo KICKR,
Elite, Saris, etc.), lo controla en **modo ERG** para correr entrenamientos estructurados
y en **modo pendiente (SIM)** para rodar libre, y guarda la rodada en **TCX** para que la
subas a Strava / Garmin Connect / TrainingPeaks / intervals.icu.

```
╭─ smart-trainer ───────────────────────────────────────────────────────────────────────╮
│ Sweet Spot 3x10                         EN MARCHA              ⏱ 21:36 / 1:05:00      │
│ ╭──────────────╮╭──────────────╮╭──────────────╮╭──────────────╮╭──────────────╮      │
│ │   POTENCIA   ││   OBJETIVO   ││   CADENCIA   ││  FREC. CARD. ││  VELOCIDAD   │      │
│ │  ▀▀█ ▀▀█ ▀█  ││  ▀▀█ ▀▀█ █▀▀ ││   █▀█ █▀▀    ││  ▀█  █▀▀ ▀█  ││   ▀▀█ █▀▀    │      │
│ │  █▀▀  ▀█  █  ││  █▀▀ █▀▀ ▀▀█ ││   ▀▀█ ▀▀█    ││   █  █▀█  █  ││    ▀█ █▀█    │      │
│ │ ▀▀▀ ▀▀▀ ▀▀▀  ││  ▀▀▀ ▀▀▀ ▀▀▀ ││   ▀▀▀ ▀▀▀    ││  ▀▀▀ ▀▀▀ ▀▀▀ ││   ▀▀▀ ▀▀▀    │      │
│ │   W  +6 W    ││      W       ││  rpm obj 90  ││     bpm      ││ km/h 11.8 km │      │
│ ╰──────────────╯╰──────────────╯╰──────────────╯╰──────────────╯╰──────────────╯      │
│ ╭ [4/10] Intervalo 1/3 — 90% (225 W)       ▀█  ▀▀█ ▀▀█     Siguiente: 55% · 5:00 ╮    │
│ │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╸━━━━━━━━━━━━━━ │    │
│ ╰─────────────────────────────────────────────────────────────────────────────────╯    │
│ ╭─ Perfil ────────────────────────────────────────────────────────────────────────╮    │
│ │              ▅   ▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂        ▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂        ▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂    │    │
│ │       ▁▂▃▄▅▇██   ███████████████        ███████████████        ███████████████    │    │
│ │  ▁▂▄▅▆▇███████▃▃▃███████████████▃▃▃▃▃▃▃▃███████████████▃▃▃▃▃▃▃▃███████████████▆▄▂ │    │
│ │                              ▲                                                   │    │
│ ╰─────────────────────────────────────────────────────────────────────────────────╯    │
│ ● Tacx Neo 2T (conectado)   Modo: ERG   FTP 250 W                                       │
│ espacio pausa  ←/b →/n intervalo  ↑/+ ↓/- intensidad/pendiente  e ERG on/off  q terminar│
╰─────────────────────────────────────────────────────────────────────────────────────────╯
```

(en la terminal real todo va a color, por zonas de potencia)

## Instalación

Requiere Python 3.10+ y una computadora con Bluetooth LE.

```bash
git clone <este repo> && cd smart-trainer
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
trainer --help
```

(o `pipx install .` para tener el comando `trainer` disponible globalmente)

**Bluetooth según sistema:**

- **Linux:** usa BlueZ. Verifica que `bluetoothctl show` muestre tu adaptador y que esté
  encendido (`bluetoothctl power on`). Tu usuario debe poder usar D-Bus/Bluetooth
  (normalmente ya puede; si no, agrégalo al grupo `bluetooth`).
- **macOS:** la primera vez, macOS pedirá permiso de Bluetooth para tu app de terminal
  (Terminal / iTerm). Acéptalo en *Ajustes → Privacidad y seguridad → Bluetooth*.
  En macOS las "direcciones" son UUIDs, no MACs; funciona igual.
- **Windows 10/11:** funciona con el Bluetooth integrado.

## Primeros pasos

```bash
# 1. Configura tu FTP (y peso, opcional)
trainer config --ftp 250 --weight 72

# 2. Despierta el Neo 2T (pedalea un poco) y búscalo
trainer scan

# 3. Guarda la dirección de tu rodillo (opcional: si no, usa el primer rodillo FTMS que vea)
trainer config --trainer XX:XX:XX:XX:XX:XX

# 4. Comprueba la conexión y las capacidades del rodillo
trainer info

# 5. ¡A rodar!
trainer workouts            # lista la biblioteca
trainer show "sweet spot"   # perfil y detalle de un workout
trainer ride "sweet spot"   # correrlo en ERG
trainer free --grade 2      # rodada libre en modo pendiente
```

> ⚠️ **Importante:** un rodillo Bluetooth sólo acepta **un** controlador a la vez. Cierra
> Zwift, la app Tacx Training / Garmin, TrainerRoad, etc. antes de conectar. Tu Garmin de
> manillar puede seguir grabando por ANT+ sin problema.

### ¿Sin el rodillo a mano? Prueba el simulador

```bash
trainer ride vo2 --sim                    # rodillo simulado en tiempo real
trainer ride vo2 --sim --time-scale 30    # 30× más rápido, para ver cómo funciona
```

## Durante la rodada

| Tecla | Acción |
|---|---|
| `espacio` / `p` | pausa / reanudar (en pausa el rodillo queda en llano, suave) |
| `→` / `n` | saltar al siguiente intervalo |
| `←` / `b` | volver al inicio del intervalo (o al anterior si estás en los primeros 5 s) |
| `↑` / `+` | en ERG: +5 % de intensidad · en modo pendiente: +0.5 % de pendiente |
| `↓` / `-` | en ERG: −5 % de intensidad · en modo pendiente: −0.5 % de pendiente |
| `e` | activar / desactivar ERG (sin ERG pedaleas libre con pendiente, viendo el objetivo) |
| `q` `q` | terminar y guardar (pide confirmación) |

Además:

- El workout **arranca cuando empiezas a pedalear** y se **auto-pausa** si dejas de pedalear
  más de 3 s (desactívalo con `--no-auto-pause` o `trainer config --auto-pause off`).
- Los bloques `FreeRide` del workout cambian el rodillo a modo pendiente automáticamente.
- Al terminar el workout sigues en rodada libre hasta que presiones `q`.
- `--bias 95` arranca el workout al 95 % de intensidad; `--ftp 240` cambia el FTP sólo
  para esa sesión.
- Banda de frecuencia cardíaca Bluetooth: `trainer scan` para verla y
  `trainer config --hrm <dirección>` (o `--hrm` en `ride`).

Al final verás el resumen (potencia media/normalizada, IF, TSS, kJ, FC, cadencia) y la
rodada se guarda en `~/smart-trainer/rides/` como **.tcx** (con potencia, cadencia, FC,
velocidad y distancia) y **.csv**.

## Entrenamientos

Pon tus archivos en `~/smart-trainer/workouts/` (o usa `trainer import archivo.zwo`).
Puedes referirte a un workout por **número** (de `trainer workouts`), por **nombre**
(o parte del nombre) o por **ruta** de archivo.

Formatos soportados:

| Formato | Descripción |
|---|---|
| `.zwo` | Formato de workouts de Zwift. Lo exportan intervals.icu, TrainingPeaks, whatsonzwift, etc. Soporta `Warmup`, `Cooldown`, `Ramp`, `SteadyState`, `IntervalsT`, `FreeRide`, `MaxEffort`, cadencias y mensajes de texto. |
| `.erg` | Vatios absolutos por minuto (TrainerRoad / Golden Cheetah). |
| `.mrc` | Porcentaje de FTP por minuto. |
| `.txt` / `.wkt` | Formato de texto simple propio, ideal para escribir workouts a mano (ver abajo). |

### Formato de texto

Una línea por paso: `<duración> <potencia> [@cadencia] ["mensaje"]`

```text
# comentario
name: Mi workout de umbral
description: 2x20 al umbral

10m 45-75% "Calentamiento progresivo"     # rampa del 45 al 75 % FTP
5m 55%
2x 20m 95% @90 "¡Al umbral!", 5m 55%      # repeticiones: Nx paso, paso, ...
3x(30s 150%, 30s 50%)                     # también con paréntesis
5m 200w                                   # vatios absolutos
10m free                                  # rodada libre (modo pendiente)
8m 60-40%
```

- Duraciones: `30s`, `10m`, `1h`, `1m30s`, `1:30`, o un número solo (= minutos).
- Potencia: `75%` del FTP, `0.75ftp`, `200w`, rampas `50-75%` / `150-250w`, o `free`.

Workouts incluidos: *Sweet Spot 3x10*, *VO2 Max 5x3*, *Over-Unders 3x9*, *Endurance 60*,
*Ramp Test FTP* y un ejemplo `.erg`.

## Cómo funciona

- `smart_trainer/ftms.py` — protocolo Bluetooth FTMS (Fitness Machine Service): lectura de
  *Indoor Bike Data* (potencia, cadencia, velocidad) y comandos al *Control Point*:
  `Request Control`, `Start`, `Set Target Power` (ERG) y `Set Indoor Bike Simulation`
  (pendiente).
- `smart_trainer/devices.py` — conexión BLE con [bleak](https://github.com/hbldh/bleak),
  reconexión automática, banda de FC y un rodillo simulado. Los comandos se envían en
  segundo plano ("el último gana"), así la interfaz nunca se traba esperando al Bluetooth.
- `smart_trainer/session.py` — el motor del workout: reloj, intervalos, rampas (objetivo
  actualizado cada segundo), auto-pausa, teclas y grabación. No tiene código de interfaz,
  por eso está testeado.
- `smart_trainer/ui.py` — dashboard con [rich](https://github.com/Textualize/rich).
- `smart_trainer/workout.py` — modelo de workout y lectores de `.zwo`/`.erg`/`.mrc`/`.txt`.
- `smart_trainer/recorder.py` — exporta TCX y CSV.

Tests: `pip install -e '.[dev]' && pytest`

## Solución de problemas

- **No aparece el rodillo en `trainer scan`:** pedalea para despertarlo; asegúrate de que no
  esté conectado a otra app/dispositivo por Bluetooth (el Neo 2T sólo acepta una conexión
  BLE de control). Desconecta el adaptador de la app Tacx en el teléfono.
- **"El rodillo no cedió el control":** otra app tiene el control. Ciérrala o apaga el
  Bluetooth del teléfono.
- **La potencia no sigue el objetivo en ERG:** es normal unos segundos en los cambios de
  intervalo. A cadencia muy baja el ERG se "atasca" (espiral de la muerte): pedalea más
  rápido o pausa con `espacio`.
- **Logs de depuración:** `trainer -v ride ... 2> debug.log`.

## Limitaciones / ideas

- Sin mundo virtual 🙂: es un trainer de workouts, no un simulador de rutas.
- El modo pendiente usa los parámetros de viento/rodadura por defecto de FTMS; el Neo
  calcula la resistencia con su propio modelo.
- Ideas futuras: exportar FIT, subir directo a Strava/intervals.icu, rutas GPX con
  pendiente real, soporte para FE-C sobre BLE en rodillos sin FTMS.
