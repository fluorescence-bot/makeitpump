# RainMixer GUI: interface contract

Every part of the GUI must follow this contract exactly. Paths are relative to `gui/`.

```
gui/
  CONTRACT.md
  requirements.txt      # pywebview
  app.py                # starts the pywebview window
  api.py                # Api class exposed to JS (js_api) + program runner thread
  hardware.py           # STUBS the backend team replaces (print + nothing else)
  presets/example.yaml
  web/                  # Vite + TypeScript (vanilla, no framework)
    package.json, tsconfig.json, vite.config.ts (base: './')
    index.html
    src/main.ts         # app wiring, layout, preset UI, controls, status
    src/types.ts        # shared types (below), copy verbatim
    src/bridge.ts       # talks to window.pywebview.api, falls back to an in-browser mock
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
// mL over its own seconds (s = 0 means that channel is idle). The block lasts max(s).
export interface ChannelPump { ml: number; s: number; }
export interface PumpBlock { id: string; type: "pump"; pumps: Record<Channel, ChannelPump>; }
export interface PauseBlock { id: string; type: "pause"; s: number; }
export type Block = PumpBlock | PauseBlock;

export interface Preset {
  name: string;
  solutions: Record<Channel, string>;   // free-text names, e.g. {A:"Milk",B:"Water",C:"NaOH",D:"Final mix"}
  blocks: Block[];
  repeat: number;                        // >= 1
}

export type RunStatus = "ready" | "raining" | "paused" | "dosing" | "done" | "stopped" | "error";
export type PumpSource = "program" | "manual";

// Events pushed from backend -> UI
export type BackendEvent =
  | { type: "status"; status: RunStatus; message?: string }
  | { type: "block_start"; index: number; repeat: number; block: Block }   // repeat is 0-based
  | { type: "block_end"; index: number; repeat: number }
  | { type: "progress"; fraction: number }                                  // 0..1 of whole run
  | { type: "dose_start"; channel: Channel; ml: number; s: number }         // a manual click started
  | { type: "pumped"; channel: Channel; ml: number; source: PumpSource };    // mL actually delivered (pro-rata if stopped)

export interface BackendApi {
  run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }>;
  pause_run(): Promise<{ ok: boolean }>;
  resume_run(): Promise<{ ok: boolean }>;
  stop(): Promise<{ ok: boolean }>;
  manual_dose(channel: Channel, ml: number, s: number): Promise<{ ok: boolean; error?: string }>;
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.yaml
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
```

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
- A pump block starts every channel with `s > 0` simultaneously via `hardware.start_pump(channel, ml, s)`, stops each channel when its own `s` has elapsed, and ends after `max(s)`. It checks stop/pause every 50 ms.
- A pause block waits `s` seconds, checking stop/pause the same way.
- Pause: stop the pump, set status `paused`, wait. Resume: set status `raining` and restart the pump with the remaining ml/s pro-rata (re-emitting `block_start` with the remaining block values is fine).
- Stop: `hardware.stop_all()`, status `stopped`.
- Every pump block (program or manual) ends by emitting one `pumped` event per channel with `s > 0` and delivered mL > 0. Delivered = `ml * min(elapsed, s) / s`, where `elapsed` excludes paused time, so a stopped block reports only what was actually pumped. `source` is `program` or `manual`.
- `manual_dose(channel, ml, s)`: one manual click. Refused (`ok: false`) while anything else is running. Otherwise runs a single-channel pump block on the runner thread: status `dosing`, `dose_start`, the block (which emits `pumped` with source `manual`), then status `ready`. Stop works as for a program.

## hardware.py (stubs for the backend team)
```python
def start_pump(channel: str, ml: float, s: float) -> None: ...   # non-blocking start
def stop_pump(channel: str) -> None: ...
def stop_all() -> None: ...
```
Each stub just prints what it would do.

## scene.ts exports
```ts
import type { Channel, SourceChannel } from "./types";
export interface Scene {
  setNames(names: Record<Channel, string>): void;
  onNameChange(cb: (ch: Channel, name: string) => void): void;       // user edited a label in the scene
  startRain(ch: SourceChannel, ml: number, s: number): void;         // cloud rains for s sec, bowl gains ml gradually
  startPour(ml: number, s: number): void;                            // bowl drains ml over s sec into final cup
  setPaused(paused: boolean): void;                                  // freeze/unfreeze running animations
  stopAll(): void;                                                   // end all animations now (volumes stay)
  onDose(cb: (ch: Channel, ml: number) => void): void;               // click on a cloud (A/B/C) or the bowl (D); ml = that channel's mL-per-click field
  setTotals(totals: Record<Channel, number>): void;                  // session mL shown above each cloud and the funnel
  resetBowl(): void;
  getBowl(): { A: number; B: number; C: number; total: number };
}
export function createScene(container: HTMLElement): Scene;
```
Bowl liquid colour = volume-weighted blend of A/B/C colours. The level is proportional to total volume (full at 50 mL, capped visually). On `block_start` for a pump block, the UI calls `startRain(ch, ml, s)` for each of A/B/C with s > 0 and `startPour(ml, s)` for D with s > 0. On `dose_start` it does the same for the one dosed channel.

## Session log
The UI keeps a per-session log of every `pumped` event and shows running totals per channel A–D (manual and program) above each cloud and the funnel, so the user can work backwards from a result (e.g. a target pH) to how much of each liquid it took. The log can be reset and copied as CSV.
