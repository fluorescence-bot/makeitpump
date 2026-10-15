# DIGIFAB — Automatic Solution Mixer

Course project for Digital Fabrication and Maker Space: an automatic solution
mixer for a fluorescence scientist. A robot pumps up to four liquids
(A, B, C) into a mixing bowl (D), which is then pumped out to a measuring
instrument. The pumping is done by an Ismatec Reglo ICC peristaltic pump, and a
small robot rotates the cuvette (0–60°) for the measurement.

## Repository layout

```
code/
├── src/
│   ├── kawaii_gui/       # Main desktop GUI (pywebview + TypeScript) — start here
│   ├── reglo_gui/        # Simpler Tkinter GUI for the Reglo pump + rotator
│   └── cuvette_rotater/  # CircuitPython firmware for the cuvette rotator (Trinket M0)
└── lib/
    └── ismatec-pump/     # Python driver for the Ismatec Reglo ICC pump
course/
├── portfolios/           # Portfolio reports (LaTeX/PDF) and sketches
├── exercises/            # Standalone course exercises (Arduino)
├── notes/                # Lab visit notes and photos
└── prototypes/           # Early static HTML mockups
```
