# RainMixer GUI

A cute desktop application for controlling the automatic solution mixer
(RainMixer): a robot pumps up to four liquids (A, B, C) into a mixing bowl (D),
which is then pumped out to a measuring instrument. It's a pywebview desktop app
with a Python backend and a TypeScript (Vite) frontend.

All paths below are relative to this directory (`code/src/kawaii_gui/`). See
[`CONTRACT.md`](CONTRACT.md) for how the frontend talks to the backend.

## Setup

### Prerequisites

- Python 3.8+
- Node.js 16+ (for building the frontend)

### Quick start (one command)

The run script installs whatever is missing (Homebrew on macOS, Node.js/npm,
uv, Python and its packages, GTK/WebKit on Linux via apt, dnf or pacman),
builds the frontend and opens the app. Re-running it skips steps that are
already done; add `--clean` to start from scratch.

- **macOS / Linux:** `./run.sh`
- **Windows 10/11:** double-click `run.cmd` (or run `run.cmd` in a terminal),
  or `./run.sh` from Git Bash. Uses `winget` to install Node.js LTS and, if
  missing, the WebView2 runtime; Windows may ask for permission.

### Manual installation (All Platforms)

1. **Enter the GUI directory** (from the repo root):
   ```bash
   cd code/src/kawaii_gui
   ```

2. **Create a Python virtual environment:**
   ```bash
   python3 -m venv .venv
   ```

3. **Activate the virtual environment:**
   - **macOS/Linux:**
     ```bash
     source .venv/bin/activate
     ```
   - **Windows (Command Prompt):**
     ```bash
     .venv\Scripts\activate.bat
     ```
   - **Windows (PowerShell):**
     ```bash
     .venv\Scripts\Activate.ps1
     ```

4. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

5. **Linux-specific (if you haven't installed GTK and WebKit already):**
   ```bash
   sudo apt install python3-gi gir1.2-webkit2-4.1
   ```

6. **Build the frontend:**
   ```bash
   cd web
   npm install
   npm run build
   cd ..
   ```

### Running the Application

**Production mode:**
```bash
python app.py
```

**Development mode** (requires `npm run dev` running in a separate terminal):
```bash
# In one terminal
cd web && npm run dev

# In another terminal
python app.py --dev
```

## Hardware Integration

`hardware.py` drives the Ismatec Reglo ICC pump with Andreas' pump library
(`ismatec-control`, vendored in [`../../lib/ismatec-pump`](../../lib/ismatec-pump) and
installed from there by `requirements.txt`) and his `DilutionController`
(`dilution_controller.py`, copied from `../reglo_gui/`), set up the same way as
in `reglo_gui.py`. Channels A/B/C/D are pump channels 1/2/3/4.

`requirements.txt` refers to it by a relative path, so run `pip install -r
requirements.txt` from this directory.

Settings at the top of `hardware.py`:

- `FORCE_MOCK` - `False` (the default) looks for the real pump and falls
  back to the library's mock pump if it isn't there or doesn't answer (the
  app says so). `True` always uses the mock. Run
  `python app.py --pump-log` for the pump library's debug logging.
- `PUMP_ADDRESS` - serial port of the pump, e.g. `/dev/ttyACM0` or `COM3`.
  `None` (the default) tries every USB serial port except the cuvette
  rotator and Arduino boards (`NOT_PUMP_VIDS`), so it works on Linux
  (`/dev/ttyACM*`), macOS (`/dev/cu.usbmodem*`) and Windows (`COMx`) alike.
- Port dropdowns: if auto-detection picks the wrong port, choose it by hand in
  the dropdown next to **🔌 Redetect pump** (pump) or just above the 🤖
  (cuvette rotator). "Auto port" goes back to detection. A chosen pump port
  must still answer the pump, else the mock is used.
- `FLOW_ML_PER_S` - estimated flow per channel, only used to estimate dose
  times (placeholder; keep in sync with `web/src/flow.ts`).

The pump runs every step at 90 % of its max flow, so pump blocks and doses are
set in mL only. A pump block can't be paused: Pause takes effect before the
next block.

We ended up running the electronics with an Adafruit M0 Express.

## Manual Dosing & Session Totals

Clicking a cloud (A/B/C) or the bowl (D) in the scene doses that channel's
"mL/click" amount on the runner thread — doses are refused while the runner is busy and
can be stopped mid-way. Every pump (manual or from a program block) emits a
`pumped` event with the mL actually delivered, which the frontend accumulates
into a per-channel (A-D) session log shown in the scene, with reset and
copy-as-CSV. There is no standalone "Make it POUR!" button/API anymore —
dosing is click-driven.

## Project Structure

```
code/src/kawaii_gui/
├── requirements.txt     # Python dependencies
├── app.py              # Application entry point
├── api.py              # Backend API (exposed to JS)
├── hardware.py         # Pump control (ismatec library + DilutionController)
├── rotator.py          # Cuvette rotator (Arduino Nano over USB serial)
├── dilution_controller.py  # Copy of ../reglo_gui/dilution_controller.py
├── run.sh / run.ps1 / run.cmd  # One-shot setup + launch
├── tests/              # Python tests (pytest)
├── presets/            # Preset programs (YAML)
│   └── example.yaml
└── web/                # Frontend (Vite + TypeScript)
    ├── package.json
    ├── src/
    └── dist/           # Built frontend (created by npm run build)
```

## Development

### Frontend (web/)
- TypeScript + Vite
- Cute cartoon/anime visual style with pastel colors
- Block-based program editor (drag-and-drop)
- SVG scene visualization

### Tests

```bash
pip install -r requirements-dev.txt && pytest -q   # Python
cd web && npm test                                  # TypeScript
```

### Backend (Python)
- pywebview for desktop GUI
- Threading for non-blocking program execution
- YAML-based preset system
- Event-driven communication with frontend

## Troubleshooting

**"web/dist/index.html not found"**: Build the frontend with `cd web && npm install && npm run build`

**pywebview import error**: Make sure you're in the virtual environment and ran `pip install -r requirements.txt`

**Linux GTK errors**: Install `python3-gi` and `gir1.2-webkit2-4.1` as shown above

**Linux "QtPy"/"must have either QT or GTK" errors**: GTK couldn't load, so pywebview fell back to Qt. `requirements.txt` installs the Qt backend on Linux; re-run `pip install -r requirements.txt` (or just `./run.sh`). If Qt then complains about the `xcb` platform plugin, install `libxcb-cursor0` (apt) / `xcb-util-cursor` (dnf, pacman).

**Windows: `run.cmd` says Node.js wasn't found right after installing it**: the installer updated PATH for new terminals only. Close the window and run `run.cmd` again. If `winget` itself is missing, install Node.js LTS from https://nodejs.org.

**Windows: blank window**: pywebview needs the Microsoft Edge WebView2 runtime (preinstalled on Windows 11). Install it from https://developer.microsoft.com/microsoft-edge/webview2/.
