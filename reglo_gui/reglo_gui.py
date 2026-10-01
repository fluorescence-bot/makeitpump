"""Reglo ICC pump control - GUI frontend only (pump calls are stubs)."""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import serial  # pip install pyserial

# ---- Configuration ---------------------------------------------------------
COLUMN_HEADERS = ["1", "2", "3", "4"]   # rename later (e.g. "Volume", "Time"...)
NUM_ROWS = 20

# ---- Arduino arm configuration ----------------------------------------------
SERIAL_PORT = "/dev/ttyACM0"
SERIAL_BAUD = 9600        # must match Serial.begin(...) in the Arduino sketch
ARM_BOOT_DELAY = 2.0      # Uno resets when the port opens; wait for it to boot
ARM_TIMEOUT = 60.0        # max seconds to wait for the arm to finish a command
ARM_DONE_REPLY = None     # text the Arduino sends when finished, e.g. "DONE".
                          # None = any non-empty line counts as "done".
ARM_MIN_DEG, ARM_MAX_DEG = 0, 60
PRESETS = {                              # stub data; replace with file/db later
    "preset1": {"rotation": 45, "rows": [["5", "0.1", "0", "0"]]},
    "preset2": {"rotation": 20, "rows": [["2", "0.5", "0", "0"],
                                         ["1", "0.2", "0", "0"]]},
}


