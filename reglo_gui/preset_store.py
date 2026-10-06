"""SQLite storage for presets (stdlib sqlite3, no extra dependency).

A preset is the same dict the GUI uses:
    {"rotation": 45, "ch4_volume": "0.005", "mix_pause": "5",
     "measure_pause": "30",
     "rows": [{"type": "in", "volumes": ["0.001", "0.002", "0"]},
              {"type": "wait", "seconds": "30"},
              {"type": "out"}]}
Values are stored as the text the user typed, so they load back unchanged.
"""

import sqlite3

SCHEMA_VERSION = 1   # bump and add a migration step in _migrate() on change

_SCHEMA_V1 = """
CREATE TABLE presets (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position      INTEGER NOT NULL,          -- order in the drop-down
    rotation      INTEGER NOT NULL,
    ch4_volume    TEXT NOT NULL,
    mix_pause     TEXT NOT NULL,
    measure_pause TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE steps (
    preset_id INTEGER NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    type      TEXT NOT NULL CHECK (type IN ('in', 'out', 'wait')),
    vol1      TEXT,                          -- pump in: channels 1-3 (ml)
    vol2      TEXT,
    vol3      TEXT,
    seconds   TEXT,                          -- wait
    PRIMARY KEY (preset_id, position)
);
"""


class PresetStore:
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
        if version < 1:
            with self.conn:
                for statement in _SCHEMA_V1.split(";"):
                    if statement.strip():
                        self.conn.execute(statement)
                self.conn.execute("PRAGMA user_version = 1")

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
