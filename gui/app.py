#!/usr/bin/env python3
"""
RainMixer desktop GUI application.

Launches a pywebview window with the RainMixer interface and starts the backend API.
"""

import importlib.util
import os
import sys
from pathlib import Path

# On Linux pywebview tries GTK first and logs a scary "No module named 'gi'"
# traceback before falling back to Qt. Skip straight to Qt when GTK's Python
# bindings aren't installed. Must run before webview picks its backend.
if sys.platform.startswith("linux") and "PYWEBVIEW_GUI" not in os.environ:
    if importlib.util.find_spec("gi") is None:
        os.environ["PYWEBVIEW_GUI"] = "qt"

import webview

from api import Api, resource_dir


def main() -> None:
    """Start the RainMixer application."""
    dev_mode = "--dev" in sys.argv

    if dev_mode:
        url = "http://localhost:5173"
    else:
        dist_dir = resource_dir() / "web" / "dist"
        index_path = dist_dir / "index.html"

        if not index_path.exists():
            print(
                "Error: web/dist/index.html not found.\n"
                "To build the frontend:\n"
                "  cd web\n"
                "  npm install\n"
                "  npm run build\n"
            )
            sys.exit(1)

        url = str(index_path)

    api = Api()

    window = webview.create_window(
        "RainMixer",
        url,
        js_api=api,
        width=1280,
        height=820,
        min_size=(1000, 700),
    )

    api.set_window(window)

    webview.start(http_server=True)


if __name__ == "__main__":
    main()
