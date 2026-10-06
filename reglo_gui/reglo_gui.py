"""Reglo ICC pump control GUI, using DilutionController (same as the mock script)."""

import asyncio
import logging
import math
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import serial  # pip install pyserial

from dilution_controller import DilutionController

DEBUGGING = True

if DEBUGGING:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('ismatec').setLevel(logging.DEBUG)
    from ismatec.mock import Pump
else:
    from ismatec import Pump

# ---- Configuration ---------------------------------------------------------
COLUMN_HEADERS = ["1", "2", "3"]   # volume columns = pump channels 1-3
# Channel 4 is the output channel: its volume is one constant per preset
# (enough to flush the cuvette), not part of each step.

# Step types. "in" pumps channels 1-3 and then waits the preset's mixing
# pause; "out" pumps channel 4 with the preset's output volume; "wait" just
# waits a number of seconds (async, so the GUI stays usable meanwhile).
STEP_TYPES = {"in": "Pump in", "out": "Pump out", "wait": "Wait"}
STEP_KEYS = {label: key for key, label in STEP_TYPES.items()}

# ---- Pump configuration ------------------------------------------------------
# Use COMx on Windows. NOTE: must be a different port than the Arduino arm.
PUMP_ADDRESS = "/dev/ttyACM0"

# ---- Arduino arm configuration ----------------------------------------------
SERIAL_PORT = "/dev/ttyACM1"
SERIAL_BAUD = 9600        # must match Serial.begin(...) in the Arduino sketch
ARM_BOOT_DELAY = 2.0      # Uno resets when the port opens; wait for it to boot
ARM_TIMEOUT = 60.0        # max seconds to wait for the arm to finish a command
ARM_DONE_REPLY = None     # text the Arduino sends when finished, e.g. "DONE".
                          # None = any non-empty line counts as "done".
ARM_MIN_DEG, ARM_MAX_DEG = 0, 60
PRESETS = {                              # stub data; replace with file/db later
    "preset1": {"rotation": 45, "ch4_volume": "0.005", "mix_pause": "5",
                "rows": [{"type": "in", "volumes": ["0.001", "0.002", "0"]},
                         {"type": "out"}]},
    "preset2": {"rotation": 20, "ch4_volume": "0.010", "mix_pause": "10",
                "rows": [{"type": "in", "volumes": ["0.002", "0.005", "0"]},
                         {"type": "wait", "seconds": "30"},
                         {"type": "out"},
                         {"type": "in", "volumes": ["0.001", "0.002", "0"]},
                         {"type": "out"}]},
}


_NUMBER = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)$")


def is_number(text):
    """True for plain numbers like '3', '0.001' or '0,001' (never raises)."""
    return bool(_NUMBER.match(text.strip()))


def to_float(text):
    """Parse a number, accepting both '0.001' and '0,001'."""
    return float(text.strip().replace(",", "."))


def is_duration(text):
    """True for a non-negative number of seconds."""
    return is_number(text) and to_float(text) >= 0


def to_degrees(text):
    """Arm rotation as an int, or None if invalid. Whole numbers only
    ('30' or '30.0'), within ARM_MIN_DEG-ARM_MAX_DEG."""
    if not is_number(text):
        return None
    value = to_float(text)
    if not value.is_integer() or not ARM_MIN_DEG <= value <= ARM_MAX_DEG:
        return None
    return int(value)


class ArmError(Exception):
    pass


class ArmController:
    """Talks to the Arduino over serial. Commands: 'HOME' or an int 0-60.
    Each command is sent as one line and we wait for the Arduino's reply
    line before returning (blocking: call from a worker thread)."""

    def __init__(self, port, baud, done_reply, timeout):
        self.port, self.baud = port, baud
        self.done_reply, self.timeout = done_reply, timeout
        self.ser = None

    def connect(self):
        if self.ser is not None and self.ser.is_open:
            return
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.5)
        except serial.SerialException as exc:
            self.ser = None
            raise ArmError(f"Could not open {self.port}:\n{exc}")
        time.sleep(ARM_BOOT_DELAY)

    def _command(self, cmd):
        try:
            self.ser.reset_input_buffer()           # drop stale output
            self.ser.write(f"{cmd}\n".encode("ascii"))
            self.ser.flush()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                line = self.ser.readline().decode("ascii", "ignore").strip()
                if not line:
                    continue
                if (self.done_reply is None
                        or self.done_reply.upper() in line.upper()):
                    return
        except serial.SerialException as exc:
            self.close()
            raise ArmError(f"Serial error while sending '{cmd}':\n{exc}")
        raise ArmError(f"No reply from the Arduino to '{cmd}' "
                       f"within {self.timeout:.0f} s.")

    def home(self):
        self._command("HOME")

    def rotate(self, degrees):
        if not ARM_MIN_DEG <= degrees <= ARM_MAX_DEG:
            raise ArmError(f"Rotation {degrees} is outside "
                           f"{ARM_MIN_DEG}-{ARM_MAX_DEG} degrees.")
        self._command(str(degrees))

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None


