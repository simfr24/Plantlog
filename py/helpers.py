"""Helpers for the plants application – **event/state edition**.

All public functions keep their previous signatures so existing routes and
views continue to work, but the implementation now targets the new schema:

* **event_types** defines the kinds of events a user can record (sow, soak …)
* **state_types** holds the possible plant states (sown, sprouted …)
* **events** stores individual occurrences and references an event_type
* **plants.current_state_id** mirrors the most recent state‑changing event

Whenever we insert/update/delete events we also refresh `current_state_id` so
that list views can show a plant’s latest status without an extra JOIN.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from py.db import get_conn

###############################################################################
# Utility helpers
###############################################################################

def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


###############################################################################
# Event‑type lookup helpers – cached for speed
###############################################################################

@lru_cache(maxsize=None)
def _event_type_map() -> Dict[str, Dict[str, Any]]:
    """Return a mapping {code: {id:…, new_state_id:…}} for all event types."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, code, new_state_id FROM event_types"
        ).fetchall()
    return {r["code"]: {"id": r["id"], "new_state_id": r["new_state_id"]} for r in rows}


def _etl(code: str) -> Dict[str, Any]:
    """Short‑hand: event‑type‑lookup; raises if unknown."""
    et = _event_type_map().get(code)
    if et is None:
        raise ValueError(f"Unknown event code: {code}")
    return et

###############################################################################
# Data loading helpers
###############################################################################

def _events_for_plant(conn, plant_id: int) -> List[Dict[str, Any]]:
    """Return a list of event dicts (oldest→newest) for a given plant."""
    rows = conn.execute(
        """
        SELECT e.id, et.code AS action, e.happened_on AS start,
               e.range_min, e.range_min_u, e.range_max, e.range_max_u,
               e.dur_val,  e.dur_unit,
               e.measure_val, e.measure_unit
        FROM events      e
        JOIN event_types et ON et.id = e.event_type_id
        WHERE e.plant_id = ?
        ORDER BY e.happened_on, e.id
        """,
        (plant_id,),
    ).fetchall()

    hist: List[Dict[str, Any]] = []
    for a in rows:
        ev: Dict[str, Any] = {
            "id": a["id"],
            "action": a["action"],
            "start": a["start"],
        }
        if a["action"] == "sow":
            ev["range"] = [a["range_min"], a["range_min_u"], a["range_max"], a["range_max_u"]]
        elif a["action"] in ("soak", "strat"):
            ev["duration"] = [a["dur_val"], a["dur_unit"]]
        elif a["action"] == "measure":
            ev["size"] = [a["measure_val"], a["measure_unit"]]
        hist.append(ev)
    return hist


def load_one(plant_id: int) -> Optional[Dict[str, Any]]:
    """Return one plant dict with history + current event."""
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
        if p is None:
            return None
        history = _events_for_plant(conn, plant_id)

    return {
        "id": p["id"],
        "common": p["common"],
        "latin": p["latin"],
        "location": p["location"],
        "notes": p["notes"],
        "history": history,
        "current": history[-1] if history else None,
    }


