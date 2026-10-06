"""SQLite storage for presets and session logs (stdlib sqlite3, no extra
dependency).

A preset is the same dict the GUI uses:
    {"rotation": 45, "ch4_volume": "0.005", "mix_pause": "5",
     "measure_pause": "30",
     "rows": [{"type": "in", "volumes": ["0.001", "0.002", "0"]},
              {"type": "wait", "seconds": "30"},
              {"type": "out"}]}
Values are stored as the text the user typed, so they load back unchanged.

A session is:
    {"id": None,                     # set by save_session()
     "started_at": "2026-10-06T14:03:12", "preset_name": "preset1",
     "rotation": 45, "ch4_volume": "0.005", "mix_pause": "5",
     "measure_pause": "30",          # header: the preset as it was used
     "steps": [{"timestamp": "2026-10-06T14:05:40", "type": "in",
                "ch": [0.001, 0.002, 0.0, 0.0],     # ml pumped, ch 1-4
                "wait": None}]}                     # seconds, wait steps only
plus, when loaded from the database, "saved_at" and "step_count" (footer).
"""

import sqlite3

SCHEMA_VERSION = 3   # bump and add a migration step in _migrate() on change

# Each schema step is a list of single statements (not one string split on
# ";", which would break on a ";" inside a comment).
_SCHEMA_V1 = ["""
CREATE TABLE presets (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position      INTEGER NOT NULL,          -- order in the drop-down
    rotation      INTEGER NOT NULL,
    ch4_volume    TEXT NOT NULL,
    mix_pause     TEXT NOT NULL,
    measure_pause TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)""", """
CREATE TABLE steps (
    preset_id INTEGER NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    type      TEXT NOT NULL CHECK (type IN ('in', 'out', 'wait')),
    vol1      TEXT,                          -- pump in: channels 1-3 (ml)
    vol2      TEXT,
    vol3      TEXT,
    seconds   TEXT,                          -- wait
    PRIMARY KEY (preset_id, position)
)"""]

_SCHEMA_V2 = ["""
CREATE TABLE sessions (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,             -- header: when Initialize ran
    preset_name   TEXT NOT NULL,             -- header: a copy of the preset's
    rotation      INTEGER NOT NULL,          -- name and settings as used, not
    ch4_volume    TEXT NOT NULL,             -- a link, so later edits or
    mix_pause     TEXT NOT NULL,             -- renames don't change the log
    measure_pause TEXT NOT NULL,
    saved_at      TEXT NOT NULL,             -- footer: last save
    step_count    INTEGER NOT NULL           -- footer: steps at last save
)""", """
CREATE TABLE session_steps (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    timestamp  TEXT NOT NULL,                -- when the step started
    type       TEXT NOT NULL CHECK (type IN ('in', 'out', 'wait')),
    ch1        REAL NOT NULL,                -- ml pumped on each channel
    ch2        REAL NOT NULL,
    ch3        REAL NOT NULL,
    ch4        REAL NOT NULL,
    PRIMARY KEY (session_id, position)
)"""]

# wait: seconds of a wait step, NULL otherwise. (No SQL comment here: SQLite
# copies ADD COLUMN text into the table definition, where a "--" comment
# would swallow the closing parenthesis.)
_SCHEMA_V3 = ["ALTER TABLE session_steps ADD COLUMN wait REAL"]


