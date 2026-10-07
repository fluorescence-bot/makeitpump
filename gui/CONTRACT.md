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

## Visual style
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
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.json
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
```

## Pump flow
Pump channels are set by **mL only**. The pump runs every channel at its own rate (the `DilutionController` uses 90 % of the pump's max flow), so there is no speed setting. A channel is idle when `ml = 0`; negative mL is refused (not clamped) by `run_program`, by `manual_dose` (which needs mL > 0), and by the UI before it calls them. Pause blocks are a length of time and keep `s` in seconds.

Where a duration is needed (stopped-block estimate, mock, animations, block and program times) it is the estimate `seconds = ml / FLOW_ML_PER_S`. `FLOW_ML_PER_S` is a placeholder (1 mL/s) until the pump is calibrated, and is defined twice: `hardware.FLOW_ML_PER_S` (read at call time) and `flow.mlPerS` in `src/flow.ts`. The two must match.

Presets saved with a per-channel `s` (seconds) or `speed` load with those fields dropped.

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
DEBUGGING = True          # True: the `ismatec` library's mock pump; False: the real pump
PUMP_ADDRESS = "/dev/ttyACM0"
FLOW_ML_PER_S = 1.0       # estimated flow per channel, placeholder until calibrated (estimates only)

def run_step(volumes: dict[str, float]) -> None: ...   # mL per channel A-D; blocks until done or stopped
def stop_all() -> None: ...                            # cancel the step and stop every channel; doesn't wait
```
Set up like `reglo_gui/reglo_gui.py`: the `ismatec` Pump and `DilutionController` (`dilution_controller.py`, a copy of `reglo_gui/`'s) live on an asyncio loop in a background thread, created and `setup()` on the first step. Channels A/B/C/D are pump channels 1/2/3/4. The mock pump runs one channel at a time and can't be interrupted, so with `DEBUGGING` a stopped channel still finishes in the background.

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
