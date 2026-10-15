"""Install (or remove) a desktop entry for the Reglo GUI on Linux.

GNOME takes an app's icon in the dock and Alt-Tab from its .desktop file,
matched to the window by WM_CLASS, not from the window itself. This writes
~/.local/share/applications/reglogui.desktop, which also adds a launcher.

Run it with the Python the app should use (the venv's), from anywhere:
    .venv/bin/python install_desktop_entry.py           # install / update
    .venv/bin/python install_desktop_entry.py --remove  # remove
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WM_CLASS = "Reglogui"           # must match WM_CLASS in reglo_gui.py
APPS_DIR = Path(os.environ.get("XDG_DATA_HOME",
                               Path.home() / ".local" / "share")) / "applications"
ENTRY = APPS_DIR / "reglogui.desktop"


def quote(path):
    """Quote a path for the Exec key of a .desktop file."""
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"') \
        .replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def entry_text():
    return f"""[Desktop Entry]
Type=Application
Name=Reglo ICC Pump Control
Comment=Run mixing sequences on the Reglo ICC pump and cuvette rotator
Exec={quote(sys.executable)} {quote(HERE / "reglo_gui.py")}
Path={HERE}
Icon={HERE / "pump-arm-icon.png"}
StartupWMClass={WM_CLASS}
Terminal=false
Categories=Science;
"""


def main():
    if "--remove" in sys.argv[1:]:
        if ENTRY.exists():
            ENTRY.unlink()
            print(f"Removed {ENTRY}")
        else:
            print(f"Nothing to remove: {ENTRY} does not exist")
        return
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    ENTRY.write_text(entry_text(), encoding="utf-8")
    print(f"Installed {ENTRY}")
    print(f"  runs: {sys.executable} {HERE / 'reglo_gui.py'}")


if __name__ == "__main__":
    main()
