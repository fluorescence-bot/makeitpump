# RainMixer GUI: interface contract

Every part of the GUI must follow this contract exactly. Paths are relative to `gui/`.

```
gui/
  CONTRACT.md
  requirements.txt      # pywebview
  app.py                # starts the pywebview window
  api.py                # Api class exposed to JS (js_api) + program runner thread
  hardware.py           # STUBS the backend team replaces (print + nothing else)
  rotator.py            # real USB-serial driver for the cuvette rotator (Trinket M0)
  presets/example.yaml
  web/                  # Vite + TypeScript (vanilla, no framework)
    package.json, tsconfig.json, vite.config.ts (base: './')
    index.html
    src/main.ts         # app wiring, layout, preset UI, controls, status
    src/types.ts        # shared types (below), copy verbatim
    src/bridge.ts       # talks to window.pywebview.api, falls back to an in-browser mock
    src/flow.ts         # mL -> estimated seconds (flow.mlPerS) for animations, times and the mock
    src/blocks.ts       # program block editor (SortableJS)
    src/scene.ts        # SVG cartoon scene (clouds A/B/C, bowl D, final cup)
    src/scene.css
    src/styles.css      # global pastel tokens + layout
```

## Writing another frontend

RainMixer's backend can drive multiple frontends: the main rain-themed GUI above, or a simpler interface (e.g. a "matrix" controller). The backend is stable; any frontend that implements this contract will work.

### Connecting to the backend

`app.py` creates a pywebview window pointing to a URL (either `http://localhost:5173` in `--dev` mode, or a `web/dist/` folder for production). The window has an injected JavaScript object `window.pywebview.api`, which exposes the Python `Api` class methods as async functions.

Reaching the backend:
1. Wait for the `pywebviewready` window event, or up to ~800 ms for it to fire.
2. Check `window.pywebview` and `window.pywebview.api` — if present, use it; if not, you are in a browser (dev without the Python backend) and should provide a fallback mock.
3. Call methods as `window.pywebview.api.<name>(...args)` — each returns a `Promise`.
4. Arguments are JSON; Python function names use snake_case (e.g. `run_program`, `manual_dose`, `pump_connect`).
5. All function calls are stateless on the frontend side: the backend manages the runner thread and pump state.

### Events from the backend

Before the app connects, define `window.__onBackendEvent = (event) => {...}` at the global scope. The backend will call this with JSON objects matching `BackendEvent` (see types.ts below). Events are pushed, not polled: when something changes in the runner or hardware, you receive an event.

**Event types:**
- `{ type: "status"; status: RunStatus; message?: string }` — program/runner state changed (raining, paused, dosing, rotating, done, stopped, error, ready).
- `{ type: "block_start"; index: number; repeat: number; block: Block }` — about to run block `index` in repetition `repeat` (0-based).
- `{ type: "block_end"; index: number; repeat: number }` — finished that block.
- `{ type: "progress"; fraction: number }` — overall progress through the run, 0..1.
- `{ type: "dose_start"; channel: Channel; ml: number }` — manual dose started (from a click or call).
- `{ type: "pumped"; channel: Channel; ml: number; source: PumpSource }` — channel delivered `ml` (full or pro-rata if stopped); `source` is "program" or "manual".
- `{ type: "rotator"; state: RotatorState; deg: number | null; target?: number; message?: string }` — rotator state changed (disconnected, homing, ready, moving, error), current angle, or target angle during a move.

### Conventions

**Result shapes:** Most calls return `{ ok: boolean; error?: string; ...fields }`. If `ok` is false, check `error` for the reason. Some calls return more fields (e.g. `PumpInfo`, `TubingInfo`).

**Units:**
- Volume: **millilitres (mL)**, always positive (0 is idle, negative is rejected).
- Time: **seconds**, floating point (e.g. pause blocks).
- Rotator angle: **whole degrees, 0–60** (rejected if not in range).
- Flow rate: **mL/min**.
- Tubing diameter: **millimetres, rounded to 0.01 mm** (e.g. 1.52).

**Async vs sync calls:**
- **Calls that return immediately (run in background):** `run_program`, `manual_dose`, `manual_rotate`, `rotator_connect`. Check status events to know when they finish.
- **Calls that block until done:** `pump_connect`, `get_tubing`, `set_tubing`. Wait for the Promise.
- **All other calls:** `pause_run`, `resume_run`, `stop` (return immediately with a status flag).