class ArmMock:
    """Stand-in for the arm when DEBUGGING: does nothing, needs no hardware."""

    def connect(self):
        print("[arm mock] connect (skipped)")

    def home(self):
        print("[arm mock] HOME (skipped)")

    def rotate(self, degrees):
        print(f"[arm mock] rotate to {degrees} deg (skipped)")

    def close(self):
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Reglo ICC Pump Control")
        self.minsize(720, 560)

        # Pump side: DilutionController is async, so it lives on its own
        # asyncio event loop running in a background thread. The Pump and
        # controller are created inside that loop (see _init_hardware).
        self.pump = None
        self.controller = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        if DEBUGGING:
            self.arm = ArmMock()
        else:
            self.arm = ArmController(SERIAL_PORT, SERIAL_BAUD,
                                     ARM_DONE_REPLY, ARM_TIMEOUT)
        self.protocol("WM_DELETE_WINDOW", self.on_exit)
        self.bind_all("<Button-1>", self._on_click_anywhere, add="+")

        self.current_preset = tk.StringVar(value="preset1")
        self.chosen_preset = tk.StringVar(value="preset1")
        self.rotation = tk.StringVar(value="45")
        self.status = tk.StringVar(value="Not initialized")
        self.current_step = 0   # row that will run next; rows above are done
        self.busy = False       # initializing or running a step
        self.waiting = False    # busy, but only waiting (wait step / mixing):
                                # upcoming rows stay editable meanwhile
        self._wait_end = None   # monotonic time the current wait ends
        self.auto_run = False   # "Run all": start the next step automatically
        self.stopping = False   # Stop pressed; the running step still finishes
        self.initialized = False
        self.duplicate_next = tk.BooleanVar(value=False)
        self.cells = []        # cells[row][col] -> Entry (channels 1-3)
        self.wait_cells = []   # wait_cells[row] -> Entry (seconds)
        self.type_vars = []    # type_vars[row] -> StringVar ("Pump in", ...)
        self.type_boxes = []   # type_boxes[row] -> Combobox
        self.row_buttons = []  # row labels (#1, #2, ...)
        self.delete_btns = []  # delete_btns[row] -> trashcan Button
        self.row_widgets = []  # row_widgets[row] -> every widget in that row

        self._build_header()
        self._build_steps()
        self._build_footer()
        self._load_preset("preset1")
        self._refresh_rows()

    # ---- async bridge ------------------------------------------------------
    def _submit(self, coro, on_ok, on_err):
        """Run a coroutine on the asyncio thread; call on_ok(result) or
        on_err(exception) back on the GUI thread when it finishes."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        self._poll_future(fut, on_ok, on_err)

    def _poll_future(self, fut, on_ok, on_err):
        if not fut.done():
            self.after(100, self._poll_future, fut, on_ok, on_err)
            return
        try:
            result = fut.result()
        except Exception as exc:
            on_err(exc)
        else:
            on_ok(result)

    # ---- layout ------------------------------------------------------------
    def _build_header(self):
        header = ttk.Frame(self, padding=10)
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)

        left = ttk.Frame(header)
        left.grid(row=0, column=0, rowspan=2, sticky="nw")
        self.current_label = ttk.Label(left, text="Current: preset1",
                                       font=("TkDefaultFont", 12, "bold"))
        self.current_label.pack(anchor="w")
        consts = ttk.Frame(left)
        consts.pack(anchor="w", pady=(4, 0))
        self.ch4_var = tk.StringVar(value="0")
        self.mix_var = tk.StringVar(value="0")
        entries = []
        for r, (text, var) in enumerate(
                [(f"Rotation (\u00b0, {ARM_MIN_DEG}-{ARM_MAX_DEG}):",
                  self.rotation),
                 ("Ch 4 output volume (ml):", self.ch4_var),
                 ("Mixing pause (s):", self.mix_var)]):
            ttk.Label(consts, text=text).grid(row=r, column=0, sticky="w")
            e = ttk.Entry(consts, textvariable=var, width=8, justify="center")
            e.grid(row=r, column=1, padx=(6, 0), pady=(0, 2))
            e.bind("<KeyRelease>", lambda ev: self._update_run_state())
            entries.append(e)
        self.rotation_entry, self.ch4_entry, self.mix_entry = entries

        right = ttk.Frame(header)
        right.grid(row=0, column=1, sticky="ne")
        self.preset_box = ttk.Combobox(right, textvariable=self.chosen_preset,
                                       values=list(PRESETS), state="readonly",
                                       width=18)
        self.preset_box.pack(side="left")
        self.ok_btn = ttk.Button(right, text="OK", width=4,
                                 command=self.on_choose_preset)
        self.ok_btn.pack(side="left", padx=(6, 0))

        self.save_btn = ttk.Button(header, text="Save changes",
                                   command=self.on_save)
        self.save_btn.grid(row=1, column=1, sticky="ne", pady=(8, 0))

    def _build_steps(self):
        body = ttk.Frame(self, padding=(10, 10, 10, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(body, text="Steps",
                  font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0,
                                                          sticky="w", pady=(0, 4))

        table_frame = ttk.Frame(body, relief="sunken", borderwidth=1)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(table_frame, highlightthickness=0)
        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self.grid_frame = ttk.Frame(self.canvas)
        win = self.canvas.create_window((0, 0), window=self.grid_frame,
                                        anchor="nw")
        self.grid_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self._bind_mousewheel()

        # columns: 0 = row label, 1 = type, 2.. = channels, then wait (s);
        # the delete buttons go in one more column after these
        headers = ["", "Type"] + COLUMN_HEADERS + ["Wait (s)"]
        for c, name in enumerate(headers):
            ttk.Label(self.grid_frame, text=name, anchor="center",
                      font=("TkDefaultFont", 10, "bold")).grid(
                row=0, column=c, sticky="ew", padx=1, pady=1)
            if c >= 2:
                self.grid_frame.columnconfigure(c, weight=1, uniform="col")

        self._num_cols = len(headers)

        # One row per step; this bar sits under the last row and adds steps.
        self.add_bar = ttk.Frame(self.grid_frame)
        ttk.Label(self.add_bar, text="Add row:").pack(side="left", padx=(0, 6))
        for text, kind in [("Pause", "wait"), ("Output", "out"),
                           ("Input", "in")]:
            ttk.Button(self.add_bar, text=text, width=9,
                       command=lambda k=kind: self.on_add_step(k)).pack(
                side="left", padx=(0, 4))
        self._place_add_bar()

        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.grid(row=1, column=1, sticky="n")
        self.init_btn = ttk.Button(side, text="Initialize", width=16,
                                   command=self.on_initialize)
        self.init_btn.pack(pady=(0, 10), ipady=6)
        self.run_btn = ttk.Button(side, text="Run cur. step", width=16,
                                  command=self.on_run_step, state="disabled")
        self.run_btn.pack(ipady=6)
        self.run_all_btn = ttk.Button(side, text="Run all", width=16,
                                      command=self.on_run_all, state="disabled")
        self.run_all_btn.pack(pady=(10, 0), ipady=6)
        self.stop_btn = ttk.Button(side, text="Stop after step", width=16,
                                   command=self.on_stop, state="disabled")
        self.stop_btn.pack(pady=(4, 0), ipady=6)
        ttk.Checkbutton(side, text="Duplicate current row\nto next row",
                        variable=self.duplicate_next).pack(pady=(10, 0),
                                                           anchor="w")

    def _add_row(self, kind, data=None):
        """Append a step row of type `kind`, filled from a PRESETS row dict."""
        data = data or {}
        r = len(self.row_buttons)
        grid_row = r + 1                    # row 0 holds the column headers

        def make_entry(c, value):
            var = tk.StringVar(value=value)
            var.trace_add("write", lambda *_: self._update_run_state())
            e = tk.Entry(self.grid_frame, justify="center", width=10,
                         relief="solid", borderwidth=1, textvariable=var)
            e.var = var   # keep a reference, or Tk drops the trace
            e.grid(row=grid_row, column=c, sticky="ew", padx=1, pady=1, ipady=3)
            return e

        lbl = ttk.Label(self.grid_frame, text=f"#{r + 1}", width=6, anchor="w")
        lbl.grid(row=grid_row, column=0, padx=(2, 4), pady=1, sticky="ew")
        self.row_buttons.append(lbl)

        type_var = tk.StringVar(value=STEP_TYPES[kind])
        box = ttk.Combobox(self.grid_frame, textvariable=type_var,
                           values=list(STEP_TYPES.values()),
                           state="readonly", width=9)
        box.grid(row=grid_row, column=1, sticky="ew", padx=1, pady=1)
        box.bind("<<ComboboxSelected>>", lambda ev: self._on_type_changed(r))
        self.type_vars.append(type_var)
        self.type_boxes.append(box)

        volumes = data.get("volumes", [])
        row_entries = []
        for c in range(len(COLUMN_HEADERS)):
            e = make_entry(c + 2, volumes[c] if c < len(volumes) else "")
            e.bind("<FocusOut>", lambda ev: self.after_idle(
                self._normalize_if_left, r))
            row_entries.append(e)
        self.cells.append(row_entries)
        wait = make_entry(self._num_cols - 1, data.get("seconds", ""))
        self.wait_cells.append(wait)
        delete = ttk.Button(self.grid_frame, text="\U0001F5D1", width=3,
                            command=lambda: self.on_delete_row(r))
        delete.grid(row=grid_row, column=self._num_cols, padx=(4, 2), pady=1)
        self.delete_btns.append(delete)
        self.row_widgets.append([lbl, box, *row_entries, wait, delete])
        self._place_add_bar()

    def _clear_rows(self):
        for widgets in self.row_widgets:
            for w in widgets:
                w.destroy()
        for rows in (self.row_buttons, self.row_widgets, self.type_vars,
                     self.type_boxes, self.cells, self.wait_cells,
                     self.delete_btns):
            rows.clear()

    def _place_add_bar(self):
        self.add_bar.grid(row=len(self.row_buttons) + 1, column=0,
                          columnspan=self._num_cols, sticky="w",
                          padx=2, pady=(6, 4))

    def _build_footer(self):
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Button(footer, text="Exit", width=14,
                   command=self.on_exit).pack(side="right")

    def _on_click_anywhere(self, event):
        """Clicking anything but a text field leaves the current field (Tk
        only moves focus on clicks into other fields). The typed value stays."""
        w = event.widget
        if isinstance(w, str):          # combobox drop-down list: leave alone
            return
        if isinstance(w, (tk.Entry, ttk.Entry)):    # incl. ttk.Combobox
            return
        self.focus_set()

    def _bind_mousewheel(self):
        def on_wheel(event):
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")
            else:
                self.canvas.yview_scroll(int(-event.delta / 120) or
                                         (-1 if event.delta > 0 else 1), "units")
        self.canvas.bind_all("<MouseWheel>", on_wheel)   # Windows / macOS
        self.canvas.bind_all("<Button-4>", on_wheel)     # Linux
        self.canvas.bind_all("<Button-5>", on_wheel)

    # ---- helpers -----------------------------------------------------------
    def _refresh_rows(self):
        """Done rows: locked + greyed. Current row: highlighted. Rest: editable.
        Fields that don't apply to a row's type are greyed out. While a step
        pumps everything is locked; while it only waits (wait step / mixing
        pause) the upcoming rows stay editable."""
        for i, lbl in enumerate(self.row_buttons):
            if i < self.current_step:
                text, bg = f"✓ #{i + 1}", "#e0e0e0"
            elif i == self.current_step:
                text, bg = f"▶ #{i + 1}", "#fff6c8"
            else:
                text, bg = f"#{i + 1}", "white"
            editable = self._row_editable(i)
            lbl.configure(text=text)
            delete = self.delete_btns[i]
            if i < self.current_step:
                delete.grid_remove()        # steps that have run stay
            else:
                delete.grid()
                delete.configure(state="normal" if editable else "disabled")
            kind = self._row_type(i)
            self.type_boxes[i].configure(
                state="readonly" if editable else "disabled")
            for e, applies in ([(e, kind == "in") for e in self.cells[i]]
                               + [(self.wait_cells[i], kind == "wait")]):
                cell_bg = bg if applies else "#c8c8c8"
                e.configure(state="normal" if editable and applies else "disabled",
                            bg=cell_bg, disabledbackground=cell_bg,
                            disabledforeground="#555555")

    def _row_editable(self, i):
        """Rows that haven't run can be changed, except while a step pumps
        (then none) or waits (then all but the running one)."""
        if i < self.current_step:
            return False
        return not (self.busy and (i == self.current_step or not self.waiting))

    def _on_type_changed(self, index):
        """Clear the fields that don't apply to the row's new type."""
        kind = self._row_type(index)
        if kind != "in":
            for e in self.cells[index]:
                e.var.set("")
        if kind != "wait":
            self.wait_cells[index].var.set("")
        self._refresh_rows()
        self._update_run_state()

    def _fill_blanks(self, index):
        """Empty fields in a pump-in row become 0.0."""
        if self._row_type(index) != "in":
            return
        for e in self.cells[index]:
            if str(e.cget("state")) == "normal" and not e.get().strip():
                e.var.set("0.0")

    def _normalize_if_left(self, index):
        if self.focus_get() in self.cells[index]:
            return
        if not self._step_is_empty(index):
            self._fill_blanks(index)

    def _update_run_state(self):
        if not hasattr(self, "stop_btn"):
            return
        ok = self._can_run_current()
        for w in (self.run_btn, self.run_all_btn):
            w.configure(state="normal" if ok and not self.auto_run
                        else "disabled")
        self.stop_btn.configure(state="normal" if self.auto_run else "disabled")

    def _can_run_current(self):
        return (self.initialized and not self.busy
                and self.current_step < self._num_steps()
                and not self._step_is_empty(self.current_step)
                and self._row_is_complete(self.current_step))

    def _row_is_complete(self, index):
        """True when the row (and the preset constant it uses) is valid."""
        kind = self._row_type(index)
        if kind == "in":
            return (all(is_number(v) for v in self._volume_values(index))
                    and is_duration(self.mix_var.get()))
        if kind == "out":
            return is_number(self.ch4_var.get())
        return is_duration(self.wait_cells[index].get())

    def _step_is_empty(self, index):
        """Only a pump-in row with no volumes counts as empty; pump-out and
        wait rows are steps as soon as their type is chosen."""
        return (self._row_type(index) == "in"
                and not any(v.strip() for v in self._volume_values(index)))

    def _load_preset(self, name):
        data = PRESETS[name]
        self._clear_rows()
        for row in data["rows"]:
            self._add_row(row["type"], row)
        self.ch4_var.set(str(data.get("ch4_volume", "0")))
        self.mix_var.set(str(data.get("mix_pause", "0")))
        self.current_preset.set(name)
        self.current_step = 0
        self._refresh_rows()
        self.current_label.configure(text=f"Current: {name}")
        self.rotation.set(str(data["rotation"]))

    def _num_steps(self):
        return len(self.row_buttons)

    def _row_type(self, index):
        return STEP_KEYS[self.type_vars[index].get()]

    def _volume_values(self, index):
        return [e.get() for e in self.cells[index]]

    def _row_data(self, index):
        """The row as stored in PRESETS."""
        kind = self._row_type(index)
        if kind == "in":
            return {"type": "in", "volumes": self._volume_values(index)}
        if kind == "wait":
            return {"type": "wait", "seconds": self.wait_cells[index].get()}
        return {"type": "out"}

    def _update_header(self):
        """Preset switching and rotation (only applied by Initialize) only when
        idle; the other preset constants and Save also while a step waits."""
        idle = not self.busy
        editable = idle or self.waiting
        self.preset_box.configure(state="readonly" if idle else "disabled")
        self.ok_btn.configure(state="normal" if idle else "disabled")
        self.rotation_entry.configure(state="normal" if idle else "disabled")
        for w in (self.save_btn, self.ch4_entry, self.mix_entry):
            w.configure(state="normal" if editable else "disabled")

    # ---- callbacks ---------------------------------------------------------
    def on_choose_preset(self):
        self._load_preset(self.chosen_preset.get())
        self.initialized = False
        self.init_btn.configure(state="normal")
        self.status.set("Not initialized")
        self._update_run_state()

    def on_save(self):
        name = self.current_preset.get()
        degrees = to_degrees(self.rotation.get())
        if degrees is None:
            messagebox.showerror("Invalid rotation", self._rotation_error())
            return
        if not is_number(self.ch4_var.get()):
            messagebox.showerror("Invalid value",
                                 "Ch 4 output volume must be a number.")
            return
        if not is_duration(self.mix_var.get()):
            messagebox.showerror("Invalid value",
                                 "Mixing pause must be a number of seconds "
                                 "(0 or more).")
            return
        PRESETS[name]["rotation"] = degrees
        PRESETS[name]["ch4_volume"] = self.ch4_var.get().strip()
        PRESETS[name]["mix_pause"] = self.mix_var.get().strip()
        PRESETS[name]["rows"] = [self._row_data(i)
                                 for i in range(self._num_steps())
                                 if not self._step_is_empty(i)]
        print(f"[gui] saved changes to {name}")
        messagebox.showinfo("Saved", f"Changes saved to {name}.")

    # -- initialize: pump setup + arm HOME/rotate, all on the asyncio thread --
    async def _init_hardware(self, degrees):
        if self.controller is None:
            # Create inside the running loop (the mock grabs the current loop).
            self.pump = Pump(PUMP_ADDRESS, debug=DEBUGGING)
            self.controller = DilutionController(self.pump)
        await self.controller.setup()

        # Arm code is blocking serial I/O, so run it in a thread.
        def arm_sequence():
            self.arm.connect()
            self.arm.home()
            self.arm.rotate(degrees)
        await asyncio.to_thread(arm_sequence)

    def on_initialize(self):
        degrees = to_degrees(self.rotation.get())
        if degrees is None:
            messagebox.showerror("Invalid rotation", self._rotation_error())
            return

        self.init_btn.configure(state="disabled")
        self.initialized = False
        self._set_busy(True)
        self.status.set("Initializing...")
        self._submit(self._init_hardware(degrees),
                     self._init_done, self._init_failed)

    def _rotation_error(self):
        return (f"Rotation '{self.rotation.get().strip()}' is not valid.\n"
                f"It must be a whole number of degrees from {ARM_MIN_DEG} "
                f"to {ARM_MAX_DEG} (e.g. 30 or 30.0).")

    def _init_failed(self, exc):
        self._set_busy(False)
        self.init_btn.configure(state="normal")
        self.status.set("Initialization failed")
        messagebox.showerror("Initialization failed", str(exc))

    def _init_done(self, _result):
        self.current_step = 0
        self.initialized = True
        self.status.set("Ready")
        self._set_busy(False)

    def _set_busy(self, busy, waiting=False):
        """Update the busy/waiting state and every widget that depends on it."""
        self.busy = busy
        self.waiting = busy and waiting
        self._update_header()
        self._refresh_rows()
        self._update_run_state()

    # -- run one step: pump in (+ mixing pause), pump out, or wait ------------
    def on_run_step(self):
        i = self.current_step
        n = i + 1
        kind = self._row_type(i)
        if self._step_is_empty(i):
            messagebox.showwarning("Empty step", f"Step #{n} has no values.")
            return
        self._fill_blanks(i)                        # empty fields -> 0.0
        try:
            if kind == "in":
                step = tuple(to_float(v) for v in self._volume_values(i))
                step += (0.0,)                      # channel 4 stays off
                mix_pause = to_float(self.mix_var.get())
            elif kind == "out":
                step = (0.0,) * len(COLUMN_HEADERS) + (to_float(self.ch4_var.get()),)
            else:
                seconds = to_float(self.wait_cells[i].get())
        except ValueError:
            messagebox.showerror("Invalid value",
                                 f"Step #{n} contains a value that is not "
                                 "a number.")
            return
        self._duplicate_into_next_row()

        if kind == "wait":
            self._start_wait(f"Step #{n}: waiting...", seconds)
            return
        if kind == "in":
            self.status.set(f"Step #{n}: pumping in (ch 1-3)...")
            on_pumped = lambda _r: self._start_wait(
                f"Step #{n}: pumping done, mixing...", mix_pause)
        else:
            self.status.set(f"Step #{n}: pumping out (ch 4)...")
            on_pumped = self._step_done
        self._set_busy(True)
        self._submit(self.controller.run_step(step),
                     on_pumped, self._step_failed)

    def _start_wait(self, text, seconds):
        """Wait `seconds` on the asyncio loop, so the GUI stays usable (the
        upcoming rows can be edited meanwhile), then finish the step."""
        self._wait_end = time.monotonic() + seconds
        self._set_busy(True, waiting=True)
        self._countdown(text, self._wait_end)
        self._submit(asyncio.sleep(seconds), self._step_done, self._step_failed)

    def _countdown(self, text, end):
        if not (self.waiting and self._wait_end == end):
            return                          # that wait is over
        left = math.ceil(max(0.0, end - time.monotonic()))
        self.status.set(f"{text} {left} s left" + self._stop_note())
        self.after(200, self._countdown, text, end)

    def on_add_step(self, kind):
        """Add a step row at the bottom and put the cursor in its first field."""
        self._add_row(kind)
        r = self._num_steps() - 1
        self._refresh_rows()
        self._update_run_state()
        target = {"in": self.cells[r][0], "wait": self.wait_cells[r]}.get(kind)
        if target is not None and str(target.cget("state")) == "normal":
            target.focus_set()
        self.after_idle(lambda: self.canvas.yview_moveto(1.0))

    def on_delete_row(self, index):
        if not self._row_editable(index):
            return
        if not messagebox.askyesno(
                "Delete step",
                f"Delete step #{index + 1} ({self.type_vars[index].get()})?",
                icon="warning", parent=self):
            return
        if not self._row_editable(index):   # it started while the box was open
            return
        # Rebuild the table without that row, so every row's callbacks keep
        # pointing at the right index.
        rows = [self._row_data(i) for i in range(self._num_steps())]
        del rows[index]
        self._clear_rows()
        for row in rows:
            self._add_row(row["type"], row)
        self._place_add_bar()
        self._refresh_rows()
        self._update_run_state()

    def _duplicate_into_next_row(self):
        """If ticked, copy the current row into the next row if that is empty,
        or into a new row if this is the last one. Not during Run all: each
        copied step would add another, so it would never finish."""
        cur, nxt = self.current_step, self.current_step + 1
        if not self.duplicate_next.get() or self.auto_run:
            return
        if nxt >= self._num_steps():
            self._add_row(self._row_type(cur), self._row_data(cur))
            return
        if not self._step_is_empty(nxt):
            return                      # never overwrite an existing step
        self.type_vars[nxt].set(self.type_vars[cur].get())
        for entry, value in zip(self.cells[nxt], self._volume_values(cur)):
            entry.var.set(value)
        self.wait_cells[nxt].var.set(self.wait_cells[cur].get())

    def _step_failed(self, exc):
        self.auto_run = self.stopping = False
        self.status.set("Pump error")
        self._set_busy(False)
        messagebox.showerror("Pump error", str(exc))

    def _step_done(self, _result):
        self.stopping = False
        self.status.set(f"Step #{self.current_step + 1} done")
        self.current_step += 1              # lock finished row, advance
        at_end = self.current_step >= self._num_steps()
        if at_end:
            if self.auto_run:
                self.status.set("All steps done")
            self.auto_run = False
            self.init_btn.configure(state="normal")
        self._set_busy(False)
        if self.auto_run:
            self._run_next_auto()

    # -- run all: go through the steps without manual interaction -------------
    def on_run_all(self):
        self.auto_run = True
        self._run_next_auto()

    def on_stop(self):
        """Stop after the step that is running now (never mid-pump)."""
        self.auto_run = False
        if self.busy:
            self.stopping = True
            self.status.set(self.status.get() + self._stop_note())
        self._update_run_state()

    def _stop_note(self):
        return " (stopping after this step)" if self.stopping else ""

    def _run_next_auto(self):
        if self.current_step < self._num_steps():
            self._fill_blanks(self.current_step)    # blanks -> 0.0, as on leave
        if not self._can_run_current():
            self.auto_run = False
            self.status.set(f"Run all stopped: step #{self.current_step + 1} "
                            "is incomplete")
            self._update_run_state()
            return
        self.on_run_step()

    def on_exit(self):
        self.arm.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()