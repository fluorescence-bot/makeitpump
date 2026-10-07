# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RainMixer: a pywebview desktop app that controls a robotic liquid mixer. Python backend
(`app.py`, `api.py`, `hardware.py`) drives a vanilla TypeScript/Vite frontend (`web/`) through
pywebview's JS bridge. Cute cartoon/pastel visual style. Student project; `hardware.py` drives the Ismatec
pump through Andreas' `ismatec` library and `DilutionController` (see "Pump" below).

**Read `CONTRACT.md` before touching backend/frontend communication, types, or runner semantics.**
It is the source of truth for the `Api`/`BackendApi` interface, event shapes, pump-speed math, and
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
concurrently, each for its own duration derived from `ml` and `speed`; a block ends when its
slowest channel finishes. A pump block blocks in `hardware.run_step` and can't be paused: Pause
takes effect before the next block (`_wait_if_paused`); Stop calls `hardware.stop_all`, which
cancels the step. `manual_dose` reuses the same single-block runner path
(status `dosing` instead of `raining`).

**Speed, not time:** channels are dosed by `ml` at a `speed` (0-100% of max flow), not by seconds.
Duration is derived: `seconds = ml / (MAX_FLOW_ML_PER_S * speed / 100)`. `MAX_FLOW_ML_PER_S` is
defined independently in `hardware.py` (Python, used by the real runner) and `web/src/flow.ts`
(TypeScript, used by the UI mock and by animation timing) — **these two must be kept in sync**
manually; there is no shared source.

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
(`ismatec-control` from `drivs/ismatec-pump`, over SSH) is async, so the `Pump` and
`DilutionController` (`dilution_controller.py`, a verbatim copy of `../reglo_gui/`'s — keep it in
sync) live on an asyncio loop in a daemon thread. `DEBUGGING = True` uses the library's mock pump.
`tests/test_hardware.py` runs against that mock; `tests/test_api.py` monkeypatches `run_step`.

**Cuvette rotator** (`rotator.py`): this is a USB-serial driver for
the Trinket M0 that turns the cuvette 0–60° (firmware in `../firmware/cuvette_rotator/`). Rotate
blocks and `manual_rotate` wait for the board's `MOVE_COMPLETE`; replies are matched to their own
command because a cancelled move's late reply can still be in the pipe. Tests use a fake board in
`tests/test_rotator.py`. With the board plugged in, starting the app homes the motor.

**Presets** are YAML/JSON programs (`presets/*.yaml`/`.json`): a name, per-channel solution labels,
a list of blocks (pump or pause), and a repeat count. Loading an old preset that used `s` (seconds)
per channel instead of `speed` drops `s` and resets to `DEFAULT_SPEED`, since converting would
often exceed 100%.

## Testing notes

- Python tests stub out `webview` in `conftest.py` before `api.py` is imported, since `pywebview`
  isn't installable/importable in all test environments.
- Frontend and backend both implement the runner semantics independently (real Python runner vs.
  TS mock in `bridge.ts`); when changing run/pause/resume/stop/dose behavior, update both and their
  tests (`tests/test_api.py`, `web/src/bridge.test.ts`) so they don't drift apart.