def load_data(user_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        plants = conn.execute("""
            SELECT p.*,
                   st.label       AS state_label,
                   st.icon_class  AS state_icon,
                   st.color_class AS state_color,
                   st.sort_rank   AS state_rank          -- ➊ NEW
            FROM plants p
            LEFT JOIN state_types st ON st.id = p.current_state_id
            WHERE p.user_id = ?
        """, (user_id,)).fetchall()

        return [
            {
                "id": p["id"],
                "common": p["common"],
                "latin": p["latin"],
                "location": p["location"],
                "notes": p["notes"],
                "history": (hist := _events_for_plant(conn, p["id"])),
                "current": hist[-1] if hist else None,
                "state": None if p["state_label"] is None else {
                    "label":       p["state_label"],
                    "icon_class":  p["state_icon"],
                    "color_class": p["state_color"],
                },
                "state_rank": p["state_rank"] if p["state_rank"] is not None else 999  # ➋ NEW
            }
            for p in plants
        ]


###############################################################################
# Event insertion / update helpers
###############################################################################

def _refresh_current_state(cur, plant_id: int) -> None:
    """Update plants.current_state_id based on the newest state‑changing event."""
    row = cur.execute(
        """
        SELECT et.new_state_id
        FROM events e
        JOIN event_types et ON et.id = e.event_type_id
        WHERE e.plant_id = ? AND et.new_state_id IS NOT NULL
        ORDER BY e.happened_on DESC, e.id DESC
        LIMIT 1
        """,
        (plant_id,),
    ).fetchone()
    cur.execute(
        "UPDATE plants SET current_state_id = ? WHERE id = ?",
        (row["new_state_id"] if row else None, plant_id),
    )


def _insert_event(cur, plant_id: int, ev: Dict[str, Any]) -> None:
    """Low‑level helper to insert *one* event and update current_state."""
    et = _etl(ev["action"])

    base_cols = ("plant_id", "event_type_id", "happened_on")
    base_vals: Tuple[Any, ...] = (plant_id, et["id"], ev["start"])

    if ev["action"] == "sow":
        # 3 base + 4 range = 7 placeholders
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, range_min, range_min_u, range_max, range_max_u)"
            " VALUES (?,?,?,?,?,?,?)",
            base_vals + tuple(ev["range"]),
        )
    elif ev["action"] in ("soak", "strat"):
        # 3 base + 2 duration = 5 placeholders
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, dur_val, dur_unit) VALUES (?,?,?,?,?)",
            base_vals + tuple(ev["duration"]),
        )
    elif ev["action"] == "measure":
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, measure_val, measure_unit)"
            " VALUES (?,?,?,?,?)",
            base_vals + (ev["size"][0], ev["size"][1]),
        )
    else:  # sprout
        cur.execute(f"INSERT INTO events ({','.join(base_cols)}) VALUES (?,?,?)", base_vals)

    # reflect new state (if any)
    if et["new_state_id"] is not None:
        cur.execute("UPDATE plants SET current_state_id = ? WHERE id = ?", (et["new_state_id"], plant_id))


###############################################################################
# Public CRUD helpers
###############################################################################

def save_new_plant(plant_dict: Dict[str, Any], first_event: Dict[str, Any], user_id: int) -> None:
    """Insert a **brand‑new** plant plus its first event in one transaction."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO plants (common, latin, location, notes, user_id) VALUES (?,?,?,?,?)",
            (
                plant_dict["common"],
                plant_dict["latin"],
                plant_dict.get("location"),
                plant_dict.get("notes"),
                user_id,
            ),
        )
        plant_id = cur.lastrowid
        _insert_event(cur, plant_id, first_event)
        conn.commit()


def update_plant(plant_id: int, plant_dict: Dict[str, Any], new_event: Optional[Dict[str, Any]] = None) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE plants SET common = ?, latin = ?, location = ?, notes = ? WHERE id = ?""",
            (
                plant_dict["common"],
                plant_dict["latin"],
                plant_dict.get("location"),
                plant_dict.get("notes"),
                plant_id,
            ),
        )
        if new_event:
            _insert_event(cur, plant_id, new_event)
        conn.commit()


###############################################################################
# Event UPDATE / DELETE helpers (keeping original names for backward compat)
###############################################################################

def update_action(event_id: int, ev: Dict[str, Any]) -> None:  # ✓ kept name
    with get_conn() as conn:
        cur = conn.cursor()
        et = _etl(ev["action"])
        base = (et["id"], ev["start"])

        if ev["action"] == "sow":
            cur.execute(
                """UPDATE events SET event_type_id = ?, happened_on = ?,
                           range_min = ?, range_min_u = ?, range_max = ?, range_max_u = ?
                   WHERE id = ?""",
                base + tuple(ev["range"]) + (event_id,),
            )
        elif ev["action"] in ("soak", "strat"):
            cur.execute(
                """UPDATE events SET event_type_id = ?, happened_on = ?,
                           dur_val = ?, dur_unit = ?
                   WHERE id = ?""",
                base + tuple(ev["duration"]) + (event_id,),
            )
        elif ev["action"] == "measure":
            cur.execute(
                """UPDATE events SET event_type_id = ?, happened_on = ?,
                        measure_val = ?, measure_unit = ?
                WHERE id = ?""",
                base + tuple(ev["size"]) + (event_id,),
            )
        
        else:
            cur.execute(
                "UPDATE events SET event_type_id = ?, happened_on = ? WHERE id = ?",
                base + (event_id,),
            )

        # Refresh plant state (may have changed if this is last event)
        plant_id = cur.execute("SELECT plant_id FROM events WHERE id = ?", (event_id,)).fetchone()[0]
        _refresh_current_state(cur, plant_id)
        conn.commit()


