# RainMixer GUI

A cute desktop application for controlling a robotic liquid mixer (RainMixer). This is a student project that combines hardware control with a delightful TypeScript/React frontend.

## Setup

### Prerequisites

- Python 3.8+
- Node.js 16+ (for building the frontend)

### Installation (All Platforms)

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

The `hardware.py` file contains stubs for pump control:

- `start_pump(channel, ml, s)` - Start a pump on channel A/B/C/D
- `stop_pump(channel)` - Stop a pump
- `stop_all()` - Stop all pumps

The backend team replaces these functions with actual pump control logic. Currently they just print debug messages.

## Project Structure

```
gui/
├── requirements.txt     # Python dependencies
├── app.py              # Application entry point
├── api.py              # Backend API (exposed to JS)
├── hardware.py         # Hardware control stubs
├── presets/            # Preset programs (JSON)
│   └── example.json
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
- JSON-based preset system
- Event-driven communication with frontend

## Troubleshooting

**"web/dist/index.html not found"**: Build the frontend with `cd web && npm install && npm run build`

**pywebview import error**: Make sure you're in the virtual environment and ran `pip install -r requirements.txt`

**Linux GTK errors**: Install `python3-gi` and `gir1.2-webkit2-4.1` as shown above
