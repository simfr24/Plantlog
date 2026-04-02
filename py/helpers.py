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
from markupsafe import Markup
import math

from py.db import get_conn

###############################################################################
# Utility helpers
###############################################################################

def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def format_age(days, t):
    if days < 60:
        unit = t['day'] if days == 1 else t['days']
        return f"{days} {unit}"
    elif days < 365:
        months = days // 30
        remaining_days = days % 30
        output = f"{months} {t['month'] if months == 1 else t['months']}"
        if remaining_days > 0:
            output += f" {remaining_days} {t['day'] if remaining_days == 1 else t['days']}"
        return output
    else:
        years = days // 365
        remaining_days = days % 365
        months = remaining_days // 30
        remaining_days = remaining_days % 30
        output = f"{years} {t['year'] if years == 1 else t['years']}"
        if months > 0:
            output += f" {months} {t['month'] if months == 1 else t['months']}"
        if remaining_days > 0 and months == 0:
            output += f" {remaining_days} {t['day'] if remaining_days == 1 else t['days']}"
        return output

def age_badge(label, days, t):
    return Markup(
        f'<span class="badge bg-success ms-2">{label}: {format_age(days, t)}</span>'
    )

def size_badge(value, unit, age_label, age_days, t):
    size = int(value) if value == int(value) else value
    return Markup(
        f'<span class="badge bg-success ms-2">{size}{unit} '
        f'<small>({age_label}: {format_age(age_days, t)})</small></span>'
    )

def overdue_badge(t):
    return Markup(
        f'<span class="badge bg-danger ms-2">{t["Overdue"]}</span>'
    )

def anytime_soon_badge(days_left, progress, t):
    progress = max(0, min(progress, 100))
    gradient = (
        "bg-success" if progress < 33 else
        "bg-warning" if progress < 66 else
        "bg-danger"
    )
    return Markup(
        f'<span class="badge bg-warning text-dark ms-2 position-relative" style="padding-bottom: 8px;">'
        f'{t["Anytime soon"]} <small>({format_age(days_left, t)})</small>'
        f'<div class="progress position-absolute bottom-0 start-0" '
        f'style="height: 4px; width: 100%; border-radius: 0 0 0.2rem 0.2rem; background: rgba(0,0,0,0.15);">'
        f'<div class="progress-bar {gradient}" style="width: {progress}%"></div>'
        f'</div></span>'
    )

def countdown_badge(days_left, t):
    return Markup(
        f'<span class="badge bg-info text-white ms-2">{format_age(days_left, t)} {t["left"]}</span>'
    )

def done_badge(t):
    return Markup(
        f'<span class="badge bg-secondary ms-2">{t["Done"]}</span>'
    )

def get_daily_unique_logins(start: str | None = None,
                            end:   str | None = None):
    """
    Returns a list of (day, unique_user_count) tuples ordered by day ASC.
    Dates must be 'YYYY-MM-DD'.  Pass none/none for the full history.
    """
    sql   = """
        SELECT day, COUNT(*) AS users
        FROM   user_daily_logins
        WHERE  (?1 IS NULL OR day >= ?1)
           AND (?2 IS NULL OR day <= ?2)
        GROUP  BY day
        ORDER  BY day ASC
    """
    with get_conn() as conn:
        return conn.execute(sql, (start, end)).fetchall()


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

def group_plants_by_state(plants):
    """Groups plants by their state label, returns an OrderedDict (preserves sort order)."""
    from collections import OrderedDict
    state_groups = OrderedDict()
    for plant in plants:
        state = plant.get("state", {}).get("label", "unknown")
        if state.lower() == "dead":
            state = "Dead"
        if state not in state_groups:
            state_groups[state] = []
        state_groups[state].append(plant)
    return state_groups