**Busy refusals:** Only one thing runs at a time (program, manual dose or manual rotate). While one runs, `manual_dose`, `manual_rotate`, `pump_connect` and `set_tubing` return `{ ok: false, error: "Busy" }`, and `run_program` returns `{ ok: false, error: "A program is already running" }`. Treat any `ok: false` as "refused, nothing happened" and show `error`. Wait for the run to finish (status `ready`, `done`, `stopped` or `error`) before trying again.

### Minimal startup sequence

```
1. pump_connect()       → PumpInfo (detects real pump, else uses mock)
2. Check mode field     → Show "mock pump" badge if mode === "mock"
3. get_tubing()         → TubingInfo (diameter and calibrated max flow)
4. rotator_connect()    → Returns immediately; listen for rotator events to track state
```

After this, the UI is ready: show status `ready`, load/run presets, and respond to user input.

### Event stream example

A simple program with one pump block (A=1 mL, D=1 mL, no repeat) produces this event sequence. Assume `FLOW_ML_PER_S = 1.0`, so 1 mL takes ~1 second per channel.

```
{ type: "status", status: "raining" }
{ type: "block_start", index: 0, repeat: 0, block: { id: "...", type: "pump", pumps: { A: {ml: 1}, B: {ml: 0}, C: {ml: 0}, D: {ml: 1} } } }
  (pump block runs, ~1 second for the slowest channel)
{ type: "pumped", channel: "A", ml: 1, source: "program" }
{ type: "pumped", channel: "D", ml: 1, source: "program" }
{ type: "block_end", index: 0, repeat: 0 }
{ type: "progress", fraction: 1 }
{ type: "status", status: "done" }
```

If the user clicks Stop during the pump block, `stop()` emits `stopped` at once, then the block reports what it estimates was delivered (and no `block_end`/`progress`/`done` follow):
```
(pump block is running)
{ type: "status", status: "stopped" }
{ type: "pumped", channel: "A", ml: 0.5, source: "program" }    // (estimated pro-rata)
{ type: "pumped", channel: "D", ml: 0.5, source: "program" }
```

### Preset file format

Presets are YAML files in `gui/presets/`. `list_presets` / `load_preset` read every `*.yaml` / `*.yml` there and match on the `name` field (not the file name); `save_preset` writes `<name, sanitised>.yaml`. `import_preset_file` / `export_preset_file` use native file dialogs for YAML anywhere on disk. A rotate block is `{ id, type: rotate, deg: 30 }`.

**YAML structure:**
```yaml
name: Example
solutions:
  A: Milk           # free-text labels for the channels
  B: Water
  C: NaOH
  D: Final mix
blocks:
  - id: b1
    type: pump
    pumps:
      A:
        ml: 5
      B:
        ml: 3
      C:
        ml: 0
      D:
        ml: 0
  - id: b2
    type: pause
    s: 5              # 5 seconds
  - id: b3
    type: pump
    pumps:
      A:
        ml: 0
      B:
        ml: 0
      C:
        ml: 1
      D:
        ml: 9         # out to the cuvette
repeat: 1             # run all blocks once
```

Loading an old preset with per-channel `s` (seconds) or `speed` fields drops those.

### Minimal vs full frontend

A minimal frontend must:
- Call `pump_connect()` and show pump status (real/mock badge).
- Listen to `status` events and show current state (raining, paused, dosing, done, etc.).
- Provide a way to run a program (e.g. preset picker, block editor, or manual command entry).
- Respond to `pause_run()` and `resume_run()` buttons when status is `raining`.
- Handle `Busy` errors gracefully (disable buttons, show a message).

A minimal frontend can skip:
- The cartoon scene, animations, and visual flourishes.
- Tubing diameter selection (or hardcode it).
- Cuvette rotator support (skip rotate blocks in presets).
- Session logging and totals.
- Preset file dialogs (only use list/load/save in memory, or skip presets entirely).
- The drag-and-drop block editor (use a fixed program or preset list).

## Visual style (RainMixer GUI)
Cute cartoon / anime / childlike, pastel. Rounded font **M PLUS Rounded 1c** via npm `@fontsource/m-plus-rounded-1c` (weights 400/700/800), bundled for offline use. Soft coloured outlines (not black), big rounded corners, gentle bobbing and bouncy hover.