def get_action_by_id(event_id: int) -> Optional[Dict[str, Any]]:  # ✓ kept name
    with get_conn() as conn:
        a = conn.execute(
            """
            SELECT e.id, e.plant_id, et.code AS action, e.happened_on AS start,
                   e.range_min, e.range_min_u, e.range_max, e.range_max_u,
                   e.dur_val,  e.dur_unit,
                   e.measure_val, e.measure_unit,
                   p.common, p.latin,
            FROM events      e
            JOIN event_types et ON et.id = e.event_type_id
            JOIN plants      p  ON p.id  = e.plant_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if a is None:
            return None

        act: Dict[str, Any] = {
            "id": a["id"],
            "plant_id": a["plant_id"],
            "action": a["action"],
            "start": a["start"],
            "common": a["common"],
            "latin": a["latin"],
        }
        if a["action"] == "sow":
            act["range"] = [a["range_min"], a["range_min_u"], a["range_max"], a["range_max_u"]]
        elif a["action"] in ("soak", "strat"):
            act["duration"] = [a["dur_val"], a["dur_unit"]]
        elif a["action"] == "measure":
           act["size"] = [a["measure_val"], a["measure_unit"]]
        return act


def process_delete_action(event_id: int, user_id: int) -> None:  # ✓ kept name
    with get_conn() as conn:
        cur = conn.cursor()
        # ensure ownership
        row = cur.execute(
            """
            SELECT e.plant_id FROM events e
            JOIN plants p ON p.id = e.plant_id AND p.user_id = ?
            WHERE e.id = ?
            """,
            (user_id, event_id),
        ).fetchone()
        if not row:
            return  # not owner – silently ignore

        plant_id = row["plant_id"]
        cur.execute("DELETE FROM events WHERE id = ?", (event_id,))
        _refresh_current_state(cur, plant_id)
        conn.commit()


def process_delete_plant(plant_id: int, user_id: int) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        if cur.execute("SELECT 1 FROM plants WHERE id = ? AND user_id = ?", (plant_id, user_id)).fetchone():
            cur.execute("DELETE FROM events WHERE plant_id = ?", (plant_id,))
            cur.execute("DELETE FROM plants WHERE id = ?", (plant_id,))
            conn.commit()

###############################################################################
# Ancillary helpers (translations, forms…) – unchanged from previous version
###############################################################################

AVAILABLE_LANGS = ["en", "fr", "ru"]


def duration_to_days(val: int, unit: str) -> int:
    if unit == "months":
        return val * 30
    if unit == "weeks":
        return val * 7
    if unit == "hours":
        return round(val / 24)
    return val


def get_translations(lang: str):
    if lang in AVAILABLE_LANGS:
        try:
            module = __import__(f"translations.{lang}", fromlist=["translations"])
            return module.translations
        except ImportError:
            pass
    from translations.en import translations as tr
    return tr


def dateadd(value, days: int = 0, weeks: int = 0, months: int = 0):
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value
    days += weeks * 7 + months * 30
    return value + timedelta(days=days)


###############################################################################
# Form helpers – also largely unchanged (still use action codes)
###############################################################################

# … The rest of the form‑related helpers (get_empty_form, get_form_data, etc.)
# remain identical to the original helpers.py because they only operate on
# *event/action* codes and do not touch the database directly.  They have been
# copied verbatim below for completeness.


def get_empty_form():
    return {
        "common": "",
        "latin": "",
        "status": "sow",
        "event_date": "",
        "event_range_min": 0,
        "event_range_min_u": "days",
        "event_range_max": 0,
        "event_range_max_u": "days",
        "event_dur_val": 0,
        "event_dur_unit": "hours",
        "location": "",
        "event_size_val": 0,
        "event_size_unit": "cm",
        "notes": "",
    }



def get_form_data(request):
    return {
        "common": request.form.get("common", "").strip(),
        "latin": request.form.get("latin", "").strip(),
        "status": request.form.get("status", ""),
        "event_date": request.form.get("event_date", ""),
        "event_range_min": to_int(request.form.get("event_range_min")),
        "event_range_min_u": request.form.get("event_range_min_u", "days"),
        "event_range_max": to_int(request.form.get("event_range_max")),
        "event_range_max_u": request.form.get("event_range_max_u", "days"),
        "event_dur_val": to_int(request.form.get("event_dur_val")),
        "event_dur_unit": request.form.get("event_dur_unit", "hours"),
        "location": request.form.get("location", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "event_size_val": to_int(request.form.get("event_size_val")),
        "event_size_unit": request.form.get("event_size_unit", "cm"),
    }


def get_form_from_plant(plant):
    form = get_empty_form()
    form["common"] = plant["common"]
    form["latin"] = plant["latin"]
    form["location"] = plant.get("location", "") or ""
    form["notes"] = plant.get("notes", "") or ""

    if not plant["history"]:
        return form

    first = plant["history"][0]
    form["status"] = first["action"]
    form["event_date"] = first["start"]

    if first["action"] == "sow" and "range" in first:
        form["event_range_min"] = first["range"][0]
        form["event_range_min_u"] = first["range"][1]
        form["event_range_max"] = first["range"][2]
        form["event_range_max_u"] = first["range"][3]

    elif first["action"] in ("soak", "strat") and "duration" in first:
        form["event_dur_val"] = first["duration"][0]
        form["event_dur_unit"] = first["duration"][1]

    elif first["action"] == "measure" and "size" in first:
        form["event_size_val"] = first["size"][0]
        form["event_size_unit"] = first["size"][1]


    return form

def validate_form(form, translations, context="add"):
    errors: List[str] = []
    event: Optional[Dict[str, Any]] = None

    if context in ("add", "edit"):
        if not form.get("common"):
            errors.append(f"{translations['Common name']} required.")
        if not form.get("latin"):
            errors.append(f"{translations['Latin name']} required.")

    status = form.get("status", "")
    if not status:
        return errors, None

    date = form.get("event_date", "").strip()
    if not date:
        label = translations.get(status.capitalize(), status.capitalize())
        errors.append(f"{translations.get('Date', 'Date')} for {label.lower()} {translations.get('is required.', 'is required.')}")
        return errors, None

    # Handle known types with extra info
    if status == "sow":
        min_days = duration_to_days(form.get("event_range_min", 0), form.get("event_range_min_u", "days"))
        max_days = duration_to_days(form.get("event_range_max", 0), form.get("event_range_max_u", "days"))
        if min_days <= 0 or max_days <= 0:
            errors.append("Sprout range must be > 0.")
        if min_days > max_days:
            errors.append("Sprout min > max.")
        event = {
            "action": "sow",
            "start": date,
            "range": [
                form["event_range_min"],
                form["event_range_min_u"],
                form["event_range_max"],
                form["event_range_max_u"],
            ],
        }

    elif status in ("soak", "strat"):
        dur_val = form.get("event_dur_val", 0)
        if dur_val <= 0:
            errors.append("Duration > 0.")
        event = {
            "action": status,
            "start": date,
            "duration": [dur_val, form["event_dur_unit"]],
        }
    elif status == "measure":
        val = to_int(form.get("event_size_val", 0))
        if val <= 0:
            errors.append("Size must be > 0.")
        event = {
            "action": "measure",
            "start": date,
            "size": [val, form.get("event_size_unit", "cm")],
        }


    else:
        # All other events just need a date
        event = {
            "action": status,
            "start": date
        }

    return errors, event



def form_keys_for(action_dict):
    extras = {
        "common": action_dict.get("common", ""),
        "latin": action_dict.get("latin", ""),
        "event_date": action_dict.get("start", "")
    }
    action = action_dict.get("action")

    if action == "sow" and "range" in action_dict:
        extras.update({
            "event_range_min": action_dict["range"][0],
            "event_range_min_u": action_dict["range"][1],
            "event_range_max": action_dict["range"][2],
            "event_range_max_u": action_dict["range"][3],
        })
    elif action in ("soak", "strat") and "duration" in action_dict:
        extras.update({
            "event_dur_val": action_dict["duration"][0],
            "event_dur_unit": action_dict["duration"][1],
        })
    elif action == "measure" and "size" in action_dict:
        extras.update({
            "event_size_val": action_dict["size"][0],
            "event_size_unit": action_dict["size"][1],
        })

    return "event_date", extras


@lru_cache(maxsize=None)
def get_event_specs():
    """Return every event_type with the columns the UI needs, sorted."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT code, label, icon_class, color_class, sort_rank
               FROM event_types ORDER BY sort_rank"""
        ).fetchall()
    return [
        {
            "code": r["code"],
            "label": r["label"],
            "icon": r["icon_class"],
            # 'text-success' → 'success' so we can feed it to btn‑outline‑…
            "bs_color": r["color_class"].removeprefix("text-"),
            "color_class": r["color_class"],
        }
        for r in rows
    ]
