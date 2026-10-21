"""App settings in the database (schema v6), used for the tube lengths."""

import sqlite3

import data_store
from data_store import DataStore


def test_setting_round_trip(tmp_path):
    store = DataStore(tmp_path / "data.db")
    assert store.get_setting("tube_lengths_mm", [None] * 4) == [None] * 4
    store.set_setting("tube_lengths_mm", [304.0, None, 120.5, 500.0])
    store.set_setting("tube_lengths_mm", [304.0, 200.0, 120.5, 500.0])
    store.close()
    again = DataStore(tmp_path / "data.db")
    assert again.get_setting("tube_lengths_mm") == [304.0, 200.0, 120.5, 500.0]


def test_v5_database_is_upgraded(tmp_path):
    """A database from before settings existed keeps its presets and gets the table."""
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    for statements in (data_store._SCHEMA_V1, data_store._SCHEMA_V2, data_store._SCHEMA_V3,
                       data_store._SCHEMA_V4, data_store._SCHEMA_V5):
        for statement in statements:
            conn.execute(statement)
    conn.execute("INSERT INTO presets (name, position, rotation, ch4_volume, mix_pause, measure_pause) "
                 "VALUES ('p', 0, 30, '1', '2', '3')")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()

    store = DataStore(path)
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == data_store.SCHEMA_VERSION == 6
    assert list(store.load_all()) == ["p"]
    store.set_setting("x", 1)
    assert store.get_setting("x") == 1