def build_state_cards(state_groups, include_dead=False):
    """
    Given OrderedDict of {state: [plants]}, returns two lists of cards for left/right columns.
    Each card is a tuple (state, state_plants, is_dead_state)
    Dead plants are excluded unless include_dead=True.
    """
    state_cards = []
    total_plants = sum(len(plants) for plants in state_groups.values())
    for i, (state, plants) in enumerate(state_groups.items()):
        is_dead_state = (state.lower() == "dead")
        is_stash_state = (state.lower() in ("stashed", "ordered"))
        if is_dead_state and not include_dead:
            continue
        if is_stash_state:
            continue
        state_cards.append((state, plants, is_dead_state))
    # Split for columns:
    left, right = [], []
    left_count, right_count = 0, 0
    # Check if any group is "huge"
    for state, plants, is_dead_state in state_cards:
        if len(plants) > (total_plants - len(plants)):
            # Put this giant group alone in left, all others in right
            right = [(state, plants, is_dead_state)]
            left = [c for c in state_cards if c[0] != state]
            return left, right
    # Otherwise, greedily balance by plant count
    for card in state_cards:
        plants = card[1]
        if left_count <= right_count:
            left.append(card)
            left_count += len(plants)
        else:
            right.append(card)
            right_count += len(plants)
    return left, right


def _events_for_plant(conn, plant_id: int) -> List[Dict[str, Any]]:
    """Return a list of event dicts (oldest→newest) for a given plant."""
    rows = conn.execute(
        """
        SELECT e.id, et.code AS action, e.happened_on AS start,
               e.range_min, e.range_min_u, e.range_max, e.range_max_u,
               e.dur_val,  e.dur_unit,
               e.measure_val, e.measure_unit,
               e.custom_label, e.custom_note,
               e.ended_on, e.source, e.acquire_type, e.price, e.price_currency
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
        elif a["action"] == "custom":
            ev["custom_label"] = a["custom_label"]
            ev["custom_note"] = a["custom_note"]
        elif a["action"] == "acquire":
            ev["source"] = a["source"]
            ev["acquire_type"] = a["acquire_type"]
            if a["price"] is not None:
                ev["price"] = a["price"]
                ev["price_currency"] = a["price_currency"]
        elif a["action"] == "order":
            ev["source"] = a["source"]
            if a["ended_on"]:
                ev["expected_on"] = a["ended_on"]
            if a["price"] is not None:
                ev["price"] = a["price"]
                ev["price_currency"] = a["price_currency"]
        if a["action"] in ("flower", "fruit") and a["ended_on"]:
            ev["ended_on"] = a["ended_on"]
        hist.append(ev)
    return hist


def _resolve_state(state: Optional[Dict], history: List[Dict], conn) -> Optional[Dict]:
    """If the most recent flower/fruit event has ended_on <= today, return Growing state instead."""
    if state is None or state.get("label") not in ("Flowering", "Fruiting"):
        return state
    # Find the last flower/fruit event
    for ev in reversed(history):
        if ev["action"] in ("flower", "fruit"):
            ended = ev.get("ended_on")
            if ended and ended <= datetime.today().strftime("%Y-%m-%d"):
                growing = conn.execute(
                    "SELECT code, label, color_class, icon_class FROM state_types WHERE code = 'growing'"
                ).fetchone()
                return dict(growing) if growing else state
            break
    return state


def load_one(plant_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
        if p is None:
            return None
        history = _events_for_plant(conn, plant_id)

        # Fetch the current state
        state = None
        if p["current_state_id"] is not None:
            state_row = conn.execute("""
                SELECT code, label, color_class, icon_class
                FROM state_types WHERE id = ?
            """, (p["current_state_id"],)).fetchone()
            if state_row:
                state = dict(state_row)
        state = _resolve_state(state, history, conn)

    return {
        "user_id": p["user_id"],
        "id": p["id"],
        "common": p["common"],
        "latin": p["latin"],
        "location": p["location"],
        "notes": p["notes"],
        "variety": p["variety"] if "variety" in p.keys() else None,
        "nickname": p["nickname"] if "nickname" in p.keys() else None,
        "count": p["count"] if "count" in p.keys() else 1,
        "batch_id": p["batch_id"] if "batch_id" in p.keys() else None,
        "history": history,
        "current": history[-1] if history else None,
        "state": state,
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
                "variety": p["variety"] if "variety" in p.keys() else None,
                "nickname": p["nickname"] if "nickname" in p.keys() else None,
                "count": p["count"] if "count" in p.keys() else 1,
                "batch_id": p["batch_id"] if "batch_id" in p.keys() else None,
                "history": (hist := _events_for_plant(conn, p["id"])),
                "current": hist[-1] if hist else None,
                "state": _resolve_state(
                    None if p["state_label"] is None else {
                        "label":       p["state_label"],
                        "icon_class":  p["state_icon"],
                        "color_class": p["state_color"],
                    },
                    hist, conn
                ),
                "state_rank": p["state_rank"] if p["state_rank"] is not None else 999
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
    elif ev["action"] == "custom":
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, custom_label, custom_note)"
            " VALUES (?,?,?,?,?)",
            base_vals + (ev["custom_label"], ev["custom_note"]),
        )
    elif ev["action"] == "acquire":
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, source, acquire_type, price, price_currency)"
            " VALUES (?,?,?,?,?,?,?)",
            base_vals + (ev.get("source") or None, ev.get("acquire_type") or None,
                         ev.get("price") or None, ev.get("price_currency") or None),
        )
    elif ev["action"] == "order":
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, source, ended_on, price, price_currency)"
            " VALUES (?,?,?,?,?,?,?)",
            base_vals + (ev.get("source") or None, ev.get("expected_on") or None,
                         ev.get("price") or None, ev.get("price_currency") or None),
        )
    elif ev["action"] in ("flower", "fruit") and ev.get("ended_on"):
        cur.execute(
            f"INSERT INTO events ({','.join(base_cols)}, ended_on) VALUES (?,?,?,?)",
            base_vals + (ev["ended_on"],),
        )
    else:
        cur.execute(f"INSERT INTO events ({','.join(base_cols)}) VALUES (?,?,?)", base_vals)

    # reflect new state (if any)
    if et["new_state_id"] is not None:
        cur.execute("UPDATE plants SET current_state_id = ? WHERE id = ?", (et["new_state_id"], plant_id))


###############################################################################
# Public CRUD helpers
###############################################################################

def save_new_plant(plant_dict: Dict[str, Any], first_event: Dict[str, Any], user_id: int) -> int:
    """Insert a **brand‑new** plant plus its first event in one transaction."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO plants (common, latin, location, notes, variety, nickname, count, batch_id, user_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                plant_dict["common"],
                plant_dict["latin"],
                plant_dict.get("location"),
                plant_dict.get("notes"),
                plant_dict.get("variety") or None,
                plant_dict.get("nickname") or None,
                plant_dict.get("count", 1) or 1,
                plant_dict.get("batch_id") or None,
                user_id,
            ),
        )
        plant_id = cur.lastrowid
        _insert_event(cur, plant_id, first_event)
        conn.commit()
        return plant_id


