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
- `code/src/cuvette_rotater/` — Arduino sketch `cuvette_rotater.ino` for the Nano (A4988 + NEMA 17)
  that rotates the cuvette (0–60° from the calibrated 0°, USB serial), plus `calibrate.py` for
  finding that 0°; see its README for the serial protocol. The board's copy is the one that runs —
  keep this in sync.
- `code/lib/ismatec-pump/` — vendored `ismatec-control` pump driver (GPL-3; the rest of the repo is MIT). Both GUIs install it
  from here via a relative path in their `requirements.txt`.

`course/` (reports, sketches, notes) exists only in the private `alexandermittet/DIGIFAB` repo.
`scripts/publish-to-org.sh` pushes everything else, with history, to the public
`fluorescence-bot/rainmixer`; add any new top-level code path to its `KEEP` list.

## CI

`.github/workflows/build-apps.yml` builds the GUI with PyInstaller (bundling `web/dist` and
`presets`) for Windows (`RainMixer.exe` + console `RainMixer-debug.exe`), macOS (`.app` zips for
Apple silicon and Intel) and Linux (Qt, `.tar.gz`), starts each for 40 s as a smoke test, and on
every push to `main` publishes them as the rolling `latest` release (PRs only build). If you add
runtime files the app reads via `resource_dir()`, add them to every `--add-data` list there.
