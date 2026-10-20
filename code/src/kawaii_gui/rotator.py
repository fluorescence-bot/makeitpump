"""
USB-serial driver for the cuvette rotator (Arduino Nano running
../cuvette_rotater/cuvette_rotater.ino).

Protocol (newline-terminated, replies are `<...>` lines):
  (boot) -> <STATUS:BOOTED>          the Nano restarts when the port is opened
  PING   -> <STATUS:PONG>
  HOME   -> <STATUS:HOMING> ... <STATUS:READY>   (or <ERROR:...>)
  <int>  -> <STATUS:MOVING_TO_<deg>.0> ... <STATUS:MOVE_COMPLETE>   (0-60, or <ERROR:...>)
  <int> outside 0-60 -> <ERROR:OUT_OF_BOUNDS>

Several serial ports can look like an Arduino (the pump can be a USB-serial
adapter too), so connect() only uses a port once it has said BOOTED or PONG.

A move can't be interrupted: the board finishes it even if we stop waiting, and
its late reply may still be in the pipe when we send the next command. So each
command first waits for its own start line (HOMING / MOVING_TO_x) and only then
accepts the matching done line; anything before that is a stale reply.
"""

import threading
import time
from typing import Callable, List, Optional

import serial
from serial.tools import list_ports

# USB IDs a Nano (or clone) can show up with: (VID, PID), None = any PID
USB_IDS = [
    (0x1A86, 0x7523),  # CH340 (most Nano clones)
    (0x1A86, 0x55D4),  # CH9102
    (0x0403, 0x6001),  # FTDI FT232R (older Nanos)
    (0x2341, None),    # Arduino
    (0x2A03, None),    # Arduino.org
]
MAX_DEG = 60
BAUD = 115200  # must match Serial.begin() in the firmware
BOOT_TIMEOUT_S = 3.0  # the Nano restarts when the port opens and says BOOTED
PING_TIMEOUT_S = 1.0  # a board that didn't restart gets a PING instead
HOME_TIMEOUT_S = 60.0  # worst case: a full turn to find the switch
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


def candidate_ports() -> List[str]:
    """Serial ports whose USB ID could be the rotator's Nano, in order."""
    return [p.device for p in list_ports.comports()
            if any(p.vid == vid and (pid is None or p.pid == pid) for vid, pid in USB_IDS)]


def find_port() -> Optional[str]:
    """First serial port that could be the rotator, by USB ID, or None (not yet confirmed)."""
    ports = candidate_ports()
    return ports[0] if ports else None


class Rotator:
    """One rotator board. All commands are serialized by a lock."""

    def __init__(self) -> None:
        self._ser = None
        self._buf = b""
        self._lock = threading.Lock()
        self.deg: Optional[int] = None  # current angle, None until homed
        self.port: Optional[str] = None  # the open port, None when closed

    @property
    def connected(self) -> bool:
        return self._ser is not None

    def connect(self, port: Optional[str] = None,
                should_cancel: Callable[[], bool] = lambda: False) -> str:
        """Open the board on `port` (None: try the ports found by USB ID), then
        home it. Returns the port name."""
        ports = [port] if port else candidate_ports()
        if not ports:
            raise RotatorError("Cuvette rotator not found (is the Arduino Nano plugged in?)")
        self.close()
        for port in ports:
            if should_cancel():
                break
            try:
                self._ser = serial.Serial(port, BAUD, timeout=0.1)
            except (serial.SerialException, OSError):
                continue
            self._buf = b""
            if self._handshake():
                self.port = port
                self.home(should_cancel)
                return port
            self.close()
        raise RotatorError(f"Cuvette rotator not found: no answer on {', '.join(ports)}")

    def _handshake(self) -> bool:
        """True once the board on the open port says BOOTED (it restarts when the
        port opens) or, if it didn't restart, answers a PING."""
        try:
            deadline = time.monotonic() + BOOT_TIMEOUT_S
            pinged = False
            while True:
                if time.monotonic() >= deadline:
                    if pinged:
                        return False
                    self._ser.write(b"PING\r\n")
                    pinged = True
                    deadline = time.monotonic() + PING_TIMEOUT_S
                line = self._readline()
                if line in ("<STATUS:BOOTED>", "<STATUS:PONG>"):
                    return True
        except (serial.SerialException, OSError):
            return False

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.port = None
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
