"""
USB-serial driver for the cuvette rotator (Adafruit Trinket M0 running
firmware/cuvette_rotator/code.py).

Protocol (newline-terminated, replies are `<...>` lines):
  HOME   -> <STATUS:HOMING> ... <STATUS:READY>
  <int>  -> <STATUS:MOVING_TO_<deg>.0> ... <STATUS:MOVE_COMPLETE>   (0-60)
  <int> outside 0-60 -> <ERROR:OUT_OF_BOUNDS>

A move can't be interrupted: the board finishes it even if we stop waiting, and
its late reply may still be in the pipe when we send the next command. So each
command first waits for its own start line (HOMING / MOVING_TO_x) and only then
accepts the matching done line; anything before that is a stale reply.
"""

import threading
import time
from typing import Callable, Optional

import serial
from serial.tools import list_ports

VID, PID = 0x239A, 0x801F  # Adafruit Trinket M0
PRODUCT = "Trinket M0"
MAX_DEG = 60
BAUD = 115200  # ignored by USB CDC, but pyserial needs one
HOME_TIMEOUT_S = 60.0  # worst case: a full sweep to find the switch
MOVE_TIMEOUT_S = 20.0


class RotatorError(Exception):
    pass


def rotate_error(deg) -> Optional[str]:
    """Why a rotate angle is invalid, or None if it is fine (mirrors flow.rotateError)."""
    try:
        d = float(deg)
    except (TypeError, ValueError):
        d = float("nan")
    if not d.is_integer() or not 0 <= d <= MAX_DEG:
        return f"Rotate: angle must be a whole number 0-{MAX_DEG}°"
    return None


def find_port() -> Optional[str]:
    """Serial port of the rotator board, by USB VID/PID (or product name), or None."""
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == VID and p.pid == PID:
            return p.device
    for p in ports:
        if p.product == PRODUCT:
            return p.device
    return None


class Rotator:
    """One rotator board. All commands are serialized by a lock."""

    def __init__(self) -> None:
        self._ser = None
        self._buf = b""
        self._lock = threading.Lock()
        self.deg: Optional[int] = None  # current angle, None until homed

    @property
    def connected(self) -> bool:
        return self._ser is not None

    def connect(self, should_cancel: Callable[[], bool] = lambda: False) -> str:
        """Find and open the board, then home it. Returns the port name."""
        port = find_port()
        if port is None:
            raise RotatorError("Cuvette rotator not found (is the Trinket M0 plugged in?)")
        self.close()
        try:
            self._ser = serial.Serial(port, BAUD, timeout=0.1)
        except serial.SerialException as e:
            raise RotatorError(f"Can't open {port}: {e}")
        self._buf = b""
        self.home(should_cancel)
        return port

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.deg = None

    def home(self, should_cancel: Callable[[], bool] = lambda: False) -> None:
        self.deg = None
        if self._command("HOME", "<STATUS:HOMING>", "<STATUS:READY>", HOME_TIMEOUT_S, should_cancel):
            self.deg = 0

    def move(self, deg: int, should_cancel: Callable[[], bool] = lambda: False) -> None:
        """Turn to `deg` (0-60) and wait until the board reports it is there.
        If `should_cancel` returns True we stop waiting; the board still finishes."""
        error = rotate_error(deg)
        if error:
            raise RotatorError(error)
        deg = int(float(deg))
        self._command(str(deg), f"<STATUS:MOVING_TO_{float(deg)}>", "<STATUS:MOVE_COMPLETE>", MOVE_TIMEOUT_S, should_cancel)
        self.deg = deg

    def _command(self, cmd: str, start: str, done: str, timeout: float, should_cancel: Callable[[], bool]) -> bool:
        """Send `cmd`, wait for `start` then `done`. False if cancelled."""
        with self._lock:
            if self._ser is None:
                raise RotatorError("Cuvette rotator not connected")
            try:
                self._ser.write((cmd + "\r\n").encode())
                started = False
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if should_cancel():
                        return False
                    line = self._readline()
                    if line is None:
                        continue
                    if line == start:
                        started = True
                    elif started and line == done:
                        return True
                    elif started and line.startswith("<ERROR"):
                        raise RotatorError(f"Rotator: {line}")
                    elif line == "<ERROR:OUT_OF_BOUNDS>":
                        # A move we validated never gets this, so it is ours
                        raise RotatorError(f"Rotator: {line}")
                raise RotatorError(f"Rotator didn't answer {cmd!r} in {timeout:.0f} s")
            except (serial.SerialException, OSError) as e:
                self.close()
                raise RotatorError(f"Rotator disconnected: {e}")

    def _readline(self) -> Optional[str]:
        """One complete line from the board, or None if none arrived yet (partial lines are kept)."""
        if b"\n" not in self._buf:
            self._buf += self._ser.read(self._ser.in_waiting or 1)
        if b"\n" not in self._buf:
            return None
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode(errors="replace").strip()
