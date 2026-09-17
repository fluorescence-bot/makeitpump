"""
Backend API for RainMixer GUI.

Exposes the BackendApi interface to JavaScript via pywebview.
Manages program execution on a daemon thread with pause/resume/stop support.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import hardware
import webview


def resource_dir() -> Path:
    """Return the directory where resources are stored, supporting PyInstaller bundling."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


class Api:
    """Backend API for RainMixer, exposed to JavaScript."""

    def __init__(self) -> None:
        self._window: Any = None
        self._runner_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._resume_event = threading.Event()
        self._runner_lock = threading.Lock()
        self._active_status = "raining"  # status to restore after resume
        self._manual_channels: set = set()

    def set_window(self, window: Any) -> None:
        """Set the pywebview window object for event delivery."""
        self._window = window

    def _emit_event(self, event: Dict[str, Any]) -> None:
        """Push an event to the frontend via JavaScript."""
        if self._window is None:
            return
        try:
            js_code = "window.__onBackendEvent(" + json.dumps(event) + ")"
            self._window.evaluate_js(js_code)
        except Exception as e:
            print(f"[api] Failed to emit event: {e}")

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a preset name to a safe filename."""
        # Remove non-alphanumeric, keeping spaces and common chars
        sanitized = re.sub(r"[^\w\s.-]", "", name).strip()
        # Replace spaces with underscores
        sanitized = re.sub(r"\s+", "_", sanitized)
        # Ensure it's not empty
        return sanitized or "preset"

    def _get_presets_dir(self) -> Path:
        """Return the user presets directory, creating it if needed.

        When frozen (PyInstaller .exe) this is a `presets/` folder next to the
        executable, so saved presets survive restarts; bundled examples are
        copied there on first run.
        """
        if getattr(sys, "frozen", False):
            presets_dir = Path(sys.executable).parent / "presets"
            first_run = not presets_dir.exists()
            presets_dir.mkdir(exist_ok=True)
            bundled = resource_dir() / "presets"
            if first_run and bundled.is_dir():
                for f in bundled.glob("*.json"):
                    (presets_dir / f.name).write_bytes(f.read_bytes())
        else:
            presets_dir = Path(__file__).parent / "presets"
            presets_dir.mkdir(exist_ok=True)
        return presets_dir

    def _run_program_impl(self, blocks: List[Dict[str, Any]], repeat: int) -> None:
        """Implementation of run_program on the runner thread."""
        try:
            self._active_status = "raining"
            self._emit_event({"type": "status", "status": "raining"})
            total_blocks = len(blocks)
            total_work = total_blocks * repeat

            for repeat_idx in range(repeat):
                if self._stop_event.is_set():
                    break

                for block_idx, block in enumerate(blocks):
                    if self._stop_event.is_set():
                        break

                    progress = (repeat_idx * total_blocks + block_idx) / total_work
                    self._emit_event({"type": "block_start", "index": block_idx, "repeat": repeat_idx, "block": block})

                    block_type = block.get("type")

                    if block_type == "pump":
                        self._run_pump_block(block.get("pumps", {}))
                    elif block_type == "pause":
                        self._run_pause_block(float(block.get("s", 0.0)))

                    if not self._stop_event.is_set():
                        self._emit_event({"type": "block_end", "index": block_idx, "repeat": repeat_idx})
                        progress = (repeat_idx * total_blocks + block_idx + 1) / total_work
                        self._emit_event({"type": "progress", "fraction": progress})

            if not self._stop_event.is_set():
                self._emit_event({"type": "status", "status": "done"})
        except Exception as e:
            self._emit_event({"type": "status", "status": "error", "message": str(e)})
            hardware.stop_all()

    def _wait_step(self, elapsed: float, last: float) -> "tuple[float, float]":
        """Sleep one step and return (elapsed, last), counting only un-paused time."""
        time.sleep(0.05)
        now = time.monotonic()
        return elapsed + (now - last), now

    def _run_pump_block(self, pumps: Dict[str, Any]) -> None:
        """Run several channels at once. Each channel pumps its own mL over its own
        seconds; the block ends when the longest channel is done. Checks
        pause/stop every 50 ms."""
        jobs: Dict[str, "tuple[float, float]"] = {}
        for ch, p in pumps.items():
            ml, s = float(p.get("ml", 0) or 0), float(p.get("s", 0) or 0)
            if s > 0:
                jobs[ch] = (ml, s)
        if not jobs:
            return
        duration = max(s for _, s in jobs.values())
        running: set = set()

        def start(ch: str, elapsed: float) -> None:
            ml, s = jobs[ch]
            remaining_s = s - elapsed
            hardware.start_pump(ch, ml * remaining_s / s, remaining_s)
            running.add(ch)

        for ch in jobs:
            start(ch, 0.0)

        elapsed, last, paused = 0.0, time.monotonic(), False
        while elapsed < duration:
            if self._stop_event.is_set():
                for ch in running:
                    hardware.stop_pump(ch)
                return
            if self._pause_event.is_set() and not paused:
                for ch in running:
                    hardware.stop_pump(ch)
                running.clear()
                paused = True
                self._emit_event({"type": "status", "status": "paused"})
            if paused:
                if not self._resume_event.is_set():
                    time.sleep(0.05)
                    continue
                self._resume_event.clear()
                paused = False
                self._emit_event({"type": "status", "status": self._active_status})
                for ch, (_, s) in jobs.items():
                    if s > elapsed:
                        start(ch, elapsed)
                last = time.monotonic()
            elapsed, last = self._wait_step(elapsed, last)
            for ch in list(running):
                if elapsed >= jobs[ch][1]:
                    hardware.stop_pump(ch)
                    running.discard(ch)

        for ch in running:
            hardware.stop_pump(ch)

    def _run_pause_block(self, s: float) -> None:
        """Wait s seconds (not counting time spent paused), checking pause/stop."""
        elapsed, last, paused = 0.0, time.monotonic(), False
        while elapsed < s:
            if self._stop_event.is_set():
                return
            if self._pause_event.is_set() and not paused:
                paused = True
                self._emit_event({"type": "status", "status": "paused"})
            if paused:
                if not self._resume_event.is_set():
                    time.sleep(0.05)
                    continue
                self._resume_event.clear()
                paused = False
                self._emit_event({"type": "status", "status": self._active_status})
                last = time.monotonic()
            elapsed, last = self._wait_step(elapsed, last)

    def run_program(self, blocks: List[Dict[str, Any]], repeat: int) -> Dict[str, Any]:
        """Run a program with the given blocks and repetition count."""
        with self._runner_lock:
            if self._runner_thread is not None and self._runner_thread.is_alive():
                return {"ok": False, "error": "A program is already running"}

            self._stop_event.clear()
            self._pause_event.clear()
            self._resume_event.clear()

            self._runner_thread = threading.Thread(
                target=self._run_program_impl,
                args=(blocks, repeat),
                daemon=True,
            )
            self._runner_thread.start()

        return {"ok": True}

    def pause_run(self) -> Dict[str, Any]:
        """Pause the currently running program."""
        if self._pause_event.is_set():
            return {"ok": False}
        self._pause_event.set()
        return {"ok": True}

    def resume_run(self) -> Dict[str, Any]:
        """Resume a paused program."""
        if not self._pause_event.is_set():
            return {"ok": False}
        self._resume_event.set()
        self._pause_event.clear()
        return {"ok": True}

    def stop(self) -> Dict[str, Any]:
        """Stop the currently running program."""
        self._stop_event.set()
        hardware.stop_all()
        self._emit_event({"type": "status", "status": "stopped"})
        return {"ok": True}

    def pour(self, ml: float, s: float) -> Dict[str, Any]:
        """Pour liquid from bowl D into the final cup."""
        with self._runner_lock:
            if self._runner_thread is not None and self._runner_thread.is_alive():
                return {"ok": False, "error": "A program is already running"}

            self._stop_event.clear()
            self._pause_event.clear()
            self._resume_event.clear()

            def _pour_impl() -> None:
                try:
                    self._active_status = "pouring"
                    self._emit_event({"type": "status", "status": "pouring"})
                    self._emit_event({"type": "pour_start", "ml": ml, "s": s})
                    self._run_pump_block({"D": {"ml": ml, "s": s}})
                    if not self._stop_event.is_set():
                        self._emit_event({"type": "status", "status": "done"})
                except Exception as e:
                    self._emit_event({"type": "status", "status": "error", "message": str(e)})
                    hardware.stop_all()

            self._runner_thread = threading.Thread(target=_pour_impl, daemon=True)
            self._runner_thread.start()

        return {"ok": True}

    def _busy(self) -> bool:
        return self._runner_thread is not None and self._runner_thread.is_alive()

    def manual_pump_start(self, channel: str) -> Dict[str, Any]:
        """Start a manual pump on the given channel (runs until stopped).
        Refused while a program or pour is running, so it can't fight the runner."""
        if self._busy():
            return {"ok": False, "error": "Busy running a program"}
        hardware.start_pump(channel, 0, 0)
        self._manual_channels.add(channel)
        self._emit_event({"type": "manual", "channel": channel, "active": True})
        return {"ok": True}

    def manual_pump_stop(self, channel: str) -> Dict[str, Any]:
        """Stop a manual pump on the given channel."""
        if channel not in self._manual_channels:
            return {"ok": False}
        self._manual_channels.discard(channel)
        hardware.stop_pump(channel)
        self._emit_event({"type": "manual", "channel": channel, "active": False})
        return {"ok": True}

    def list_presets(self) -> List[str]:
        """List all available presets."""
        presets_dir = self._get_presets_dir()
        presets = []
        for preset_file in presets_dir.glob("*.json"):
            try:
                with open(preset_file) as f:
                    preset_data = json.load(f)
                    if "name" in preset_data:
                        presets.append(preset_data["name"])
            except Exception:
                pass
        return sorted(presets)

    def load_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a preset by name."""
        presets_dir = self._get_presets_dir()
        for preset_file in presets_dir.glob("*.json"):
            try:
                with open(preset_file) as f:
                    preset_data = json.load(f)
                    if preset_data.get("name") == name:
                        return preset_data
            except Exception:
                pass
        return None

    def save_preset(self, preset: Dict[str, Any]) -> Dict[str, Any]:
        """Save a preset to disk."""
        try:
            name = preset.get("name", "preset")
            presets_dir = self._get_presets_dir()
            filename = self._sanitize_filename(name) + ".json"
            preset_path = presets_dir / filename

            with open(preset_path, "w") as f:
                json.dump(preset, f, indent=2)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def import_preset_file(self) -> Optional[Dict[str, Any]]:
        """Import a preset from a file using the native file dialog."""
        if self._window is None:
            return None
        try:
            # Try new webview FileDialog constants first
            try:
                dialog_type = webview.FileDialog.OPEN
            except AttributeError:
                # Fall back to old constants
                dialog_type = webview.OPEN_DIALOG

            file_result = self._window.create_file_dialog(
                dialog_type,
                file_types=("JSON files (*.json)", "All files (*.*)",),
            )
            if file_result:
                file_path = file_result[0]
                with open(file_path) as f:
                    return json.load(f)
        except Exception as e:
            print(f"[api] Failed to import preset: {e}")
        return None

    def export_preset_file(self, preset: Dict[str, Any]) -> Dict[str, Any]:
        """Export a preset to a file using the native file dialog."""
        if self._window is None:
            return {"ok": False, "error": "Window not available"}
        try:
            # Try new webview FileDialog constants first
            try:
                dialog_type = webview.FileDialog.SAVE
            except AttributeError:
                # Fall back to old constants
                dialog_type = webview.SAVE_DIALOG

            name = preset.get("name", "preset")
            file_path = self._window.create_file_dialog(
                dialog_type,
                file_types=("JSON files (*.json)",),
                save_filename=self._sanitize_filename(name) + ".json",
            )
            if isinstance(file_path, (list, tuple)):
                file_path = file_path[0] if file_path else None
            if file_path:
                with open(file_path, "w") as f:
                    json.dump(preset, f, indent=2)
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "No file selected"}