def update_plant(plant_id: int, plant_dict: Dict[str, Any], new_event: Optional[Dict[str, Any]] = None) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE plants SET common = ?, latin = ?, location = ?, notes = ?, variety = ?, nickname = ?, count = ? WHERE id = ?""",
            (
                plant_dict["common"],
                plant_dict["latin"],
                plant_dict.get("location"),
                plant_dict.get("notes"),
                plant_dict.get("variety") or None,
                plant_dict.get("nickname") or None,
                plant_dict.get("count", 1) or 1,
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
        elif ev["action"] == "custom":
            cur.execute(
                """UPDATE events SET event_type_id = ?, happened_on = ?,
                        custom_label = ?, custom_note = ?
                WHERE id = ?""",
                base + (ev["custom_label"], ev["custom_note"], event_id),
            )

        elif ev["action"] in ("flower", "fruit"):
            cur.execute(
                "UPDATE events SET event_type_id = ?, happened_on = ?, ended_on = ? WHERE id = ?",
                base + (ev.get("ended_on") or None, event_id),
            )
        elif ev["action"] == "order":
            cur.execute(
                """UPDATE events SET event_type_id = ?, happened_on = ?,
                       source = ?, ended_on = ?, price = ?, price_currency = ?
                   WHERE id = ?""",
                base + (ev.get("source") or None, ev.get("expected_on") or None,
                        ev.get("price") or None, ev.get("price_currency") or None, event_id),
            )
        elif ev["action"] == "acquire":
            cur.execute(
                """UPDATE events SET event_type_id = ?, happened_on = ?,
                       source = ?, acquire_type = ?, price = ?, price_currency = ?
                   WHERE id = ?""",
                base + (ev.get("source") or None, ev.get("acquire_type") or None,
                        ev.get("price") or None, ev.get("price_currency") or None, event_id),
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
                   e.custom_label, e.custom_note,
                   e.ended_on, e.source, e.acquire_type, e.price, e.price_currency
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
        elif a["action"] == "custom":
            act["custom_label"] = a["custom_label"]
            act["custom_note"] = a["custom_note"]
        if a["action"] in ("flower", "fruit") and a["ended_on"]:
            act["ended_on"] = a["ended_on"]
        if a["action"] == "order":
            act["source"] = a["source"]
            act["expected_on"] = a["ended_on"]
            act["price"] = a["price"]
            act["price_currency"] = a["price_currency"]
        elif a["action"] == "acquire":
            act["source"] = a["source"]
            act["acquire_type"] = a["acquire_type"]
            act["price"] = a["price"]
            act["price_currency"] = a["price_currency"]
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


def explode_plant(plant_id: int, count: int, user_id: int) -> List[int]:
    """
    Split a plant record into `count` individual plant records.
    The original becomes member #1; count-1 new records are created.
    All members share the same batch_id (= the original plant's id).
    Returns list of all member IDs (including original).
    """
    plant = load_one(plant_id)
    if plant is None or plant["user_id"] != user_id:
        return []
    count = max(2, count)
    with get_conn() as conn:
        cur = conn.cursor()
        # Mark original as member #1 of its own batch
        cur.execute(
            "UPDATE plants SET count = 1, batch_id = ? WHERE id = ?",
            (plant_id, plant_id),
        )
        new_ids = [plant_id]
        for _ in range(count - 1):
            cur.execute(
                "INSERT INTO plants (common, latin, location, notes, variety, nickname, count, batch_id, user_id, current_state_id) "
                "SELECT common, latin, location, notes, variety, nickname, 1, ?, user_id, current_state_id "
                "FROM plants WHERE id = ?",
                (plant_id, plant_id),
            )
            new_plant_id = cur.lastrowid
            new_ids.append(new_plant_id)
            # Copy all events to the new plant
            events = conn.execute(
                "SELECT event_type_id, happened_on, range_min, range_min_u, range_max, range_max_u, "
                "dur_val, dur_unit, measure_val, measure_unit, custom_label, custom_note, source, acquire_type, price, price_currency "
                "FROM events WHERE plant_id = ? ORDER BY happened_on, id",
                (plant_id,),
            ).fetchall()
            for ev in events:
                cur.execute(
                    "INSERT INTO events (plant_id, event_type_id, happened_on, range_min, range_min_u, "
                    "range_max, range_max_u, dur_val, dur_unit, measure_val, measure_unit, custom_label, custom_note, source, acquire_type, price, price_currency) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_plant_id,) + tuple(ev),
                )
        conn.commit()
    return new_ids


def group_by_batch(plants: List[Dict[str, Any]]) -> List[Tuple[Optional[int], List[Dict[str, Any]]]]:
    """
    Group a list of plants into batches.
    Returns list of (batch_id_or_None, [plants]).
    Unbatched plants each get their own singleton group.
    Batched plants are grouped together, ordered by batch_id.
    """
    batches: dict = {}
    result = []
    for p in plants:
        bid = p.get("batch_id")
        if bid is None:
            result.append((None, [p]))
        else:
            if bid not in batches:
                batches[bid] = []
                result.append((bid, batches[bid]))
            batches[bid].append(p)
    return result


def group_by_latin(plants: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """
    Group plants by latin name (case-insensitive).
    Returns list of (latin_display, [plants]), preserving first-appearance order.
    A single plant still gets its own group of length 1.
    """
    seen: dict = {}
    result = []
    for p in plants:
        key = (p.get("latin") or "").strip().lower()
        if not key:
            # No latin name: treat each plant as its own group
            result.append((p.get("common") or "", [p]))
        elif key not in seen:
            seen[key] = []
            result.append((p.get("latin") or "", seen[key]))
            seen[key].append(p)
        else:
            seen[key].append(p)
    return result

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


###############################################################################
# Location hierarchy helpers
###############################################################################

LOCATION_SEP = "/"

def parse_location_path(location_str: Optional[str]) -> List[str]:
    """Split a location string into path segments using / as separator."""
    if not location_str:
        return []
    return [s.strip() for s in location_str.split(LOCATION_SEP) if s.strip()]


def build_location_tree(plants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build a flat, depth-annotated list of location nodes from a list of plants.
    Each node: {label, full_path, depth, count}
    Sorted top-level alphabetically, children follow their parent immediately.
    Plants with no location accumulate under full_path=None.
    """
    from collections import defaultdict, OrderedDict

    # count how many plants live at each exact full_path
    path_counts: Dict[str, int] = defaultdict(int)
    no_location_count = 0

    for p in plants:
        path = parse_location_path(p.get("location"))
        if not path:
            no_location_count += 1
        else:
            full = LOCATION_SEP.join(path)
            path_counts[full] += 1
            # also credit every ancestor
            for depth in range(1, len(path)):
                ancestor = LOCATION_SEP.join(path[:depth])
                path_counts[ancestor] += 0  # ensure key exists but don't double-count

    # build a set of all nodes (including intermediates)
    all_nodes: Dict[str, List[str]] = {}  # full_path -> path segments
    for p in plants:
        path = parse_location_path(p.get("location"))
        for depth in range(1, len(path) + 1):
            fp = LOCATION_SEP.join(path[:depth])
            if fp not in all_nodes:
                all_nodes[fp] = path[:depth]

    # compute cumulative count for each node (plants at node + all descendants)
    def count_for(fp: str) -> int:
        return sum(
            cnt for pfp, cnt in path_counts.items()
            if pfp == fp or pfp.startswith(fp + LOCATION_SEP)
        )

    # build flat sorted list depth-first
    result: List[Dict[str, Any]] = []

    def _recurse(prefix: Optional[str], depth: int):
        children = sorted(
            (fp for fp, segs in all_nodes.items()
             if len(segs) == depth + 1 and (
                 prefix is None and depth == 0 or
                 (prefix is not None and fp.startswith(prefix + LOCATION_SEP) and len(fp.split(LOCATION_SEP)) == depth + 1)
                 or (depth == 0)
             )),
        )
        # top level: segments of length 1
        if depth == 0:
            children = sorted(fp for fp, segs in all_nodes.items() if len(segs) == 1)
        else:
            children = sorted(
                fp for fp, segs in all_nodes.items()
                if len(segs) == depth + 1 and fp.startswith(prefix + LOCATION_SEP)
            )
        for fp in children:
            segs = all_nodes[fp]
            result.append({
                "label":     segs[-1],
                "full_path": fp,
                "depth":     depth,
                "count":     count_for(fp),
            })
            _recurse(fp, depth + 1)

    _recurse(None, 0)

    if no_location_count:
        result.append({
            "label":     None,
            "full_path": None,
            "depth":     0,
            "count":     no_location_count,
        })

    return result


###############################################################################
# Attention / urgency helpers
###############################################################################

def compute_attention(plants: List[Dict[str, Any]]) -> Dict[str, List]:
    """
    Return dict of plants that require attention, grouped by alert type:
      overdue_sow   – sow events past the max germination date
      strat_done    – stratification periods that have elapsed
      soak_done     – soaking periods that have elapsed
      anytime_soon  – sow events currently inside the germination window
      coming_up     – sow events whose window opens within 7 days
    """
    from datetime import date as _date
    today = _date.today()

    overdue_sow: List[Dict] = []
    strat_done:  List[Dict] = []
    soak_done:   List[Dict] = []
    anytime_soon: List[Dict] = []
    coming_up:   List[Dict] = []

    for plant in plants:
        state = plant.get("state")
        if state and state.get("label") in ("Dead", "Stashed"):
            continue
        curr = plant.get("current") or {}
        action = curr.get("action", "")
        start_str = curr.get("start")
        if not start_str:
            continue
        try:
            from datetime import date as _date2
            start = _date2.fromisoformat(start_str)
        except (ValueError, TypeError):
            continue

        if action == "sow" and "range" in curr:
            min_val, min_unit, max_val, max_unit = curr["range"]
            min_days = duration_to_days(min_val, min_unit)
            max_days = duration_to_days(max_val, max_unit)
            from datetime import timedelta as _td
            min_date = start + _td(days=min_days)
            max_date = start + _td(days=max_days)
            if today > max_date:
                overdue_sow.append(plant)
            elif today >= min_date:
                anytime_soon.append({"plant": plant, "days_left": (max_date - today).days})
            elif (min_date - today).days <= 7:
                coming_up.append({"plant": plant, "days_until": (min_date - today).days})

        elif action == "strat" and "duration" in curr:
            val, unit = curr["duration"]
            from datetime import timedelta as _td
            end_date = start + _td(days=duration_to_days(val, unit))
            if today >= end_date:
                strat_done.append(plant)

        elif action == "soak" and "duration" in curr:
            val, unit = curr["duration"]
            from datetime import timedelta as _td
            end_date = start + _td(days=duration_to_days(val, unit))
            if today >= end_date:
                soak_done.append(plant)

    return {
        "overdue_sow":  overdue_sow,
        "strat_done":   strat_done,
        "soak_done":    soak_done,
        "anytime_soon": anytime_soon,
        "coming_up":    coming_up,
    }


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
        "variety": "",
        "nickname": "",
        "count": 1,
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
        "event_ended_on": "",
        "event_source": "",
        "event_acquire_type": "bought",
        "event_price": "",
        "event_price_currency": "EUR",
        "event_order_price": "",
        "event_order_price_currency": "EUR",
        "notes": "",
    }



