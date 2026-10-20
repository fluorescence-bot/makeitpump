# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RainMixer: a pywebview desktop app that controls a robotic liquid mixer. Python backend
(`app.py`, `api.py`, `hardware.py`) drives a vanilla TypeScript/Vite frontend (`web/`) through
pywebview's JS bridge. Cute cartoon/pastel visual style. Student project; `hardware.py` drives the Ismatec
pump through Andreas' `ismatec` library and `DilutionController` (see "Pump" below).

**Read `CONTRACT.md` before touching backend/frontend communication, types, or runner semantics.**
It is the source of truth for the `Api`/`BackendApi` interface, event shapes, pump-flow estimates, and
run/pause/resume/stop semantics — both the Python runner and the JS mock in `bridge.ts` must match
it exactly. If you change behavior covered by the contract, update `CONTRACT.md` in the same change.

## Commands

Python (run from `gui/`, inside `.venv`):
```bash
pip install -r requirements.txt        # + requirements-dev.txt for pytest
python app.py                          # production mode (serves web/dist)
python app.py --dev                    # dev mode (expects `npm run dev` on :5173)
pytest                                  # run Python tests (tests/, 10s timeout per test)
pytest tests/test_api.py::test_name    # run a single test
```

Frontend (run from `gui/web/`):
```bash
npm install
npm run dev       # vite dev server, localhost:5173
npm run build     # tsc --noEmit + vite build -> web/dist
npm test          # vitest run
npx vitest run src/blocks.test.ts   # run a single test file
```

To develop with hardware wired up: `npm run dev` in one terminal, `python app.py --dev` in another.

## Architecture

**Communication is one-directional-call / one-directional-push:**
- Frontend → backend: calls methods on `window.pywebview.api` (the `Api` class in `api.py`,
  exposed via `js_api=api` in `app.py`), matching the `BackendApi` interface in `types.ts`.
- Backend → frontend: `Api._emit_event` calls `window.__onBackendEvent(<json>)` via
  `window.evaluate_js`. `bridge.ts` installs that global and exposes `onEvent(cb)` for subscribers.

**The program runner** (`Api._run_program_impl` and helpers in `api.py`) executes on a background
thread so it never blocks the pywebview UI thread. A `PumpBlock` runs all 4 channels (A/B/C/D)
by `ml` only; it blocks in `hardware.run_step` until the pump reports every channel done, and
can't be paused: Pause takes effect before the next block (`_wait_if_paused`); Stop calls
`hardware.stop_all`, which cancels the step. `manual_dose` reuses the same single-block runner path
(status `dosing` instead of `raining`).

**mL, not speed:** channels are dosed by `ml` only; the pump picks the rate (90 % of max flow).
Durations are estimates: `seconds = ml / FLOW_ML_PER_S`. `FLOW_ML_PER_S` is defined independently
in `hardware.py` (Python: stopped-block estimates) and `web/src/flow.ts` (TypeScript: UI mock,
animation timing, block times) — **these two must be kept in sync** manually; there is no shared
source.

**Frontend has no framework** — vanilla TS + Vite, DOM built by hand in `main.ts`. Key modules:
- `bridge.ts` — the `Api` client; when no pywebview host is present (e.g. plain browser dev) it
  waits briefly for `pywebviewready` then falls back to an in-process mock that mirrors the
  Python runner's event sequence and behavior (`CONTRACT.md`'s "Runner semantics" section governs
  both). Presets in mock mode persist to `localStorage`.
- `blocks.ts` — the drag-and-drop (SortableJS) program block editor.
- `scene.ts` — the SVG cartoon scene (clouds for A/B/C, bowl for D); animates rain/pour based on
  `block_start`/`dose_start` events, blends bowl liquid color by volume-weighted A/B/C mix, and
  emits click-to-dose events back up.
- `totals.ts` / `sessionlog.ts` — track per-channel session totals from `pumped` events for
  working backwards from a result (e.g. target pH) to how much of each liquid was used.
- `types.ts` — the shared contract types; must stay verbatim-identical to the copy in
  `CONTRACT.md`.

**Pump** (`hardware.py`): set up 1:1 like `../reglo_gui/reglo_gui.py`. The `ismatec` library
(`ismatec-control`, vendored in `../../lib/ismatec-pump`) is async, so the `Pump` and
`DilutionController` (`dilution_controller.py`, a verbatim copy of `../reglo_gui/`'s — keep it in
sync) live on an asyncio loop in a daemon thread. The real pump is detected at `PUMP_ADDRESS` (`connect()`, with a timeout), else the library's mock pump stands in; `FORCE_MOCK = True` (set by the tests) skips the real one; `python app.py --pump-log` turns ismatec debug logging on (also a UI toggle).
`tests/test_hardware.py` runs against that mock; `tests/test_api.py` monkeypatches `run_step`.

**Cuvette rotator** (`rotator.py`): this is a USB-serial driver for
the Trinket M0 that turns the cuvette 0–60° (firmware in `../firmware/cuvette_rotator/`). Rotate
blocks and `manual_rotate` wait for the board's `MOVE_COMPLETE`; replies are matched to their own
command because a cancelled move's late reply can still be in the pipe. Tests use a fake board in
`tests/test_rotator.py`. With the board plugged in, starting the app homes the motor.

**Presets** are YAML programs (`presets/*.yaml`): a name, per-channel solution labels,
a list of blocks (pump or pause), and a repeat count. Loading an old preset that has a per-channel `s` (seconds)
or `speed` drops those fields.

## Testing notes

- Python tests stub out `webview` in `conftest.py` before `api.py` is imported, since `pywebview`
  isn't installable/importable in all test environments.
- Frontend and backend both implement the runner semantics independently (real Python runner vs.
  TS mock in `bridge.ts`); when changing run/pause/resume/stop/dose behavior, update both and their
  tests (`tests/test_api.py`, `web/src/bridge.test.ts`) so they don't drift apart.
