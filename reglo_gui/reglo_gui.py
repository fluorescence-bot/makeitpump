"""Reglo ICC pump control - GUI frontend only (pump calls are stubs)."""

import tkinter as tk
from tkinter import ttk, messagebox

# ---- Configuration ---------------------------------------------------------
COLUMN_HEADERS = ["1", "2", "3", "4"]   # rename later (e.g. "Volume", "Time"...)
NUM_ROWS = 20
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Reglo ICC Pump Control")
        self.minsize(720, 560)
        self.pump = PumpStub()

        self.current_preset = tk.StringVar(value="preset1")
        self.chosen_preset = tk.StringVar(value="preset1")
        self.rotation = tk.StringVar(value="45")
        self.current_step = 0   # row that will run next; rows above are done
        self.pumping = False
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
                e = tk.Entry(self.grid_frame, justify="center", width=10,
                             relief="solid", borderwidth=1)
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
        self.init_btn.configure(state="normal")
        self.run_btn.configure(state="disabled")

    def on_save(self):
        name = self.current_preset.get()
        PRESETS[name]["rows"] = [v for v in
                                 (self._row_values(i) for i in range(NUM_ROWS))
                                 if any(x.strip() for x in v)]
        print(f"[gui] saved changes to {name}")
        messagebox.showinfo("Saved", f"Changes saved to {name}.")

    def on_initialize(self):
        self.init_btn.configure(state="disabled")
        self._set_header_enabled(False)
        self.pump.initialize()
        # Simulate init taking a moment; replace with real completion callback.
        self.after(1500, self._init_done)

    def _init_done(self):
        # Init finished: Initialize stays inactive, Run becomes active.
        self._set_header_enabled(True)
        self.current_step = 0
        self._refresh_rows()
        self.run_btn.configure(state="normal")

    def on_run_step(self):
        if self._step_is_empty(self.current_step):
            messagebox.showwarning("Empty step",
                                   f"Step #{self.current_step + 1} has no values.")
            return
        self.run_btn.configure(state="disabled")      # inactive while pumping
        self._set_header_enabled(False)
        self.pumping = True
        self._refresh_rows()
        self.pump.run_step(self.current_step, self._row_values(self.current_step))
        # Simulate pumping; replace with real completion callback.
        self.after(2000, self._pumping_done)

    def _pumping_done(self):
        self.pumping = False
        self.current_step += 1              # lock finished row, advance
        self._set_header_enabled(True)
        at_end = (self.current_step >= NUM_ROWS
                  or self._step_is_empty(self.current_step))
        self._refresh_rows()
        if at_end:
            # Sequence finished: nothing left to run, allow a new init.
            self.run_btn.configure(state="disabled")
            self.init_btn.configure(state="normal")
        else:
            self.run_btn.configure(state="normal")

    def on_exit(self):
        self.destroy()


if __name__ == "__main__":
    App().mainloop()