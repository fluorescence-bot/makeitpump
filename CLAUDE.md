# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Course project (Digital Fabrication and Maker Space): an automatic solution mixer for a
fluorescence scientist. A robot pumps up to four liquids (A, B, C) into a mixing bowl (D), which
is then pumped out to a measuring instrument. Electronics run on an Adafruit M0 Express.

This repo is a mix of the actual product code and course/report material — most of it is not
software to build or run:

- `gui/` — **the real codebase**: the pywebview + TypeScript desktop app that controls the mixer.
  Has its own [`gui/CLAUDE.md`](gui/CLAUDE.md) (commands, architecture) and
  [`gui/CONTRACT.md`](gui/CONTRACT.md) (the frontend/backend interface spec) — read those before
  working in `gui/`.
- `course_exercises/arduino/` — standalone Arduino sketches from course exercises (e.g.
  `hc_sr04/`), unrelated to the `gui/` app.
- `report.pdf` / `report.tex` — the project report.
- `sketches/` / `sketches.pdf` — design sketches (images).
- `prototypes/` — early static HTML mockups, superseded by `gui/`.
- `notes/` — misc project notes.
- `pyproject.toml` / `uv.lock` at the root are for exploratory/hardware scripts (e.g. the
  `ismatec` pump library, `pyserial`, `matplotlib`) outside `gui/`, managed with `uv`; they are
  unrelated to `gui/`'s own `requirements.txt` + `.venv`.

## Working here

Nearly all real feature work happens under `gui/` — go there first. Only touch the root level for
things that are genuinely repo-wide (this file, `.gitignore`, top-level docs).
