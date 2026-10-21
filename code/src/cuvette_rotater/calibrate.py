"""
Calibration window for the cuvette rotator: home the motor, nudge it by degrees or
single microsteps until the cuvette sits where 0° should be, and read off the
BACKOFF_STEPS value to put in cuvette_rotater.ino.

Run with any Python that has tkinter and pyserial, e.g. from this folder:
    ../kawaii_gui/.venv/bin/python calibrate.py
Close RainMixer / the Reglo GUI first: only one program can use the port.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import serial
from serial.tools import list_ports

BAUD = 115200
STEPS_PER_REV = 3200          # must match the firmware (200 full steps x 16)
DEG_PER_STEP = 360 / STEPS_PER_REV
FIRMWARE_BACKOFF_STEPS = 139  # BACKOFF_STEPS in cuvette_rotater.ino (current 0°)
USB_IDS = [(0x1A86, 0x7523), (0x1A86, 0x55D4), (0x0403, 0x6001), (0x2341, None), (0x2A03, None)]


def find_port():
    for p in list_ports.comports():
        if any(p.vid == vid and (pid is None or p.pid == pid) for vid, pid in USB_IDS):
            return p.device
    return None


class Board:
    """Serial link to the Nano. send() blocks until the reply line that ends the command."""

    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=0.2)
        self._wait(("<STATUS:BOOTED>",), 5)   # the Nano restarts when the port opens

    def _wait(self, ends, timeout, stop_on_error=True):
        """The first line starting with one of `ends` (or an error line), or None.
        Everything read is kept in self.last for the caller."""
        self.last, deadline = "", time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.last += self.ser.read(200).decode(errors="replace")
            for line in self.last.splitlines():
                if (stop_on_error and line.startswith("<ERROR")) or any(line.startswith(e) for e in ends):
                    return line.strip()
        return None

    def send(self, cmd, ends, timeout=30, stop_on_error=True):
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        return self._wait(ends, timeout, stop_on_error)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cuvette rotator calibration")
        self.board = None
        self.step = None                 # steps from the firmware's current 0, None = not homed
        self.jobs = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        pad = {"padx": 6, "pady": 4}
        self.status = tk.StringVar(value="Connecting...")
        self.pos = tk.StringVar(value="Position: not homed")
        self.result = tk.StringVar(value="")

        ttk.Label(self, textvariable=self.status).grid(row=0, column=0, columnspan=6, sticky="w", **pad)
        ttk.Label(self, textvariable=self.pos, font=("Helvetica", 16, "bold")).grid(
            row=1, column=0, columnspan=6, sticky="w", **pad)

        ttk.Button(self, text="Home", command=self.home).grid(row=2, column=0, **pad)
        ttk.Label(self, text="towards switch (clockwise)  <  >  away from switch (counter-clockwise)").grid(
            row=2, column=1, columnspan=5, sticky="w", **pad)

        ttk.Label(self, text="Degrees:").grid(row=3, column=0, sticky="e", **pad)
        for i, d in enumerate((-1, -0.5, 0.5, 1, 5)):
            ttk.Button(self, text=f"{d:+g}°", width=6, command=lambda d=d: self.jog_deg(d)).grid(
                row=3, column=1 + i, **pad)

        ttk.Label(self, text="Steps:").grid(row=4, column=0, sticky="e", **pad)
        for i, n in enumerate((-10, -1, 1, 10, 50)):
            ttk.Button(self, text=f"{n:+d}", width=6, command=lambda n=n: self.jog_steps(n)).grid(
                row=4, column=1 + i, **pad)

        ttk.Label(self, text="Custom (°):").grid(row=5, column=0, sticky="e", **pad)
        self.custom = ttk.Entry(self, width=8)
        self.custom.grid(row=5, column=1, **pad)
        ttk.Button(self, text="Move", command=self.jog_custom).grid(row=5, column=2, **pad)

        ttk.Button(self, text="Use this as 0°", command=self.use_as_zero).grid(
            row=6, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(self, textvariable=self.result, foreground="#15803d", wraplength=520,
                  justify="left").grid(row=7, column=0, columnspan=6, sticky="w", **pad)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.run(self._connect)

    # -- background serial work (keeps the window responsive) ---------------
    def run(self, fn):
        self.jobs.put(fn)

    def _worker(self):
        while True:
            fn = self.jobs.get()
            try:
                fn()
            except Exception as e:  # show any serial problem in the window
                self.after(0, self.status.set, f"Error: {e}")

    def _connect(self):
        port = find_port()
        if port is None:
            self.after(0, self.status.set, "Nano not found. Plug it in and restart this window.")
            return
        self.board = Board(port)
        self.after(0, self.status.set, f"Connected on {port}. Press Home.")

    def _require_board(self):
        if self.board is None:
            self.after(0, self.status.set, "Not connected.")
            return False
        return True

    # -- buttons ---------------------------------------------------------------
    def home(self):
        def job():
            if not self._require_board():
                return
            self.after(0, self.status.set, "Homing...")
            reply = self.board.send("HOME", ("<STATUS:READY>",), 30)
            if reply == "<STATUS:READY>":
                self.step = 0
                self.after(0, self.status.set, "Homed. Nudge it to where 0° should be.")
            else:
                self.step = None
                self.after(0, self.status.set, f"Homing failed: {reply}")
            self.after(0, self._show)
        self.run(job)

    def jog_steps(self, n):
        self._jog(n * DEG_PER_STEP)

    def jog_deg(self, d):
        self._jog(d)

    def jog_custom(self):
        try:
            self._jog(float(self.custom.get().replace(",", ".")))
        except ValueError:
            self.status.set("Custom move: type a number of degrees, e.g. 2.5 or -1")

    def _jog(self, deg):
        if self.step is None:
            self.status.set("Press Home first.")
            return

        def job():
            # A jog that hits the switch sends <ERROR:LIMIT_HIT> and then still
            # reports where it stopped, so wait for the position line either way.
            reply = self.board.send(f"JOG {deg:.4f}", ("<STATUS:AT_",), 10, stop_on_error=False)
            if reply and "_STEP_" in reply:
                self.step = int(reply.rstrip(">").split("_STEP_")[1])
                hit = "LIMIT_HIT" in self.board.last
                self.after(0, self.status.set, "Stopped: the switch was pressed. Move the other way."
                           if hit else "Moved.")
            else:
                self.after(0, self.status.set, f"No answer: {reply}")
            self.after(0, self._show)
        self.run(job)

    def _show(self):
        if self.step is None:
            self.pos.set("Position: not homed")
            return
        self.pos.set(f"Position: {self.step * DEG_PER_STEP:+.2f}° from current 0  "
                     f"({self.step:+d} steps)")

    def use_as_zero(self):
        if self.step is None:
            self.status.set("Press Home first.")
            return
        backoff = FIRMWARE_BACKOFF_STEPS + self.step
        text = (f"New 0° = {backoff} steps ({backoff * DEG_PER_STEP:.2f}°) back from the switch.\n"
                f"In cuvette_rotater.ino: const long BACKOFF_STEPS = {backoff};")
        self.result.set(text)
        print(text, flush=True)

    def on_close(self):
        if self.board is not None:
            try:
                self.board.ser.close()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