CSS tokens, defined on `:root` in `styles.css`. `scene.css` may use them.
```
--col-a:#A8E8F0; --col-a-ink:#4FB3C6;   /* cyan candyfloss */
--col-b:#FFB8D9; --col-b-ink:#E07AAE;   /* pink candyfloss */
--col-c:#FFE7A0; --col-c-ink:#D9B34A;   /* butter yellow  */
--col-d-empty:#EDE7FA; --col-d-ink:#9A7FD6; /* bowl (actual liquid colour is blended live) */
--bg-top:#F4F0FF; --bg-bottom:#FFF6FB; --panel:#FFFFFF; --ink:#5B4B7A; --muted:#A99BC4;
--ok:#8EDFB4; --danger:#FF9AA8; --radius:22px; --font:"M PLUS Rounded 1c", system-ui, sans-serif;
```

## src/types.ts (verbatim)
```ts
export type Channel = "A" | "B" | "C" | "D";
export type SourceChannel = "A" | "B" | "C";

// A pump block runs all four channels at the same time. Each channel pumps its own
// mL (ml = 0 means that channel is idle) at the pump's own rate. The block lasts
// until the pump reports every channel done.
export interface ChannelPump { ml: number; }
export interface PumpBlock { id: string; type: "pump"; pumps: Record<Channel, ChannelPump>; }
export interface PauseBlock { id: string; type: "pause"; s: number; }
// Turns the cuvette robot to an absolute angle: whole degrees, 0-60.
export interface RotateBlock { id: string; type: "rotate"; deg: number; }
export type Block = PumpBlock | PauseBlock | RotateBlock;

export interface Preset {
  name: string;
  solutions: Record<Channel, string>;   // free-text names, e.g. {A:"Milk",B:"Water",C:"NaOH",D:"Final mix"}
  blocks: Block[];
  repeat: number;                        // >= 1
}

export type RunStatus = "ready" | "raining" | "paused" | "dosing" | "rotating" | "done" | "stopped" | "error";
export type RotatorState = "disconnected" | "homing" | "ready" | "moving" | "error";
export type PumpSource = "program" | "manual";

// Which pump the backend drives. "real" = the Ismatec pump found at `address`;
// "mock" = no pump was found, so the ismatec library's mock pump stands in.
export type PumpMode = "real" | "mock";
export interface PumpInfo {
  ok: boolean;
  error?: string;
  mode: PumpMode;
  address: string | null;     // serial port of the real pump, null for the mock
  message: string;            // for the UI, e.g. "No pump found on /dev/ttyACM0 - using the mock pump"
  logging: boolean;           // ismatec debug logging on/off
}

// Tubing inner diameter set on the pump. It sets the pump's max flow (and so how
// fast a dose runs), so it must match the tube that is fitted.
export interface TubingInfo {
  ok: boolean;
  error?: string;
  mm: number | null;                                   // same on every channel, else null (also when unknown)
  min_mm: number;                                      // range the pump accepts; any value in it,
  max_mm: number;                                      //   rounded to 0.01 mm
  max_ml_per_min: Partial<Record<Channel, number>>;    // calibrated max flow per channel; empty when unknown
}

// Events pushed from backend -> UI
export type BackendEvent =
  | { type: "status"; status: RunStatus; message?: string }
  | { type: "block_start"; index: number; repeat: number; block: Block }   // repeat is 0-based
  | { type: "block_end"; index: number; repeat: number }
  | { type: "progress"; fraction: number }                                  // 0..1 of whole run
  | { type: "dose_start"; channel: Channel; ml: number }                    // a manual click started
  | { type: "pumped"; channel: Channel; ml: number; source: PumpSource }     // mL actually delivered (pro-rata if stopped)
  | { type: "rotator"; state: RotatorState; deg: number | null; target?: number; message?: string }; // deg = current angle, null if unknown

export interface BackendApi {
  run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }>;
  pause_run(): Promise<{ ok: boolean }>;
  resume_run(): Promise<{ ok: boolean }>;
  stop(): Promise<{ ok: boolean }>;
  manual_dose(channel: Channel, ml: number): Promise<{ ok: boolean; error?: string }>;
  rotator_connect(): Promise<{ ok: boolean; error?: string }>;              // find the board, open it, HOME (in the background)
  manual_rotate(deg: number): Promise<{ ok: boolean; error?: string }>;     // one manual rotate, like manual_dose
  pump_connect(): Promise<PumpInfo>;                                         // (re)detect: real pump, else the mock; refused while busy
  set_pump_logging(on: boolean): Promise<PumpInfo>;                          // ismatec debug logging
  get_tubing(): Promise<TubingInfo>;
  set_tubing(mm: number): Promise<TubingInfo>;                              // every channel; refused while busy
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.yaml
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
```

