"""Reglo ICC pump control GUI, using DilutionController (same as the mock script)."""

import asyncio
import logging
import math
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import font as tkfont

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

LOCKED, UNLOCKED, REVERT = "\U0001F512", "\U0001F513", "\u21B6"   # button symbols
RENAME = "\u270E"
PRESET_NAME_MAX = 40

# ---- Theme -------------------------------------------------------------------
BG = "#eef1f5"            # window background, behind the cards
CARD = "#ffffff"          # card background
BORDER = "#d5dbe3"
TEXT = "#1f2933"
MUTED = "#66727f"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#dbeafe"   # current step row
SUCCESS = "#15803d"       # tick on finished steps
DANGER = "#dc2626"
DONE_BG = "#f3f4f6"       # finished step row
NA_BG = "#e5e7eb"         # field that doesn't apply to the row's step type
HOVER_BG = "#e8edf3"
BUTTON_BG = "#f6f8fa"

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
                "measure_pause": "30",
                "rows": [{"type": "in", "volumes": ["0.001", "0.002", "0"]},
                         {"type": "out"}]},
    "preset2": {"rotation": 20, "ch4_volume": "0.010", "mix_pause": "10",
                "measure_pause": "30",
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
        self.minsize(820, 640)
        self._apply_theme()

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

    # ---- theme -------------------------------------------------------------
    def _apply_theme(self):
        """ttk styles on top of 'clam' (the most restylable built-in theme).
        Cards are white frames on a light grey window."""
        self.configure(background=BG)
        default = tkfont.nametofont("TkDefaultFont")
        default.configure(size=10)
        for name in ("TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(size=10)
        family = default.actual("family")
        self.font_title = (family, 15, "bold")
        self.font_heading = (family, 11, "bold")
        self.font_small_bold = (family, 9, "bold")

        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, troughcolor=BG,
                     focuscolor=ACCENT, selectbackground=ACCENT,
                     selectforeground="white", font=default)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD, relief="solid",
                     borderwidth=1)
        st.configure("Plain.Card.TFrame", relief="flat", borderwidth=0)
        st.configure("TLabel", background=BG)
        st.configure("Card.TLabel", background=CARD)
        st.configure("Muted.Card.TLabel", background=CARD, foreground=MUTED)
        st.configure("Title.Card.TLabel", background=CARD, font=self.font_title)
        st.configure("Heading.Card.TLabel", background=CARD,
                     font=self.font_heading)
        st.configure("ColHead.Card.TLabel", background=CARD, foreground=MUTED,
                     font=self.font_small_bold)
        st.configure("Status.TLabel", background=BG, foreground=MUTED)
        # step-row labels: upcoming, current, finished
        st.configure("Row.Card.TLabel", background=CARD, foreground=MUTED)
        st.configure("Current.Row.Card.TLabel", background=CARD,
                     foreground=ACCENT, font=self.font_small_bold)
        st.configure("Done.Row.Card.TLabel", background=CARD,
                     foreground=SUCCESS)

        st.configure("TButton", background=BUTTON_BG, padding=(12, 5),
                     relief="raised", borderwidth=1)
        st.map("TButton",
               background=[("disabled", CARD), ("pressed", NA_BG),
                           ("active", HOVER_BG)],
               foreground=[("disabled", "#a0a8b3")],
               bordercolor=[("focus", ACCENT)])
        st.configure("Accent.TButton", background=ACCENT, foreground="white",
                     bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                     font=self.font_heading)
        st.map("Accent.TButton",
               background=[("disabled", "#a9bff0"), ("pressed", ACCENT_HOVER),
                           ("active", ACCENT_HOVER)],
               foreground=[("disabled", "white")],
               bordercolor=[("disabled", "#a9bff0")],
               lightcolor=[("disabled", "#a9bff0")],
               darkcolor=[("disabled", "#a9bff0")])
        # small borderless icon buttons (lock, revert, delete)
        icon = dict(padding=(4, 2), background=CARD, relief="flat",
                    bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
        st.configure("Icon.TButton", **icon)
        st.configure("Danger.Icon.TButton", **icon)
        st.map("Danger.Icon.TButton",
               background=[("disabled", CARD), ("active", "#fee2e2")],
               foreground=[("disabled", "#c4c9d0"), ("active", DANGER)])

        for widget in ("TEntry", "TCombobox"):
            st.configure(widget, fieldbackground=CARD, padding=(6, 3),
                         arrowcolor=MUTED)
            st.map(widget,
                   fieldbackground=[("disabled", DONE_BG), ("readonly", DONE_BG)],
                   foreground=[("disabled", "#a0a8b3")],
                   bordercolor=[("focus", ACCENT)],
                   lightcolor=[("focus", ACCENT)])
        # a readonly combobox is still a choice, not a locked field
        st.map("TCombobox",
               fieldbackground=[("disabled", DONE_BG), ("readonly", CARD)],
               foreground=[("disabled", "#a0a8b3")],
               bordercolor=[("focus", ACCENT)],
               lightcolor=[("focus", ACCENT)])
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

        st.configure("Card.TCheckbutton", background=CARD)
        st.map("Card.TCheckbutton", background=[("active", CARD)],
               indicatorbackground=[("selected", ACCENT)],
               indicatorforeground=[("selected", "white")])
        st.configure("Vertical.TScrollbar", background=NA_BG, troughcolor=CARD,
                     bordercolor=CARD, arrowcolor=MUTED, relief="flat")

    def _card(self, parent, **pack):
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.pack(**pack)
        return card

    # ---- layout ------------------------------------------------------------
    def _build_header(self):
        header = self._card(self, fill="x", padx=12, pady=(12, 6))
        header.columnconfigure(0, weight=1)

        left = ttk.Frame(header, style="Plain.Card.TFrame")
        left.grid(row=0, column=0, rowspan=2, sticky="nw")
        ttk.Label(left, text="PRESET", style="Muted.Card.TLabel",
                  font=self.font_small_bold).pack(anchor="w")
        title_row = ttk.Frame(left, style="Plain.Card.TFrame")
        title_row.pack(anchor="w", pady=(0, 8))
        self.current_label = ttk.Label(title_row, text="preset1",
                                       style="Title.Card.TLabel")
        self.current_label.pack(side="left")
        self.rename_btn = ttk.Button(title_row, text=RENAME, width=3,
                                     style="Icon.TButton",
                                     command=self.on_rename_preset)
        self.rename_btn.pack(side="left", padx=(6, 0))
        consts = ttk.Frame(left, style="Plain.Card.TFrame")
        consts.pack(anchor="w")
        self.ch4_var = tk.StringVar(value="0")
        self.mix_var = tk.StringVar(value="0")
        self.measure_var = tk.StringVar(value="0")  # default for new Pause rows
        # Preset constants, keyed like PRESETS. Each starts locked against
        # accidental edits; the revert button restores the saved value.
        self.consts = {}
        for r, (key, text, var) in enumerate(
                [("rotation", f"Rotation (\u00b0, {ARM_MIN_DEG}-{ARM_MAX_DEG}):",
                  self.rotation),
                 ("ch4_volume", "Ch 4 output volume (ml):", self.ch4_var),
                 ("mix_pause", "Mixing pause (s):", self.mix_var),
                 ("measure_pause", "Measurement pause (s):", self.measure_var)]):
            ttk.Label(consts, text=text, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", padx=(0, 10))
            e = ttk.Entry(consts, textvariable=var, width=8, justify="center")
            e.grid(row=r, column=1, pady=2)
            e.bind("<KeyRelease>", lambda ev: self._update_run_state())
            lock = ttk.Button(consts, text=LOCKED, width=3, style="Icon.TButton",
                              command=lambda k=key: self.on_toggle_lock(k))
            lock.grid(row=r, column=2, padx=(6, 0), pady=2)
            revert = ttk.Button(consts, text=REVERT, width=3,
                                style="Icon.TButton",
                                command=lambda k=key: self.on_revert_const(k))
            revert.grid(row=r, column=3, padx=(3, 0), pady=2)
            self.consts[key] = {"var": var, "entry": e, "lock_btn": lock,
                                "revert_btn": revert, "locked": True}
            var.trace_add("write", lambda *_, k=key: self._refresh_const(k))

        right = ttk.Frame(header, style="Plain.Card.TFrame")
        right.grid(row=0, column=1, sticky="ne")
        ttk.Label(right, text="Load preset", style="Muted.Card.TLabel").pack(
            anchor="w", pady=(0, 2))
        pick = ttk.Frame(right, style="Plain.Card.TFrame")
        pick.pack(anchor="e")
        self.preset_box = ttk.Combobox(pick, textvariable=self.chosen_preset,
                                       values=list(PRESETS), state="readonly",
                                       width=18)
        self.preset_box.pack(side="left")
        self.ok_btn = ttk.Button(pick, text="OK", command=self.on_choose_preset)
        self.ok_btn.pack(side="left", padx=(6, 0))

        self.save_btn = ttk.Button(header, text="Save changes",
                                   command=self.on_save)
        self.save_btn.grid(row=1, column=1, sticky="se")

    def _build_steps(self):
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        steps_card = ttk.Frame(body, style="Card.TFrame", padding=14)
        steps_card.grid(row=0, column=0, sticky="nsew")
        steps_card.columnconfigure(0, weight=1)
        steps_card.rowconfigure(1, weight=1)
        ttk.Label(steps_card, text="Steps", style="Heading.Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8))

        table_frame = ttk.Frame(steps_card, style="Plain.Card.TFrame")
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(table_frame, highlightthickness=0,
                                background=CARD)
        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=self.canvas.yview)
        def set_scroll(first, last):
            """Only show the scrollbar when the steps don't fit."""
            if float(first) <= 0 and float(last) >= 1:
                scroll.grid_remove()
            else:
                scroll.grid()
            scroll.set(first, last)
        self.canvas.configure(yscrollcommand=set_scroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self.grid_frame = ttk.Frame(self.canvas, style="Plain.Card.TFrame")
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
            ttk.Label(self.grid_frame, text=name.upper(), anchor="center",
                      style="ColHead.Card.TLabel").grid(
                row=0, column=c, sticky="ew", padx=2, pady=(0, 4))
            if c >= 2:
                self.grid_frame.columnconfigure(c, weight=1, uniform="col")

        self._num_cols = len(headers)

        # One row per step; this bar sits under the last row and adds steps.
        self.add_bar = ttk.Frame(self.grid_frame, style="Plain.Card.TFrame")
        ttk.Label(self.add_bar, text="Add row:", style="Muted.Card.TLabel").pack(
            side="left", padx=(0, 8))
        for text, kind in [("Pause", "wait"), ("Output", "out"),
                           ("Input", "in")]:
            ttk.Button(self.add_bar, text="+ " + text,
                       command=lambda k=kind: self.on_add_step(k)).pack(
                side="left", padx=(0, 6))
        self._place_add_bar()

        side = ttk.Frame(body, style="Card.TFrame", padding=14)
        side.grid(row=0, column=1, sticky="ns", padx=(12, 0))
        ttk.Label(side, text="Run", style="Heading.Card.TLabel").pack(
            anchor="w", pady=(0, 8))
        self.init_btn = ttk.Button(side, text="Initialize", width=16,
                                   command=self.on_initialize)
        self.init_btn.pack(fill="x", ipady=4)
        ttk.Separator(side).pack(fill="x", pady=12)
        self.run_btn = ttk.Button(side, text="Run cur. step", width=16,
                                  command=self.on_run_step, state="disabled")
        self.run_btn.pack(fill="x", ipady=4)
        self.run_all_btn = ttk.Button(side, text="\u25b6  Run all", width=16,
                                      style="Accent.TButton",
                                      command=self.on_run_all, state="disabled")
        self.run_all_btn.pack(fill="x", pady=(8, 0), ipady=6)
        self.stop_btn = ttk.Button(side, text="Stop after step", width=16,
                                   command=self.on_stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=(8, 0), ipady=4)
        ttk.Separator(side).pack(fill="x", pady=12)
        ttk.Checkbutton(side, text="Duplicate current row\nto next row",
                        style="Card.TCheckbutton",
                        variable=self.duplicate_next).pack(anchor="w")

    def _add_row(self, kind, data=None):
        """Append a step row of type `kind`, filled from a PRESETS row dict."""
        data = data or {}
        r = len(self.row_buttons)
        grid_row = r + 1                    # row 0 holds the column headers

        def make_entry(c, value):
            var = tk.StringVar(value=value)
            var.trace_add("write", lambda *_: self._update_run_state())
            e = tk.Entry(self.grid_frame, justify="center", width=10,
                         relief="flat", borderwidth=0, textvariable=var,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, foreground=TEXT,
                         insertbackground=TEXT)
            e.var = var   # keep a reference, or Tk drops the trace
            e.grid(row=grid_row, column=c, sticky="ew", padx=2, pady=2, ipady=4)
            return e

        lbl = ttk.Label(self.grid_frame, text=f"#{r + 1}", width=6, anchor="w",
                        style="Row.Card.TLabel")
        lbl.grid(row=grid_row, column=0, padx=(2, 4), pady=2, sticky="ew")
        self.row_buttons.append(lbl)

        type_var = tk.StringVar(value=STEP_TYPES[kind])
        box = ttk.Combobox(self.grid_frame, textvariable=type_var,
                           values=list(STEP_TYPES.values()),
                           state="readonly", width=9)
        box.grid(row=grid_row, column=1, sticky="ew", padx=2, pady=2)
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
                            style="Danger.Icon.TButton",
                            command=lambda: self.on_delete_row(r))
        delete.grid(row=grid_row, column=self._num_cols, padx=(6, 2), pady=2)
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
                          columnspan=self._num_cols + 1, sticky="w",
                          padx=2, pady=(10, 4))

    def _build_footer(self):
        footer = ttk.Frame(self, padding=(14, 6, 12, 12))
        footer.pack(fill="x")
        self.status_dot = tk.Canvas(footer, width=10, height=10,
                                    highlightthickness=0, background=BG)
        self.status_dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="",
                                    tags="dot")
        self.status_dot.pack(side="left", padx=(0, 8))
        ttk.Label(footer, textvariable=self.status,
                  style="Status.TLabel").pack(side="left")
        ttk.Button(footer, text="Exit", width=10,
                   command=self.on_exit).pack(side="right")
        self.status.trace_add("write", lambda *_: self._update_status_dot())

    def _update_status_dot(self):
        """Blue while running, green when ready, red on errors, grey before
        Initialize."""
        text = self.status.get().lower()
        if "error" in text or "failed" in text:
            colour = DANGER
        elif self.busy:
            colour = ACCENT
        elif self.initialized:
            colour = SUCCESS
        else:
            colour = MUTED
        self.status_dot.itemconfigure("dot", fill=colour)

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
                text, bg, style = f"✓ #{i + 1}", DONE_BG, "Done.Row.Card.TLabel"
            elif i == self.current_step:
                text, bg, style = (f"▶ #{i + 1}", ACCENT_SOFT,
                                   "Current.Row.Card.TLabel")
            else:
                text, bg, style = f"#{i + 1}", CARD, "Row.Card.TLabel"
            editable = self._row_editable(i)
            lbl.configure(text=text, style=style)
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
                cell_bg = bg if applies else NA_BG
                e.configure(state="normal" if editable and applies else "disabled",
                            bg=cell_bg, disabledbackground=cell_bg,
                            disabledforeground=MUTED)

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
        self.current_preset.set(name)
        for key, c in self.consts.items():
            c["var"].set(self._saved_const(key))
            c["locked"] = True
        self.current_step = 0
        self._refresh_rows()
        self._refresh_consts()
        self.current_label.configure(text=name)

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
        self.rename_btn.configure(state="normal" if idle else "disabled")
        self.save_btn.configure(state="normal" if editable else "disabled")
        self._refresh_consts()

    # -- preset constants: lock + revert --------------------------------------
    def _const_allowed(self, key):
        """Rotation only while idle (it is applied by Initialize); the others
        also while a step just waits."""
        if key == "rotation":
            return not self.busy
        return not self.busy or self.waiting

    def _saved_const(self, key):
        return str(PRESETS[self.current_preset.get()].get(key, "0")).strip()

    def _refresh_consts(self):
        for key in self.consts:
            self._refresh_const(key)

    def _refresh_const(self, key):
        if not hasattr(self, "consts") or key not in self.consts:
            return                          # still building the header
        c = self.consts[key]
        allowed = self._const_allowed(key)
        if not allowed:
            state = "disabled"
        else:
            state = "readonly" if c["locked"] else "normal"
        c["entry"].configure(state=state)
        c["lock_btn"].configure(text=LOCKED if c["locked"] else UNLOCKED,
                                state="normal" if allowed else "disabled")
        changed = c["var"].get().strip() != self._saved_const(key)
        c["revert_btn"].configure(
            state="normal" if allowed and changed else "disabled")

    def on_toggle_lock(self, key):
        c = self.consts[key]
        c["locked"] = not c["locked"]
        self._refresh_const(key)
        if not c["locked"]:
            c["entry"].focus_set()
            c["entry"].select_range(0, "end")
        else:
            self.focus_set()

    def on_revert_const(self, key):
        """Back to the value saved in the preset, and lock it again."""
        c = self.consts[key]
        c["var"].set(self._saved_const(key))
        c["locked"] = True
        self._refresh_const(key)
        self.focus_set()
        self._update_run_state()

    # ---- callbacks ---------------------------------------------------------
    def on_choose_preset(self):
        self._load_preset(self.chosen_preset.get())
        self.initialized = False
        self.init_btn.configure(state="normal")
        self.status.set("Not initialized")
        self._update_run_state()

    def _preset_name_error(self, new, old):
        """Why `new` can't be the new name of preset `old`, or None if it can."""
        if not new:
            return "The name can't be empty."
        if len(new) > PRESET_NAME_MAX:
            return f"The name can be at most {PRESET_NAME_MAX} characters."
        clash = next((k for k in PRESETS
                      if k != old and k.lower() == new.lower()), None)
        if clash is not None:
            return f"There is already a preset called '{clash}'."
        return None

    def _ask_preset_name(self, old):
        """Themed modal dialog. Returns the new name, or None if cancelled.
        Invalid names show an error in the dialog, which stays open."""
        dlg = tk.Toplevel(self, background=BG)
        dlg.title("Rename preset")
        dlg.transient(self)
        dlg.resizable(False, False)
        card = ttk.Frame(dlg, style="Card.TFrame", padding=16)
        card.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(card, text="Rename preset", style="Heading.Card.TLabel").pack(
            anchor="w")
        ttk.Label(card, text="New name", style="Muted.Card.TLabel").pack(
            anchor="w", pady=(10, 2))
        var = tk.StringVar(value=old)
        entry = ttk.Entry(card, textvariable=var, width=32)
        entry.pack(fill="x")
        error = ttk.Label(card, text="", style="Card.TLabel",
                          foreground=DANGER)
        error.pack(anchor="w", pady=(4, 0))
        buttons = ttk.Frame(card, style="Plain.Card.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        result = {"name": None}

        def ok(_event=None):
            new = var.get().strip()
            message = None if new == old else self._preset_name_error(new, old)
            if message:
                error.configure(text=message)
                entry.focus_set()
                return
            result["name"] = new
            dlg.destroy()

        ttk.Button(buttons, text="Rename", style="Accent.TButton",
                   command=ok).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 8))
        var.trace_add("write", lambda *_: error.configure(text=""))
        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        # centre over the main window, then make it modal
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_visibility()
        dlg.grab_set()
        entry.focus_set()
        entry.select_range(0, "end")
        self.wait_window(dlg)
        return result["name"]

    def on_rename_preset(self):
        old = self.current_preset.get()
        new = self._ask_preset_name(old)
        if new is None or new == old:
            return                          # cancelled or unchanged
        # Rebuild in place to keep the presets' order in the drop-down.
        items = [(new if k == old else k, v) for k, v in PRESETS.items()]
        PRESETS.clear()
        PRESETS.update(items)
        self.current_preset.set(new)
        if self.chosen_preset.get() == old:
            self.chosen_preset.set(new)
        self.preset_box.configure(values=list(PRESETS))
        self.current_label.configure(text=new)
        print(f"[gui] renamed preset {old} -> {new}")

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
        if not is_duration(self.measure_var.get()):
            messagebox.showerror("Invalid value",
                                 "Measurement pause must be a number of "
                                 "seconds (0 or more).")
            return
        PRESETS[name]["rotation"] = degrees
        PRESETS[name]["ch4_volume"] = self.ch4_var.get().strip()
        PRESETS[name]["mix_pause"] = self.mix_var.get().strip()
        PRESETS[name]["measure_pause"] = self.measure_var.get().strip()
        PRESETS[name]["rows"] = [self._row_data(i)
                                 for i in range(self._num_steps())
                                 if not self._step_is_empty(i)]
        for c in self.consts.values():
            c["locked"] = True
        self._refresh_consts()
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
        self._update_status_dot()
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
        """Add a step row at the bottom and put the cursor in its first field.
        A new Pause row starts with the preset's measurement pause."""
        data = {}
        if kind == "wait" and is_duration(self.measure_var.get()):
            data["seconds"] = self.measure_var.get().strip()
        self._add_row(kind, data)
        r = self._num_steps() - 1
        self._refresh_rows()
        self._update_run_state()
        target = {"in": self.cells[r][0], "wait": self.wait_cells[r]}.get(kind)
        if target is not None and str(target.cget("state")) == "normal":
            target.focus_set()
            target.select_range(0, "end")   # typing replaces the default
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