# ---- Pump hooks (fill in with the Reglo ICC python API later) ---------------
class PumpStub:
    def initialize(self):
        print("[pump] enable + reset volume, time, etc.")

    def run_step(self, step_index, values):
        print(f"[pump] run step #{step_index + 1}: {values}")


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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Reglo ICC Pump Control")
        self.minsize(720, 560)
        self.pump = PumpStub()
        self.arm = ArmController(SERIAL_PORT, SERIAL_BAUD,
                                 ARM_DONE_REPLY, ARM_TIMEOUT)
        self.arm_queue = queue.Queue()   # worker thread -> GUI thread
        self.protocol("WM_DELETE_WINDOW", self.on_exit)

        self.current_preset = tk.StringVar(value="preset1")
        self.chosen_preset = tk.StringVar(value="preset1")
        self.rotation = tk.StringVar(value="45")
        self.current_step = 0   # row that will run next; rows above are done
        self.pumping = False
        self.initialized = False
        self.duplicate_next = tk.BooleanVar(value=False)
        self.header_widgets = []  # disabled while pumping
        self.cells = []        # cells[row][col] -> Entry
        self.row_buttons = []  # row label buttons (#1, #2, ...)

        self._build_header()
        self._build_steps()
        self._build_footer()
        self._load_preset("preset1")
        self._refresh_rows()

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
        self.rotation_label = ttk.Label(left, text="Rotation: 45\u00b0")
        self.rotation_label.pack(anchor="w", pady=(4, 0))

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

        # Scrollable table
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

        # header row
        ttk.Label(self.grid_frame, text="").grid(row=0, column=0)
        for c, name in enumerate(COLUMN_HEADERS):
            ttk.Label(self.grid_frame, text=name, anchor="center",
                      font=("TkDefaultFont", 10, "bold")).grid(
                row=0, column=c + 1, sticky="ew", padx=1, pady=1)
            self.grid_frame.columnconfigure(c + 1, weight=1, uniform="col")

        # body rows
        for r in range(NUM_ROWS):
            lbl = ttk.Label(self.grid_frame, text=f"#{r + 1}", width=6,
                            anchor="w")
            lbl.grid(row=r + 1, column=0, padx=(2, 4), pady=1, sticky="ew")
            self.row_buttons.append(lbl)
            row_entries = []
            for c in range(len(COLUMN_HEADERS)):
                var = tk.StringVar()
                var.trace_add("write", lambda *_: self._update_run_state())
                e = tk.Entry(self.grid_frame, justify="center", width=10,
                             relief="solid", borderwidth=1, textvariable=var)
                e.bind("<FocusOut>", lambda ev, i=r: self.after_idle(
                    self._normalize_if_left, i))
                e.grid(row=r + 1, column=c + 1, sticky="ew", padx=1, pady=1,
                       ipady=3)
                row_entries.append(e)
            self.cells.append(row_entries)

        # Side buttons (Initialize / Run cur. step)
        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.grid(row=1, column=1, sticky="n")
        self.init_btn = ttk.Button(side, text="Initialize", width=16,
                                   command=self.on_initialize)
        self.init_btn.pack(pady=(0, 10), ipady=6)
        self.run_btn = ttk.Button(side, text="Run cur. step", width=16,
                                  command=self.on_run_step, state="disabled")
        self.run_btn.pack(ipady=6)
        ttk.Checkbutton(side, text="Duplicate current row\nto next row",
                        variable=self.duplicate_next).pack(pady=(10, 0),
                                                           anchor="w")

    def _build_footer(self):
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        ttk.Button(footer, text="Exit", width=14,
                   command=self.on_exit).pack(side="right")

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
        While pumping everything is locked."""
        for i, (lbl, row) in enumerate(zip(self.row_buttons, self.cells)):
            if i < self.current_step:
                text, bg, editable = f"\u2713 #{i + 1}", "#e0e0e0", False
            elif i == self.current_step:
                text, bg, editable = f"\u25b6 #{i + 1}", "#fff6c8", True
            else:
                text, bg, editable = f"#{i + 1}", "white", True
            lbl.configure(text=text)
            state = "normal" if editable and not self.pumping else "disabled"
            for e in row:
                e.configure(state=state, bg=bg, disabledbackground=bg,
                            disabledforeground="#555555")

    def _fill_blanks(self, index):
        """Empty fields in a row become 0.0."""
        for e in self.cells[index]:
            if str(e.cget("state")) == "normal" and not e.get().strip():
                e.insert(0, "0.0")

    def _normalize_if_left(self, index):
        """When focus leaves a row that has any value, fill its blanks with 0.0."""
        if self.focus_get() in self.cells[index]:
            return
        if not self._step_is_empty(index):
            self._fill_blanks(index)

    def _update_run_state(self):
        """Run is available when initialized, idle, and the next step row
        (the one after the steps already done) has values."""
        if not hasattr(self, "run_btn"):
            return
        ok = (self.initialized and not self.pumping
              and self.current_step < NUM_ROWS
              and not self._step_is_empty(self.current_step))
        self.run_btn.configure(state="normal" if ok else "disabled")

    def _step_is_empty(self, index):
        return not any(v.strip() for v in self._row_values(index))

    def _load_preset(self, name):
        data = PRESETS[name]
        for row in self.cells:
            for e in row:
                e.delete(0, "end")
        for r, values in enumerate(data["rows"][:NUM_ROWS]):
            for c, v in enumerate(values[:len(COLUMN_HEADERS)]):
                self.cells[r][c].insert(0, v)
        self.current_preset.set(name)
        self.current_step = 0
        self._refresh_rows()
        self.current_label.configure(text=f"Current: {name}")
        self.rotation_label.configure(text=f"Rotation: {data['rotation']}\u00b0")

    def _row_values(self, index):
        return [e.get() for e in self.cells[index]]

    def _set_header_enabled(self, enabled):
        self.preset_box.configure(state="readonly" if enabled else "disabled")
        for w in (self.ok_btn, self.save_btn):
            w.configure(state="normal" if enabled else "disabled")

    # ---- callbacks (stubs) ---------------------------------------------------
    def on_choose_preset(self):
        self._load_preset(self.chosen_preset.get())
        # New preset starts from step 1 and needs a fresh init.
        self.initialized = False
        self.init_btn.configure(state="normal")
        self._update_run_state()

    def on_save(self):
        name = self.current_preset.get()
        PRESETS[name]["rows"] = [v for v in
                                 (self._row_values(i) for i in range(NUM_ROWS))
                                 if any(x.strip() for x in v)]
        print(f"[gui] saved changes to {name}")
        messagebox.showinfo("Saved", f"Changes saved to {name}.")

    def on_initialize(self):
        name = self.current_preset.get()
        try:
            degrees = int(PRESETS[name]["rotation"])
        except (KeyError, ValueError):
            messagebox.showerror("Invalid rotation",
                                 f"Preset {name} has no valid rotation.")
            return
        if not ARM_MIN_DEG <= degrees <= ARM_MAX_DEG:
            messagebox.showerror(
                "Invalid rotation",
                f"Preset {name}: rotation {degrees}\u00b0 is outside "
                f"{ARM_MIN_DEG}-{ARM_MAX_DEG}\u00b0.")
            return

        self.init_btn.configure(state="disabled")
        self.initialized = False
        self._update_run_state()
        self._set_header_enabled(False)
        self.pump.initialize()
        # Arm: HOME, then rotate, in a worker thread so the GUI stays alive.
        threading.Thread(target=self._arm_worker, args=(degrees,),
                         daemon=True).start()
        self.after(100, self._poll_arm)

    def _arm_worker(self, degrees):
        try:
            self.arm.connect()
            self.arm.home()
            self.arm.rotate(degrees)
            self.arm_queue.put(("ok", None))
        except ArmError as exc:
            self.arm_queue.put(("error", str(exc)))

    def _poll_arm(self):
        try:
            kind, msg = self.arm_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_arm)
            return
        if kind == "ok":
            self._init_done()
        else:
            self._init_failed(msg)

    def _init_failed(self, msg):
        self._set_header_enabled(True)
        self.init_btn.configure(state="normal")
        messagebox.showerror("Arm initialization failed", msg)

    def _init_done(self):
        # Init finished: Initialize stays inactive, Run becomes active.
        self._set_header_enabled(True)
        self.current_step = 0
        self.initialized = True
        self._refresh_rows()
        self._update_run_state()

    def on_run_step(self):
        if self._step_is_empty(self.current_step):
            messagebox.showwarning("Empty step",
                                   f"Step #{self.current_step + 1} has no values.")
            return
        self._fill_blanks(self.current_step)           # empty fields -> 0.0
        self._duplicate_into_next_row()
        self._set_header_enabled(False)
        self.pumping = True
        self._update_run_state()                      # inactive while pumping
        self._refresh_rows()
        self.pump.run_step(self.current_step, self._row_values(self.current_step))
        # Simulate pumping; replace with real completion callback.
        self.after(2000, self._pumping_done)

    def _duplicate_into_next_row(self):
        """If ticked and the next row is empty, copy the current row into it."""
        nxt = self.current_step + 1
        if not self.duplicate_next.get() or nxt >= NUM_ROWS:
            return
        if not self._step_is_empty(nxt):
            return                      # never overwrite an existing step
        for entry, value in zip(self.cells[nxt], self._row_values(self.current_step)):
            entry.delete(0, "end")
            entry.insert(0, value)

    def _pumping_done(self):
        self.pumping = False
        self.current_step += 1              # lock finished row, advance
        self._set_header_enabled(True)
        at_end = (self.current_step >= NUM_ROWS
                  or self._step_is_empty(self.current_step))
        self._refresh_rows()
        if at_end:
            # Nothing queued: allow a new init. Editing the next row later
            # makes it runnable again (see _update_run_state).
            self.init_btn.configure(state="normal")
        self._update_run_state()

    def on_exit(self):
        self.arm.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()