## Pump flow
Pump channels are set by **mL only**. The pump runs every channel at its own rate (the `DilutionController` uses 90 % of the pump's max flow), so there is no speed setting. A channel is idle when `ml = 0`; negative mL is refused (not clamped) by `run_program`, by `manual_dose` (which needs mL > 0), and by the UI before it calls them. Pause blocks are a length of time and keep `s` in seconds.

Where a duration is needed (stopped-block estimate, mock, animations, block and program times) it is the estimate `seconds = ml / FLOW_ML_PER_S`. `FLOW_ML_PER_S` is a placeholder (1 mL/s) until the pump is calibrated, and is defined twice: `hardware.FLOW_ML_PER_S` (read at call time) and `flow.mlPerS` in `src/flow.ts`. The two must match.

Presets saved with a per-channel `s` (seconds) or `speed` load with those fields dropped.

### Pump detection and logging
There is no `DEBUGGING` switch. Which pump is used is detected, and debug logging is a separate runtime toggle.
- `pump_connect()`: (re)detects the pump, on the calling thread, and answers with `PumpInfo`. It tries the real pump at `hardware.PUMP_ADDRESS`; if there is none (port missing, or no answer within `PUMP_TIMEOUT_S` = 4 s; the controller `setup()` then gets `PUMP_SETUP_TIMEOUT_S` = 15 s, as the real pump takes ~4 s for it) it uses the `ismatec` mock pump and says so in `message` ("No pump found on <address> - using the mock pump"). It drops any pump it had before (stopping it first) and runs the controller `setup()` again. Refused (`ok: false`, `"Busy"`, current `PumpInfo`) while anything runs. Never hangs: a pump that doesn't answer counts as not found.
- The UI calls `pump_connect()` at startup, then `get_tubing()`. A **🔌 Redetect pump** button calls them again (for a pump plugged in after launch); it is disabled while running or paused. While `mode` is `mock` the UI shows `message` in a visible "mock pump" badge.
- A pump step (`run_step`), `get_tubing` or `set_tubing` before any `pump_connect()` detects the pump first, the same way.
- `set_pump_logging(on)`: turns `ismatec` debug logging (`logging.getLogger('ismatec')` at DEBUG, else WARNING; logs go to the terminal) on or off at runtime and answers with the current `PumpInfo`. Allowed while running. A **🐞 Log** toggle button in the UI shows and flips it. Default off, unless the app is started with `--pump-log`.
- `hardware.py` exposes `connect() -> dict` (the `PumpInfo` fields except `ok`/`error`), `info() -> dict` (same, without reconnecting), and `set_logging(on: bool)`. `hardware.FORCE_MOCK = True` skips the real pump (tests set it, so the suite never drives a real pump).
- The browser mock (`bridge.ts`) always answers `mode: "mock"` with message "Demo mode - mock pump", and keeps the logging flag in memory.

### Tubing
The pump's max flow comes from the tubing inner diameter stored on the pump (`DilutionController` doses at 90 % of the calibrated max), so a wrong diameter makes every dose far too slow or fast. The UI shows a **Tube ⌀** number input (mm, step 0.01, free entry) next to Repeat × with the resulting max flow ("max X mL/min", per channel in its tooltip). It applies on change (blur/Enter), not on every keystroke; an invalid entry is refused and the field goes back to the pump's value.
- `get_tubing()`: reads the diameter and calibrated max flow of channels A–D. `mm` is `null` if the channels differ. If the pump can't be reached: `ok: false`, `error`, `mm: null`, `max_ml_per_min: {}`.
- `set_tubing(mm)`: any diameter from `min_mm` to `max_mm` (`hardware.TUBING_MIN_MM` = 0.13, `TUBING_MAX_MM` = 3.17, the ends of `ismatec.util.Tubing`), rounded to 0.01 mm. Sets every channel, then answers like `get_tubing()`. Refused (`ok: false`, with the current tubing) outside that range / not a number (`"Tubing must be 0.13-3.17 mm"`), or while anything runs (`"Busy"`); the UI disables the input while running or paused.
- `hardware.set_tubing` sends `<ch>+<hundredths of a mm, 4 digits>` itself (e.g. 2.10 mm -> `1+0210`) instead of the library's `set_tubing_inner_diameter`, whose `pack_discrete2` drops trailing zeros (2.1 -> `0021` = 0.21 mm). If the pump refuses a channel (`command` returns False), it raises.
- `hardware.get_tubing()` / `hardware.set_tubing(mm)` return `{"mm": {ch: mm}, "max_ml_per_min": {ch: flow}}`. The mock keeps one diameter (1.52 mm) with a made-up max flow.
- `FLOW_ML_PER_S` does **not** follow the tubing; it stays a fixed estimate.

## Event delivery
Python calls `window.__onBackendEvent(<json object>)` via `window.evaluate_js(...)`.
`bridge.ts` installs `window.__onBackendEvent` and lets the app subscribe with `onEvent(cb)`.

## bridge.ts exports
```ts
export async function getApi(): Promise<BackendApi>;  // waits for 'pywebviewready' (max 800 ms), else returns mock
export function onEvent(cb: (e: BackendEvent) => void): void;
export function isMock(): boolean;
```
The mock mirrors the Python runner using timers, emits the same events, and keeps presets in localStorage (wrapped in try/catch). File import/export uses `<input type=file>` and a Blob download.

## Runner semantics (Python and mock must match)
- `run_program`: rejects if something is already running. Sets status `raining`. For r in 0..repeat-1 and each block i, it emits `block_start`, runs it, then emits `block_end` and `progress`. When finished it sets status `done`.
- A pump block hands the mL of every active channel to `hardware.run_step`, which runs Andreas' `DilutionController` and blocks until the pump reports every channel done. The mock waits for the slowest channel's estimated time (`pumpSeconds`).
- A pause block waits `s` seconds, checking stop/pause every 50 ms.
- **A pump block can't be paused** (the pump has no pause, and stopping a channel loses its count). Pause takes effect before the next block: set status `paused`, wait; Resume sets the active status again and runs the next block. A pause during a pause block takes effect at once, and that block resumes with its remaining time. Pausing and resuming before the block ends doesn't pause anything; pausing during the last block lets the run finish with `done`.
- Stop: `hardware.stop_all()` (cancels the step and stops every channel), status `stopped`.
- Every pump block (program or manual) ends by emitting one `pumped` event per active channel with delivered mL > 0. A finished block reports the full `ml`. A stopped block reports an estimate, `ml * min(elapsed, s) / s`, where `s` is that channel's estimated time and `elapsed` the time the block ran. `source` is `program` or `manual`.
- `manual_dose(channel, ml)`: one manual click. Refused (`ok: false`) while anything else is running. Otherwise runs a single-channel pump block on the runner thread: status `dosing`, `dose_start`, the block (which emits `pumped` with source `manual`), then status `ready`. Stop works as for a program.

## Cuvette rotator
A robot turns the cuvette (the "final solution" mug in the scene) to an absolute angle, **whole degrees 0–60**. It is an Adafruit Trinket M0 on USB serial running `firmware/cuvette_rotator/code.py` (protocol in its README). `rotator.py` finds it by USB VID `0x239a` / PID `0x801f` (falling back to product name `Trinket M0`), sends `HOME` on connect, and sends moves as plain integers.

- Rotator state is pushed as `rotator` events: `disconnected` → `homing` → `ready`, and `moving` (with `target`) during a move. `deg` is the current angle (`null` until homed); `message` explains a failure.
- `rotator_connect()`: find, open and home the board on a background thread; ignored while already connecting. The UI calls it at startup and when the robot is clicked while offline.
- A rotate block, or `manual_rotate(deg)`, is refused (`ok: false`) for an angle that isn't a whole number 0–60, or when the rotator isn't `ready` (`"Cuvette rotator not connected"` / `"Cuvette rotator is busy"`). `run_program` also refuses unknown block types.
- Running a rotate block: emit `rotator` `moving`, send the angle, wait for the board's `MOVE_COMPLETE`, emit `rotator` `ready`. **A move can't be paused**: Pause takes effect at the next block. Stop stops waiting at once, but the board still finishes the move it's on (its firmware can't be interrupted).
- `manual_rotate(deg)`: like `manual_dose`, on the runner thread: status `rotating`, then **re-home first** (`rotator` `homing`, `HOME` to the 0° switch) so every click starts from a grounded zero, then the move, then status `ready`. Rotate blocks in a program don't re-home. The UI disables Pause while `rotating`.
- The mock fakes a rotator that connects and homes in 1.5 s, then moves at `rotate.sPerDeg` (`flow.ts`, measured ≈ 0.03 s/°).

