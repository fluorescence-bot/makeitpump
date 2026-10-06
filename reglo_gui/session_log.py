"""Turn a saved session (see data_store) into a name, a CSV file or a readable
text log, and open files in the system's default application."""

import csv
import os
import re
import subprocess
import sys

TYPE_NAMES = {"in": "Pump in", "out": "Pump out", "wait": "Wait"}
NA = "n/a"
CSV_COLUMNS = ["step#", "type", "ch1", "ch2", "ch3", "ch4", "wait"]

# Which ch1-ch4 a step type uses; the wait column is only used by "wait".
_CHANNELS_USED = {"in": (0, 1, 2), "out": (3,), "wait": ()}


def session_name(session):
    """'<start>-<preset>', e.g. '2026-10-06_14-03-12-preset1'. The time uses
    '-' instead of ':' so the name also works as a file name."""
    stamp = session["started_at"].replace("T", "_").replace(":", "-")
    return f"{stamp}-{session['preset_name']}"


def safe_filename(name):
    """`name` with characters that aren't allowed in file names replaced."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "session"


def _number(value):
    return format(value, "g")


def step_fields(step):
    """[ch1, ch2, ch3, ch4, wait] as text, with n/a where a field doesn't
    apply to the step's type."""
    used = _CHANNELS_USED[step["type"]]
    fields = [_number(v) if i in used else NA for i, v in enumerate(step["ch"])]
    wait = step.get("wait")
    if step["type"] == "wait" and wait is not None:
        fields.append(_number(wait))
    else:
        fields.append(NA)       # also for wait steps logged before v3
    return fields


def write_csv(session, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for n, step in enumerate(session["steps"], start=1):
            writer.writerow([n, TYPE_NAMES[step["type"]], *step_fields(step)])


def to_text(session):
    """Header (preset and settings), one line per step, footer."""
    def when(iso):
        return iso.replace("T", " ")

    lines = [
        f"Session {session_name(session)}",
        f"Started: {when(session['started_at'])}",
        f"Preset:  {session['preset_name']}",
        f"  Rotation:           {session['rotation']}°",
        f"  Ch 4 output volume: {session['ch4_volume']} ml",
        f"  Mixing pause:       {session['mix_pause']} s",
        f"  Measurement pause:  {session['measure_pause']} s",
        "",
    ]
    header = ["#", "TIMESTAMP", "TYPE", "CH 1 (ml)", "CH 2 (ml)", "CH 3 (ml)",
              "CH 4 (ml)", "WAIT (s)"]
    rows = [[str(n), when(step["timestamp"]), TYPE_NAMES[step["type"]],
             *step_fields(step)]
            for n, step in enumerate(session["steps"], start=1)]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        lines.append("  ".join(cell.ljust(w) for cell, w in
                               zip(row, widths)).rstrip())
    if not rows:
        lines.append("(no steps)")
    lines += ["", f"Saved: {when(session['saved_at'])} "
                  f"({session['step_count']} steps)"]
    return "\n".join(lines) + "\n"


def open_with_system(path):
    """Open `path` in the default application (e.g. the text editor)."""
    if sys.platform.startswith("win"):
        os.startfile(path)                                    # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
