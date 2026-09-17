# DIGIFAB — Automatic Solution Mixer

Course project for Digital Fabrication and Maker Space: an automatic solution
mixer for a fluorescence scientist. A robot pumps up to four liquids
(A, B, C) into a mixing bowl (D), which is then pumped out to a measuring
instrument.

- `report.pdf` / `report.tex` — the project report
- `sketches/` / `sketches.pdf` — design sketches
- `prototypes/` — early HTML prototypes
- `gui/` — the graphical user interface (this repo's code)

## Running the GUI

```bash
cd gui
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
python app.py
```

See [`gui/README.md`](gui/README.md) for full setup (including Linux
dependencies and dev mode), and [`gui/CONTRACT.md`](gui/CONTRACT.md) for how
the GUI talks to the backend. The pump/robot stubs the backend team fills in
live in [`gui/hardware.py`](gui/hardware.py).
