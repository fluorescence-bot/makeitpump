# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RainMixer: an automatic solution mixer for a fluorescence scientist (course project, Digital
Fabrication and Maker Space). A robot pumps up to four liquids (A, B, C) into a mixing bowl (D),
which is then pumped out to a measuring instrument.

- `code/src/kawaii_gui/` — **the main codebase**: the pywebview + TypeScript desktop app that
  controls the mixer. Has its own `CLAUDE.md` (commands, architecture) and `CONTRACT.md` (the
  frontend/backend interface spec) — read those before working there.
- `code/src/reglo_gui/` — simpler Tkinter GUI for the pump + rotator.
- `code/src/cuvette_rotater/` — CircuitPython `code.py` for the Trinket M0 that rotates the cuvette
  (0–60°, USB serial); see its README for the serial protocol. The board's copy is the one that
  runs — keep this in sync.
- `code/lib/ismatec-pump/` — vendored `ismatec-control` pump driver (GPL-3). Both GUIs install it
  from here via a relative path in their `requirements.txt`.

## CI

`.github/workflows/build-exe.yml` builds a one-file Windows `RainMixer.exe` (PyInstaller, bundling
`web/dist` and `presets`) on every push to `main` and publishes it as the rolling `latest`
release. If you add runtime files the app reads via `resource_dir()`, add them to its
`--add-data` list.
