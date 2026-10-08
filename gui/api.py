"""
Backend API for RainMixer GUI.

Exposes the BackendApi interface to JavaScript via pywebview.
Manages program execution on a daemon thread with pause/resume/stop support.
"""

import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import hardware
import rotator
import webview
import yaml


def pump_seconds(ml: float) -> float:
    """Estimated time one channel takes to pump `ml`."""
    return ml / hardware.FLOW_ML_PER_S


def pumps_error(pumps: Dict[str, Any]) -> Optional[str]:
    """Return why a block's pumps are invalid, or None if they are fine."""
    for ch, p in pumps.items():
        if float(p.get("ml", 0) or 0) < 0:
            return f"{ch}: negative mL"
    return None


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
        self._rotator = rotator.Rotator()
        self._rotator_state = "disconnected"
        self._connect_thread: Optional[threading.Thread] = None

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
                for f in bundled.glob("*.yaml"):
                    (presets_dir / f.name).write_bytes(f.read_bytes())
                # Also copy any .yml files
                for f in bundled.glob("*.yml"):
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
                    self._wait_if_paused()
                    if self._stop_event.is_set():
                        break

                    progress = (repeat_idx * total_blocks + block_idx) / total_work
                    self._emit_event({"type": "block_start", "index": block_idx, "repeat": repeat_idx, "block": block})

                    block_type = block.get("type")

                    if block_type == "pump":
                        self._run_pump_block(block.get("pumps", {}), "program")
                    elif block_type == "pause":
                        self._run_pause_block(float(block.get("s", 0.0)))
                    elif block_type == "rotate":
                        self._run_rotate(int(float(block["deg"])))

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

    def _emit_pumped(self, jobs: Dict[str, "tuple[float, float]"], elapsed: float, source: str) -> None:
        """Report how much each channel delivered (estimated pro-rata if stopped)."""
        for ch, (ml, s) in jobs.items():
            delivered = ml if elapsed >= s else ml * elapsed / s
            if delivered > 0:
                self._emit_event({"type": "pumped", "channel": ch, "ml": delivered, "source": source})

    def _run_pump_block(self, pumps: Dict[str, Any], source: str) -> None:
        """Pump every active channel at once with `hardware.run_step`, which
        blocks until the whole block is done. The pump runs at its own rate (the
        DilutionController uses 90 % of max flow). A block can't be paused (Pause takes effect at the next
        block); Stop cuts it short via `hardware.stop_all`. Emits a `pumped`
        event per channel, tagged with `source`."""
        jobs: Dict[str, "tuple[float, float]"] = {}  # ch -> (ml, estimated seconds)
        for ch, p in pumps.items():
            ml = float(p.get("ml", 0) or 0)
            if ml > 0:
                jobs[ch] = (ml, pump_seconds(ml))
        if not jobs or self._stop_event.is_set():
            return
        start = time.monotonic()
        hardware.run_step({ch: ml for ch, (ml, _) in jobs.items()})
        # Stopped: estimate what was delivered from the time it ran.
        elapsed = time.monotonic() - start if self._stop_event.is_set() else math.inf
        self._emit_pumped(jobs, elapsed, source)

    def _wait_if_paused(self) -> None:
        """Pause between blocks: blocks that can't pause themselves (pump,
        rotate) leave a pause request for here. Returns at once if not paused,
        or when resumed or stopped."""
        if self._stop_event.is_set():
            return
        if not self._pause_event.is_set():
            self._resume_event.clear()  # a pause+resume during the block is moot
            return
        self._emit_event({"type": "status", "status": "paused"})
        while not self._resume_event.is_set():
            if self._stop_event.is_set():
                return
            time.sleep(0.05)
        self._resume_event.clear()
        self._emit_event({"type": "status", "status": self._active_status})

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

    def _emit_rotator(self, state: str, message: Optional[str] = None, target: Optional[int] = None) -> None:
        """Record the rotator's state and tell the UI (with its current angle)."""
        self._rotator_state = state
        event: Dict[str, Any] = {"type": "rotator", "state": state, "deg": self._rotator.deg}
        if target is not None:
            event["target"] = target
        if message:
            event["message"] = message
        self._emit_event(event)

    def _rotator_unavailable(self) -> Optional[str]:
        """Why the rotator can't take a move now, or None (mirrors bridge.rotatorUnavailable)."""
        if self._rotator_state in ("disconnected", "error"):
            return "Cuvette rotator not connected"
        if self._rotator_state in ("homing", "moving"):
            return "Cuvette rotator is busy"
        return None

    def _run_rotate(self, deg: int, home_first: bool = False) -> None:
        """Turn the cuvette to `deg` and wait for the board. With `home_first`
        it re-grounds on the 0° switch before the move. The move can't be
        paused; Stop stops waiting (the board still finishes the move)."""
        try:
            if home_first:
                self._emit_rotator("homing")
                self._rotator.home(should_cancel=self._stop_event.is_set)
                if self._stop_event.is_set():
                    self._emit_rotator("ready")
                    return
            self._emit_rotator("moving", target=deg)
            self._rotator.move(deg, should_cancel=self._stop_event.is_set)
        except rotator.RotatorError as e:
            self._emit_rotator("ready" if self._rotator.connected else "disconnected", str(e))
            raise
        self._emit_rotator("ready")

    def rotator_connect(self) -> Dict[str, Any]:
        """Find the rotator board, open it and home it, in the background.
        Progress arrives as `rotator` events. Ignored while already connecting."""
        if self._connect_thread is not None and self._connect_thread.is_alive():
            return {"ok": True}
        if self._rotator_state == "moving":
            return {"ok": False, "error": "Cuvette rotator is busy"}

        def _connect() -> None:
            self._emit_rotator("homing")
            try:
                port = self._rotator.connect()
                self._emit_rotator("ready", f"Connected on {port}")
            except rotator.RotatorError as e:
                self._rotator.close()
                self._emit_rotator("disconnected", str(e))

        self._connect_thread = threading.Thread(target=_connect, daemon=True)
        self._connect_thread.start()
        return {"ok": True}

    def manual_rotate(self, deg: float) -> Dict[str, Any]:
        """Home the cuvette on its 0° switch, then turn it to `deg` (one click on
        the robot). Runs on the runner thread like manual_dose, so it is refused
        while anything else runs."""
        error = rotator.rotate_error(deg) or self._rotator_unavailable()
        if error:
            return {"ok": False, "error": error}
        deg = int(float(deg))
        with self._runner_lock:
            if self._busy():
                return {"ok": False, "error": "Busy"}

            self._stop_event.clear()
            self._pause_event.clear()
            self._resume_event.clear()

            def _rotate_impl() -> None:
                try:
                    self._active_status = "rotating"
                    self._emit_event({"type": "status", "status": "rotating"})
                    self._run_rotate(deg, home_first=True)
                    if not self._stop_event.is_set():
                        self._emit_event({"type": "status", "status": "ready"})
                except Exception as e:
                    self._emit_event({"type": "status", "status": "error", "message": str(e)})

            self._runner_thread = threading.Thread(target=_rotate_impl, daemon=True)
            self._runner_thread.start()

        return {"ok": True}

    def run_program(self, blocks: List[Dict[str, Any]], repeat: int) -> Dict[str, Any]:
        """Run a program with the given blocks and repetition count."""
        for block in blocks:
            block_type = block.get("type")
            if block_type == "pump":
                error = pumps_error(block.get("pumps", {}))
            elif block_type == "rotate":
                error = rotator.rotate_error(block.get("deg"))
            elif block_type == "pause":
                error = None
            else:
                error = f"Unknown block type {block_type!r}"
            if error:
                return {"ok": False, "error": error}
        if any(block.get("type") == "rotate" for block in blocks):
            error = self._rotator_unavailable()
            if error:
                return {"ok": False, "error": error}
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

    def _busy(self) -> bool:
        return self._runner_thread is not None and self._runner_thread.is_alive()

    def manual_dose(self, channel: str, ml: float) -> Dict[str, Any]:
        """Pump a fixed `ml` on one channel (one manual click).
        Runs on the runner thread, so it is refused while anything else runs and
        can be stopped with stop()."""
        ml = float(ml)
        if channel not in ("A", "B", "C", "D"):
            return {"ok": False, "error": f"Unknown channel {channel}"}
        if not ml > 0:
            return {"ok": False, "error": "Dose needs mL > 0"}
        with self._runner_lock:
            if self._busy():
                return {"ok": False, "error": "Busy"}

            self._stop_event.clear()
            self._pause_event.clear()
            self._resume_event.clear()

            def _dose_impl() -> None:
                try:
                    self._active_status = "dosing"
                    self._emit_event({"type": "status", "status": "dosing"})
                    self._emit_event({"type": "dose_start", "channel": channel, "ml": ml})
                    self._run_pump_block({channel: {"ml": ml}}, "manual")
                    if not self._stop_event.is_set():
                        self._emit_event({"type": "status", "status": "ready"})
                except Exception as e:
                    self._emit_event({"type": "status", "status": "error", "message": str(e)})
                    hardware.stop_all()

            self._runner_thread = threading.Thread(target=_dose_impl, daemon=True)
            self._runner_thread.start()

        return {"ok": True}

    def _tubing_result(self, tubing: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        sizes = set(tubing["mm"].values())
        return {
            "ok": True,
            "mm": sizes.pop() if len(sizes) == 1 else None,  # None: channels differ
            "min_mm": hardware.TUBING_MIN_MM,
            "max_mm": hardware.TUBING_MAX_MM,
            "max_ml_per_min": tubing["max_ml_per_min"],
        }

    def get_tubing(self) -> Dict[str, Any]:
        """Tubing inner diameter set on the pump, and the max flow it gives."""
        try:
            return self._tubing_result(hardware.get_tubing())
        except Exception as e:
            return {"ok": False, "error": f"Pump not available: {e}", "mm": None,
                    "min_mm": hardware.TUBING_MIN_MM, "max_mm": hardware.TUBING_MAX_MM,
                    "max_ml_per_min": {}}

    def set_tubing(self, mm: float) -> Dict[str, Any]:
        """Set the tubing inner diameter (mm, any value in range, rounded to
        0.01) of every pump channel."""
        error = None
        size = None
        try:
            if isinstance(mm, bool):
                raise ValueError
            size = round(float(mm), 2)
            if not (hardware.TUBING_MIN_MM <= size <= hardware.TUBING_MAX_MM):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            error = f"Tubing must be {hardware.TUBING_MIN_MM}-{hardware.TUBING_MAX_MM} mm"
        if error is None:
            if self._busy():
                error = "Busy"
            else:
                try:
                    return self._tubing_result(hardware.set_tubing(size))
                except Exception as e:
                    error = f"Pump not available: {e}"
        result = self.get_tubing()
        result.update(ok=False, error=error)
        return result

    def _pump_info(self, **extra: Any) -> Dict[str, Any]:
        return {**extra, **hardware.info()}

    def pump_connect(self) -> Dict[str, Any]:
        """(Re)detect the pump: the real one, else the mock. Refused while busy."""
        if self._busy():
            return self._pump_info(ok=False, error="Busy")
        try:
            return {"ok": True, **hardware.connect()}
        except Exception as e:
            return self._pump_info(ok=False, error=f"Pump not available: {e}")

    def set_pump_logging(self, on: bool) -> Dict[str, Any]:
        """Turn the ismatec library's debug logging on or off."""
        try:
            hardware.set_logging(bool(on))
            return {"ok": True, **hardware.info()}
        except Exception as e:
            return self._pump_info(ok=False, error=str(e))

    def list_presets(self) -> List[str]:
        """List all available presets."""
        presets_dir = self._get_presets_dir()
        presets = []
        # List both .yaml and .yml files
        for preset_file in list(presets_dir.glob("*.yaml")) + list(presets_dir.glob("*.yml")):
            try:
                with open(preset_file) as f:
                    preset_data = yaml.safe_load(f)
                    if preset_data and "name" in preset_data:
                        presets.append(preset_data["name"])
            except Exception:
                pass
        return sorted(presets)

    def load_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a preset by name."""
        presets_dir = self._get_presets_dir()
        # Search both .yaml and .yml files
        for preset_file in list(presets_dir.glob("*.yaml")) + list(presets_dir.glob("*.yml")):
            try:
                with open(preset_file) as f:
                    preset_data = yaml.safe_load(f)
                    if preset_data and preset_data.get("name") == name:
                        return preset_data
            except Exception:
                pass
        return None

    def save_preset(self, preset: Dict[str, Any]) -> Dict[str, Any]:
        """Save a preset to disk."""
        try:
            name = preset.get("name", "preset")
            presets_dir = self._get_presets_dir()
            filename = self._sanitize_filename(name) + ".yaml"
            preset_path = presets_dir / filename

            with open(preset_path, "w") as f:
                yaml.safe_dump(preset, f, sort_keys=False, default_flow_style=False)
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
                file_types=("YAML files (*.yaml *.yml)", "All files (*.*)",),
            )
            if file_result:
                file_path = file_result[0]
                with open(file_path) as f:
                    return yaml.safe_load(f)
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
                file_types=("YAML files (*.yaml)",),
                save_filename=self._sanitize_filename(name) + ".yaml",
            )
            if isinstance(file_path, (list, tuple)):
                file_path = file_path[0] if file_path else None
            if file_path:
                with open(file_path, "w") as f:
                    yaml.safe_dump(preset, f, sort_keys=False, default_flow_style=False)
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "No file selected"}