class DataStore:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"Database schema version {version} is newer than this "
                f"program supports ({SCHEMA_VERSION}).")
        for target, statements in ((1, _SCHEMA_V1), (2, _SCHEMA_V2),
                                   (3, _SCHEMA_V3)):
            if version < target:
                with self.conn:
                    for statement in statements:
                        self.conn.execute(statement)
                    self.conn.execute(f"PRAGMA user_version = {target}")

    def close(self):
        self.conn.close()

    def is_empty(self):
        return self.conn.execute("SELECT COUNT(*) FROM presets").fetchone()[0] == 0

    def seed(self, presets):
        """Insert `presets` (name -> preset) in one transaction."""
        with self.conn:
            for name, preset in presets.items():
                self._insert(name, preset)

    def load_all(self):
        """All presets as an ordered dict: name -> preset."""
        presets = {}
        rows = self.conn.execute(
            "SELECT id, name, rotation, ch4_volume, mix_pause, measure_pause "
            "FROM presets ORDER BY position, id").fetchall()
        for pid, name, rotation, ch4, mix, measure in rows:
            steps = self.conn.execute(
                "SELECT type, vol1, vol2, vol3, seconds FROM steps "
                "WHERE preset_id = ? ORDER BY position", (pid,)).fetchall()
            presets[name] = {
                "rotation": rotation, "ch4_volume": ch4, "mix_pause": mix,
                "measure_pause": measure,
                "rows": [self._row_from_db(*step) for step in steps]}
        return presets

    def save(self, name, preset):
        """Create or overwrite the preset called `name`, steps included."""
        with self.conn:
            found = self.conn.execute(
                "SELECT id FROM presets WHERE name = ?", (name,)).fetchone()
            if found is None:
                self._insert(name, preset)
                return
            pid = found[0]
            self.conn.execute(
                "UPDATE presets SET rotation = ?, ch4_volume = ?, "
                "mix_pause = ?, measure_pause = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (preset["rotation"], preset["ch4_volume"], preset["mix_pause"],
                 preset["measure_pause"], pid))
            self.conn.execute("DELETE FROM steps WHERE preset_id = ?", (pid,))
            self._insert_steps(pid, preset["rows"])

    def rename(self, old, new):
        with self.conn:
            cur = self.conn.execute(
                "UPDATE presets SET name = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE name = ?", (new, old))
            if cur.rowcount != 1:
                raise sqlite3.DatabaseError(f"No preset called '{old}'.")

    def save_session(self, session, saved_at):
        """Write the whole session (header, steps, footer) and return its id.
        Saving again later rewrites the steps and the footer."""
        with self.conn:
            if session["id"] is None:
                cur = self.conn.execute(
                    "INSERT INTO sessions (started_at, preset_name, rotation, "
                    "ch4_volume, mix_pause, measure_pause, saved_at, "
                    "step_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (session["started_at"], session["preset_name"],
                     session["rotation"], session["ch4_volume"],
                     session["mix_pause"], session["measure_pause"],
                     saved_at, len(session["steps"])))
                sid = cur.lastrowid
            else:
                sid = session["id"]
                self.conn.execute(
                    "UPDATE sessions SET saved_at = ?, step_count = ? "
                    "WHERE id = ?", (saved_at, len(session["steps"]), sid))
                self.conn.execute(
                    "DELETE FROM session_steps WHERE session_id = ?", (sid,))
            self.conn.executemany(
                "INSERT INTO session_steps (session_id, position, timestamp, "
                "type, ch1, ch2, ch3, ch4, wait) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(sid, i, step["timestamp"], step["type"], *step["ch"],
                  step.get("wait"))
                 for i, step in enumerate(session["steps"])])
        return sid

    def list_sessions(self):
        """Saved sessions, newest first: [(id, started_at, preset_name)]."""
        return self.conn.execute(
            "SELECT id, started_at, preset_name FROM sessions "
            "ORDER BY started_at DESC, id DESC").fetchall()

    def load_session(self, sid):
        """One saved session with its steps (see the module docstring)."""
        row = self.conn.execute(
            "SELECT started_at, preset_name, rotation, ch4_volume, mix_pause, "
            "measure_pause, saved_at, step_count FROM sessions WHERE id = ?",
            (sid,)).fetchone()
        if row is None:
            raise sqlite3.DatabaseError(f"No session with id {sid}.")
        keys = ("started_at", "preset_name", "rotation", "ch4_volume",
                "mix_pause", "measure_pause", "saved_at", "step_count")
        session = {"id": sid, **dict(zip(keys, row))}
        session["steps"] = [
            {"timestamp": ts, "type": kind, "ch": [c1, c2, c3, c4],
             "wait": wait}
            for ts, kind, c1, c2, c3, c4, wait in self.conn.execute(
                "SELECT timestamp, type, ch1, ch2, ch3, ch4, wait "
                "FROM session_steps WHERE session_id = ? ORDER BY position",
                (sid,))]
        return session

    # -- helpers (call inside a transaction) ---------------------------------
    def _insert(self, name, preset):
        position = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM presets").fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO presets (name, position, rotation, ch4_volume, "
            "mix_pause, measure_pause) VALUES (?, ?, ?, ?, ?, ?)",
            (name, position, preset["rotation"], preset["ch4_volume"],
             preset["mix_pause"], preset["measure_pause"]))
        self._insert_steps(cur.lastrowid, preset["rows"])

    def _insert_steps(self, pid, rows):
        for position, row in enumerate(rows):
            volumes = (list(row.get("volumes", [])) + [None] * 3)[:3]
            self.conn.execute(
                "INSERT INTO steps (preset_id, position, type, vol1, vol2, "
                "vol3, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, position, row["type"], *volumes, row.get("seconds")))

    @staticmethod
    def _row_from_db(kind, vol1, vol2, vol3, seconds):
        if kind == "in":
            return {"type": "in",
                    "volumes": [v if v is not None else "" for v in
                                (vol1, vol2, vol3)]}
        if kind == "wait":
            return {"type": "wait", "seconds": seconds or ""}
        return {"type": "out"}