def get_form_data(request):
    return {
        "common": request.form.get("common", "").strip(),
        "latin": request.form.get("latin", "").strip(),
        "variety": request.form.get("variety", "").strip(),
        "nickname": request.form.get("nickname", "").strip(),
        "count": max(1, to_int(request.form.get("count", 1))),
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
        "event_custom_label": request.form.get("event_custom_label", "").strip(),
        "event_custom_note": request.form.get("event_custom_note", "").strip(),
        "event_ended_on": request.form.get("event_ended_on", "").strip(),
        "event_source": request.form.get("event_source", "").strip(),
        "event_acquire_type": request.form.get("event_acquire_type", "bought").strip(),
        "event_price": request.form.get("event_price", "").strip(),
        "event_price_currency": request.form.get("event_price_currency", "").strip(),
        "event_order_price": request.form.get("event_order_price", "").strip(),
        "event_order_price_currency": request.form.get("event_order_price_currency", "").strip(),
    }


def get_form_from_plant(plant):
    form = get_empty_form()
    form["common"] = plant["common"]
    form["latin"] = plant["latin"]
    form["variety"] = plant.get("variety", "") or ""
    form["nickname"] = plant.get("nickname", "") or ""
    form["count"] = plant.get("count", 1) or 1
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
    
    elif first["action"] == "custom":
        form["event_custom_label"] = first.get("custom_label", "")
        form["event_custom_note"] = first.get("custom_note", "")



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
    elif status == "custom":
        label = form.get("event_custom_label", "").strip()
        if not label:
            errors.append("Custom event label is required.")
        event = {
            "action": "custom",
            "start": date,
            "custom_label": label,
            "custom_note": form.get("event_custom_note", "").strip(),
        }



    elif status == "acquire":
        raw_price = form.get("event_price", "").strip()
        price = float(raw_price) if raw_price else None
        event = {
            "action": "acquire",
            "start": date,
            "source": form.get("event_source", "").strip() or None,
            "acquire_type": form.get("event_acquire_type", "bought").strip() or "bought",
            "price": price,
            "price_currency": form.get("event_price_currency", "").strip() or None if price is not None else None,
        }
    elif status == "order":
        raw_price = form.get("event_order_price", "").strip()
        price = float(raw_price) if raw_price else None
        event = {
            "action": "order",
            "start": date,
            "source": form.get("event_source", "").strip() or None,
            "expected_on": form.get("event_ended_on", "").strip() or None,
            "price": price,
            "price_currency": form.get("event_order_price_currency", "").strip() or None if price is not None else None,
        }
    elif status in ("flower", "fruit"):
        ended = form.get("event_ended_on", "").strip() or None
        event = {"action": status, "start": date}
        if ended:
            event["ended_on"] = ended
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
    elif action == "custom":
        extras.update({
            "event_custom_label": action_dict.get("custom_label", ""),
            "event_custom_note": action_dict.get("custom_note", ""),
        })
    if action in ("flower", "fruit"):
        extras["event_ended_on"] = action_dict.get("ended_on", "")
    elif action == "order":
        extras["event_source"] = action_dict.get("source") or ""
        extras["event_ended_on"] = action_dict.get("expected_on") or ""
        extras["event_order_price"] = action_dict["price"] if action_dict.get("price") is not None else ""
        extras["event_order_price_currency"] = action_dict.get("price_currency") or ""
    elif action == "acquire":
        extras["event_source"] = action_dict.get("source") or ""
        extras["event_acquire_type"] = action_dict.get("acquire_type") or "bought"
        extras["event_price"] = action_dict["price"] if action_dict.get("price") is not None else ""
        extras["event_price_currency"] = action_dict.get("price_currency") or ""

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