## hardware.py (pump control)
```python
FORCE_MOCK = False        # True: always the `ismatec` mock pump (tests); False: detect the real pump, else the mock
PUMP_ADDRESS = "/dev/ttyACM0"
FLOW_ML_PER_S = 1.0       # estimated flow per channel, placeholder until calibrated (estimates only)

def run_step(volumes: dict[str, float]) -> None: ...   # mL per channel A-D; blocks until done or stopped
def stop_all() -> None: ...                            # cancel the step and stop every channel; doesn't wait
def get_tubing() -> dict: ...                          # {"mm": {ch: mm}, "max_ml_per_min": {ch: flow}} for A-D
def set_tubing(mm: float) -> dict: ...                 # every channel; mm from TUBING_MM; returns get_tubing()
```
Set up like `reglo_gui/reglo_gui.py`: the `ismatec` Pump and `DilutionController` (`dilution_controller.py`, a copy of `reglo_gui/`'s) live on an asyncio loop in a background thread, created and `setup()` on the first step. Channels A/B/C/D are pump channels 1/2/3/4. The mock pump runs one channel at a time and can't be interrupted, so with the mock a stopped channel still finishes in the background.

## scene.ts exports
```ts
import type { Channel, RotatorState, SourceChannel } from "./types";
export interface Scene {
  setNames(names: Record<Channel, string>): void;
  onNameChange(cb: (ch: Channel, name: string) => void): void;       // user edited a label in the scene
  startRain(ch: SourceChannel, ml: number, s: number): void;         // cloud rains for s sec, bowl gains ml gradually
  startPour(ml: number, s: number): void;                            // bowl drains ml over s sec into final cup
  setPaused(paused: boolean): void;                                  // freeze/unfreeze running animations
  stopAll(): void;                                                   // end all animations now (volumes stay)
  onDose(cb: (ch: Channel, ml: number) => void): void;                // click on a cloud (A/B/C) or the bowl (D); ml = that channel's mL-per-click field
  setTotals(totals: Record<Channel, number>): void;                  // session mL shown above each cloud and the funnel
  resetBowl(): void;
  getBowl(): { A: number; B: number; C: number; total: number };
  setRotator(state: RotatorState, deg: number | null, target?: number, message?: string): void; // mug tilts to the angle
  onRotate(cb: (deg: number) => void): void;          // click on the robot while ready; deg = its "rotate to" field
  onRotatorConnect(cb: () => void): void;             // click on the robot while disconnected / in error
}
export function createScene(container: HTMLElement): Scene;
```
The robot (🦾🤖) stands right of the final-solution mug, with the current angle under it and a "rotate to °" field beside it. It is greyed out while the rotator is disconnected, and its arm cranks while it homes or moves. The mug tilts towards the rotator's angle at `rotate.sPerDeg`, in the same animation loop as the rain.
Bowl liquid colour = volume-weighted blend of A/B/C colours. The level is proportional to total volume (full at 50 mL, capped visually). On `block_start` for a pump block, the UI calls `startRain(ch, ml, s)` for each active A/B/C channel and `startPour(ml, s)` for D if active, with `s` from `pumpSeconds(ml)`. On `dose_start` it does the same for the one dosed channel.

## Session log
The UI keeps a per-session log of every `pumped` event and shows running totals per channel A–D (manual and program) above each cloud and the funnel, so the user can work backwards from a result (e.g. a target pH) to how much of each liquid it took. The log can be reset and copied as CSV.
