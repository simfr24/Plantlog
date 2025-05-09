import sqlite3
import os

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, '../data/plants.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash  TEXT        NOT NULL,
    lang     TEXT        NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS state_types (
  id          INTEGER PRIMARY KEY,
  code        TEXT UNIQUE NOT NULL,
  label       TEXT NOT NULL,
  color_class TEXT NOT NULL,
  icon_class  TEXT NOT NULL,
  sort_rank   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS event_types (
  id           INTEGER PRIMARY KEY,
  code         TEXT UNIQUE NOT NULL,
  label        TEXT NOT NULL,
  color_class  TEXT NOT NULL,
  icon_class   TEXT NOT NULL,
  new_state_id INTEGER,
  sort_rank INTEGER DEFAULT 50,
  FOREIGN KEY(new_state_id) REFERENCES state_types(id)
);

CREATE TABLE IF NOT EXISTS plants (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  common           TEXT   NOT NULL,
  latin            TEXT   NOT NULL,
  location         TEXT,
  notes            TEXT,
  user_id          INTEGER NOT NULL,
  current_state_id INTEGER REFERENCES state_types(id),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id        INTEGER NOT NULL,
  event_type_id   INTEGER NOT NULL,
  happened_on     TEXT    NOT NULL,
  range_min       INTEGER,
  range_min_u     TEXT,
  range_max       INTEGER,
  range_max_u     TEXT,
  dur_val         INTEGER,
  dur_unit        TEXT,
  measure_val REAL,
  measure_unit TEXT,
  FOREIGN KEY(plant_id)      REFERENCES plants(id) ON DELETE CASCADE,
  FOREIGN KEY(event_type_id) REFERENCES event_types(id)
);
"""

def init_and_fill_db():
    # 1) create fresh schema
    with get_conn() as conn:
        conn.executescript(SCHEMA)

        # 2) seed state_types
        conn.executemany("""
            INSERT OR IGNORE INTO state_types
                (code,      label,        color_class,  icon_class,     sort_rank)
            VALUES (?,        ?,            ?,             ?,             ?)
            """, [
            ('seed',       'Sown',        'text-success','fa-seedling',  10),
            ('soaked',     'Soaking',     'text-primary','fa-tint',      5),
            ('strat',      'Stratifying', 'text-info',   'fa-snowflake', 5),
            ('growing',    'Growing',     'text-success','fa-leaf',      20),
            ('flowering',  'Flowering',   'text-warning','fa-fan',       30),
            ('fruiting',   'Fruiting',    'text-danger', 'fa-apple-whole',40),
            ('dead',       'Dead',        'text-dark',   'fa-skull',     90),
            ])

            # event_types
        conn.executemany("""
            INSERT OR IGNORE INTO event_types
                (code,label,color_class,icon_class,new_state_id,sort_rank)
            VALUES (?,      ?,       ?,           ?,
                    (SELECT id FROM state_types WHERE code=?), ?)
            """, [
            ('soak',    'Soak',  'text-primary','fa-tint',       'soaked',     5),
            ('strat',   'Strat', 'text-info',   'fa-snowflake',  'strat',      5),
            ('sow',     'Sow',   'text-success','fa-seedling',   'seed',       10),
            ('plant',     'Plant',   'text-success','fa-tree',   'growing',       15),
            ('sprout',  'Sprout','text-success','fa-leaf',       'growing',    20),
            ('measure', 'Measurement', 'text-secondary', 'fa-ruler', None, 25),
            ('flower',  'Flower','text-warning','fa-fan',        'flowering',  30),
            ('fruit',   'Fruit', 'text-danger', 'fa-apple-whole','fruiting',   40),
            ('dead',    'Death', 'text-dark',   'fa-skull',      'dead',       90),
            ])

        conn.commit()



def init_db():
    init_and_fill_db()

