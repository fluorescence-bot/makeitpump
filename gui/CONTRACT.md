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
    src/flow.ts         # speed % -> seconds (MAX flow, DEFAULT_SPEED) for animations, times and the mock
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
// mL at its own speed, 0-100 % of the pump's max flow (ml = 0 or speed = 0 means that
// channel is idle). The block lasts as long as its slowest channel.
export interface ChannelPump { ml: number; speed: number; }
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
  | { type: "dose_start"; channel: Channel; ml: number; speed: number }     // a manual click started
  | { type: "pumped"; channel: Channel; ml: number; source: PumpSource }     // mL actually delivered (pro-rata if stopped)
  | { type: "rotator"; state: RotatorState; deg: number | null; target?: number; message?: string }; // deg = current angle, null if unknown

export interface BackendApi {
  run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }>;
  pause_run(): Promise<{ ok: boolean }>;
  resume_run(): Promise<{ ok: boolean }>;
  stop(): Promise<{ ok: boolean }>;
  manual_dose(channel: Channel, ml: number, speed: number): Promise<{ ok: boolean; error?: string }>;
  rotator_connect(): Promise<{ ok: boolean; error?: string }>;              // find the board, open it, HOME (in the background)
  manual_rotate(deg: number): Promise<{ ok: boolean; error?: string }>;     // one manual rotate, like manual_dose
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.json
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
```

## Pump speed
Every pump channel runs at a **speed from 0 to 100 %** of the pump's max flow; there are no flow units in the UI. New channels and manual doses start at `DEFAULT_SPEED` (80). A channel is idle when `ml = 0` or `speed = 0`; `ml > 0` with `speed = 0`, negative mL, or a speed outside 0–100 is refused (not clamped) by `run_program`, by `manual_dose`, and by the UI before it calls them. Pause blocks are a length of time and keep `s` in seconds.

Where a duration is needed (runner, mock, animations, block and program times) it is `seconds = ml / (MAX_FLOW_ML_PER_S * speed / 100)`. `MAX_FLOW_ML_PER_S` is a placeholder (1 mL/s) until the pump is calibrated, and is defined twice: `hardware.MAX_FLOW_ML_PER_S` (read at call time) and `flow.maxMlPerS` in `src/flow.ts`. The two must match.

Presets saved before speed existed have `s` per channel; loading them drops `s` and sets `speed` to the default (converting `ml / s` would give speeds far over 100 %).

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
- A pump block starts every active channel simultaneously via `hardware.start_pump(channel, ml, speed)`, stops each channel when its own time has elapsed, and ends when the slowest channel is done. It checks stop/pause every 50 ms.
- A pause block waits `s` seconds, checking stop/pause the same way.
- Pause: stop the pump, set status `paused`, wait. Resume: set status `raining` and restart the pump with the remaining mL at the same speed (re-emitting `block_start` with the remaining block values is fine).
- Stop: `hardware.stop_all()`, status `stopped`.
- Every pump block (program or manual) ends by emitting one `pumped` event per active channel with delivered mL > 0. Delivered = `ml * min(elapsed, s) / s`, where `s` is that channel's time and `elapsed` excludes paused time, so a stopped block reports only what was actually pumped. `source` is `program` or `manual`.
- `manual_dose(channel, ml, speed)`: one manual click. Refused (`ok: false`) while anything else is running. Otherwise runs a single-channel pump block on the runner thread: status `dosing`, `dose_start`, the block (which emits `pumped` with source `manual`), then status `ready`. Stop works as for a program.

## Cuvette rotator
A robot turns the cuvette (the "final solution" mug in the scene) to an absolute angle, **whole degrees 0–60**. It is an Adafruit Trinket M0 on USB serial running `firmware/cuvette_rotator/code.py` (protocol in its README). `rotator.py` finds it by USB VID `0x239a` / PID `0x801f` (falling back to product name `Trinket M0`), sends `HOME` on connect, and sends moves as plain integers.

- Rotator state is pushed as `rotator` events: `disconnected` → `homing` → `ready`, and `moving` (with `target`) during a move. `deg` is the current angle (`null` until homed); `message` explains a failure.
- `rotator_connect()`: find, open and home the board on a background thread; ignored while already connecting. The UI calls it at startup and when the robot is clicked while offline.
- A rotate block, or `manual_rotate(deg)`, is refused (`ok: false`) for an angle that isn't a whole number 0–60, or when the rotator isn't `ready` (`"Cuvette rotator not connected"` / `"Cuvette rotator is busy"`). `run_program` also refuses unknown block types.
- Running a rotate block: emit `rotator` `moving`, send the angle, wait for the board's `MOVE_COMPLETE`, emit `rotator` `ready`. **A move can't be paused**: Pause takes effect at the next block. Stop stops waiting at once, but the board still finishes the move it's on (its firmware can't be interrupted).
- `manual_rotate(deg)`: like `manual_dose`, on the runner thread: status `rotating`, then **re-home first** (`rotator` `homing`, `HOME` to the 0° switch) so every click starts from a grounded zero, then the move, then status `ready`. Rotate blocks in a program don't re-home. The UI disables Pause while `rotating`.
- The mock fakes a rotator that connects and homes in 1.5 s, then moves at `rotate.sPerDeg` (`flow.ts`, measured ≈ 0.03 s/°).

## hardware.py (stubs for the backend team)
```python
MAX_FLOW_ML_PER_S = 1.0   # flow at speed 100 %, placeholder until calibrated

def start_pump(channel: str, ml: float, speed: float) -> None: ...   # non-blocking start, speed 0-100 %
def stop_pump(channel: str) -> None: ...
def stop_all() -> None: ...
```
Each stub just prints what it would do.

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
  onDose(cb: (ch: Channel, ml: number, speed: number) => void): void; // click on a cloud (A/B/C) or the bowl (D); ml and speed = that channel's mL-per-click and speed % fields
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
Bowl liquid colour = volume-weighted blend of A/B/C colours. The level is proportional to total volume (full at 50 mL, capped visually). On `block_start` for a pump block, the UI calls `startRain(ch, ml, s)` for each active A/B/C channel and `startPour(ml, s)` for D if active, with `s` from `pumpSeconds(ml, speed)`. On `dose_start` it does the same for the one dosed channel.

## Session log
The UI keeps a per-session log of every `pumped` event and shows running totals per channel A–D (manual and program) above each cloud and the funnel, so the user can work backwards from a result (e.g. a target pH) to how much of each liquid it took. The log can be reset and copied as CSV.
