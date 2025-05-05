import sqlite3
from pathlib import Path
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, '../data/plants.db')

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS plants (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    common    TEXT   NOT NULL,
    latin     TEXT   NOT NULL,
    location  TEXT,
    notes     TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_id      INTEGER  NOT NULL,
    action        TEXT      NOT NULL,
    start         TEXT      NOT NULL,          -- YYYY‑MM‑DD
    /* sow‑specific */
    range_min     INTEGER,
    range_min_u   TEXT,
    range_max     INTEGER,
    range_max_u   TEXT,
    /* soak / strat‑specific */
    dur_val       INTEGER,
    dur_unit      TEXT,
    FOREIGN KEY (plant_id) REFERENCES plants(id) ON DELETE CASCADE
);
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)

