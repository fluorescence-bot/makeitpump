# RainMixer GUI

A cute desktop application for controlling a robotic liquid mixer (RainMixer). This is a student project that combines hardware control with a delightful TypeScript/React frontend.

## Setup

### Prerequisites

- Python 3.8+
- Node.js 16+ (for building the frontend)

### Quick start (one command)

The run script installs whatever is missing (Node.js, uv, Python packages,
platform GUI libraries), builds the frontend and opens the app. Re-running it
skips steps that are already done; add `--clean` to start from scratch.

- **macOS / Linux:** `./run.sh`
- **Windows 10/11:** double-click `run.cmd` (or run `run.cmd` in a terminal),
  or `./run.sh` from Git Bash. Uses `winget` to install Node.js LTS and, if
  missing, the WebView2 runtime; Windows may ask for permission.

### Manual installation (All Platforms)

1. **Clone or enter the gui directory:**
   ```bash
   cd gui
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
([drivs/ismatec-pump](https://github.com/drivs/ismatec-pump), installed from
`requirements.txt` as `ismatec-control`) and his `DilutionController`
(`dilution_controller.py`, copied from `../reglo_gui/`), set up the same way as
in `reglo_gui.py`. Channels A/B/C/D are pump channels 1/2/3/4.

The library installs over SSH, so `pip install -r requirements.txt` needs your
SSH key on GitHub and collaborator access to the repo.

Settings at the top of `hardware.py`:

- `DEBUGGING` - `True` (the default) uses the library's mock pump, so no
  hardware is needed. Set it to `False` to drive the real pump.
- `PUMP_ADDRESS` - serial port of the pump, e.g. `/dev/ttyACM0` or `COM3`
  (not the cuvette rotator's port).
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
gui/
├── requirements.txt     # Python dependencies
├── app.py              # Application entry point
├── api.py              # Backend API (exposed to JS)
├── hardware.py         # Pump control (ismatec library + DilutionController)
├── dilution_controller.py  # Copy of ../reglo_gui/dilution_controller.py
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
