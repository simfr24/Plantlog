import os
import json
from datetime import datetime, timedelta
from py.db import get_conn

def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def load_one(plant_id):
    """
    Return a single plant dict (like load_data()[…]) with keys:
      - id, common, latin, location, notes
      - history: list of action-dicts oldest→newest
      - current: last action dict (or None)
    """
    with get_conn() as conn:
        p = conn.execute(
            "SELECT * FROM plants WHERE id = ?", (plant_id,)
        ).fetchone()
        if p is None:
            return None

        actions = conn.execute("""
            SELECT id, action, start,
                   range_min, range_min_u, range_max, range_max_u,
                   dur_val, dur_unit
            FROM actions
            WHERE plant_id = ?
            ORDER BY start
        """, (plant_id,)).fetchall()

    history = []
    for a in actions:
        act = {"id": a["id"], "action": a["action"], "start": a["start"]}
        if a["action"] == "sow":
            act["range"] = [
                a["range_min"], a["range_min_u"],
                a["range_max"], a["range_max_u"]
            ]
        elif a["action"] in ("soak", "strat"):
            act["duration"] = [a["dur_val"], a["dur_unit"]]
        history.append(act)

    return {
        "id":       p["id"],
        "common":   p["common"],
        "latin":    p["latin"],
        "location": p["location"],
        "notes":    p["notes"],
        "history":  history,
        "current":  history[-1] if history else None
    }

def load_data(user_id=None):
    """Return a list of plant dicts, each with:
       - history: list of actions oldest→newest
       - current: the last action dict (i.e. history[-1])"""
    with get_conn() as conn:
        plants = conn.execute(
            "SELECT * FROM plants WHERE user_id = ?",
            (user_id,)
        ).fetchall()

    out = []
    for p in plants:
        # fetch actions sorted by start date ASC
        actions = conn.execute("""
            SELECT id, plant_id, action, start,
                range_min, range_min_u, range_max, range_max_u,
                dur_val, dur_unit
            FROM actions WHERE plant_id = ? ORDER BY start
        """, (p["id"],)).fetchall()

        history = []
        for a in actions:
            act = {"action": a["action"], "start": a["start"]}
            act['id'] = a['id']
            if a["action"] == "sow":
                act["range"] = [a["range_min"], a["range_min_u"],
                                a["range_max"], a["range_max_u"]]
            elif a["action"] in ("soak", "strat"):
                act["duration"] = [a["dur_val"], a["dur_unit"]]
            history.append(act)

        out.append({
            "id":       p["id"],
            "common":   p["common"],
            "latin":    p["latin"],
            "location": p["location"],
            "notes":    p["notes"],
            "history":  history,
            "current":  history[-1] if history else None
        })
    return out



def save_new_plant(plant_dict, first_action,user_id):
    """Insert a brand‑new plant + its initial action in one transaction."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO plants (common,latin,location,notes,user_id) VALUES (?,?,?,?,?)",
            (plant_dict["common"], plant_dict["latin"],
             plant_dict.get("location"), plant_dict.get("notes")
             , user_id)
        )
        plant_id = cur.lastrowid
        _insert_action(cur, plant_id, first_action)
        conn.commit()


def update_plant(idx, plant_dict, new_action=None):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE plants
               SET common=?, latin=?, location=?, notes=?
               WHERE id=?""",
            (plant_dict["common"], plant_dict["latin"],
             plant_dict.get("location"), plant_dict.get("notes"), idx)
        )
        if new_action:
            _insert_action(cur, idx, new_action)
        conn.commit()


def _insert_action(cur, plant_id, action):
    base_cols = ("plant_id", "action", "start")
    base_vals = (plant_id, action["action"], action["start"])

    if action["action"] == "sow":
        # 3 base + 4 range = 7 columns  → 7 placeholders ✔
        cur.execute(
            f"""INSERT INTO actions ({','.join(base_cols)},
                range_min, range_min_u, range_max, range_max_u)
            VALUES (?,?,?,?,?,?,?)""",
            base_vals + tuple(action["range"])
        )

    elif action["action"] in ("soak", "strat"):
        # 3 base + 2 duration = 5 columns  → **5** placeholders
        cur.execute(
            f"""INSERT INTO actions ({','.join(base_cols)},
                dur_val, dur_unit)
            VALUES (?,?,?,?,?)""",          # <-- was 6 ?, now 5
            base_vals + tuple(action["duration"])
        )

    else:  # sprout
        # 3 columns  → 3 placeholders
        cur.execute(
            f"""INSERT INTO actions ({','.join(base_cols)})
            VALUES (?,?,?)""",
            base_vals
        )



def duration_to_days(val, unit):
    """Convert duration to days"""
    if unit == 'months':
        return val * 30
    elif unit == 'weeks':
        return val * 7
    elif unit == 'hours':
        return round(val / 24)
    return val


def get_translations(lang):
    """Load translations for the specified language"""
    if lang == 'fr':
        from translations.fr import translations as tr
    else:
        from translations.en import translations as tr
    return tr


def dateadd(value, days=0, weeks=0, months=0):
    """Add days/weeks/months to a date string or object"""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value
    days += weeks * 7 + months * 30
    return value + timedelta(days=days)


def get_empty_form():
    """Return a new, empty form dictionary"""
    return {
        'common': '', 'latin': '', 'status': 'sow',
        'date_sow': '', 'sprout_min': 0, 'sprout_min_u': 'days',
        'sprout_max': 0, 'sprout_max_u': 'days',
        'soak_date': '', 'soak_val': 0, 'soak_unit': 'hours',
        'strat_date': '', 'strat_val': 0, 'strat_unit': 'days',
        'sprout_date': '', 'location': '', 'notes': ''
    }


def get_form_data(request):
    """Extract and normalize form data from request"""
    return {
        'common': request.form.get('common', '').strip(),
        'latin': request.form.get('latin', '').strip(),
        'status': request.form.get('status', ''),
        'date_sow': request.form.get('date_sow', ''),
        'sprout_min': to_int(request.form.get('sprout_min')),
        'sprout_min_u': request.form.get('sprout_min_u', 'days'),
        'sprout_max': int(request.form.get('sprout_max', 0) or 0),
        'sprout_max_u': request.form.get('sprout_max_u', 'days'),
        'soak_date': request.form.get('soak_date', ''),
        'soak_val': int(request.form.get('soak_val', 0) or 0),
        'soak_unit': request.form.get('soak_unit', 'hours'),
        'strat_date': request.form.get('strat_date', ''),
        'strat_val': int(request.form.get('strat_val', 0) or 0),
        'strat_unit': request.form.get('strat_unit', 'days'),
        'sprout_date': request.form.get('sprout_date', ''),
        'location': request.form.get('location', '').strip(),
        'notes': request.form.get('notes', '').strip()
    }


def get_form_from_plant(plant):
    """Convert a plant record into form-compatible structure"""
    form = get_empty_form()
    form['common'] = plant['common']
    form['latin'] = plant['latin']
    form['location'] = plant.get('location', '') or ''
    form['notes'] = plant.get('notes') or ''

    first = plant['history'][0]
    form['status'] = first['action']

    if first['action'] == 'sow' and 'range' in first and len(first['range']) >= 4:
        form['date_sow'] = first['start']
        form['sprout_min'] = first['range'][0]
        form['sprout_min_u'] = first['range'][1]
        form['sprout_max'] = first['range'][2]
        form['sprout_max_u'] = first['range'][3]
    elif first['action'] == 'soak':
        form['soak_date'] = first['start']
        form['soak_val'] = first['duration'][0]
        form['soak_unit'] = first['duration'][1]
    elif first['action'] == 'strat':
        form['strat_date'] = first['start']
        form['strat_val'] = first['duration'][0]
        form['strat_unit'] = first['duration'][1]
    elif first['action'] == 'sprout':
        form['sprout_date'] = first['start']

    return form


def validate_form(form, translations, context="add"):
    """Validate plant metadata and (optionally) action. Return errors + structured action"""
    errors = []
    action = None

    if context in ('add', 'edit'):
        if not form.get('common'):
            errors.append(f"{translations['Common name']} required.")
        if not form.get('latin'):
            errors.append(f"{translations['Latin name']} required.")

    # If status is absent or empty, skip action validation (metadata-only edit)
    status = form.get('status', '')
    if not status:
        return errors, None

    # Validate action fields
    if status == 'sow':
        if not form.get('date_sow'):
            errors.append(f"{translations['Sow date']} required.")
        min_days = duration_to_days(form.get('sprout_min', 0), form.get('sprout_min_u', 'days'))
        max_days = duration_to_days(form.get('sprout_max', 0), form.get('sprout_max_u', 'days'))
        if min_days <= 0 or max_days <= 0:
            errors.append("Sprout range must be > 0.")
        if min_days > max_days:
            errors.append("Sprout min > max.")
        action = {
            'action': 'sow',
            'start': form['date_sow'],
            'range': [
                form['sprout_min'], form['sprout_min_u'],
                form['sprout_max'], form['sprout_max_u']
            ]
        }
    elif status == 'soak':
        if not form.get('soak_date'):
            errors.append(f"{translations['Soak start date']} required.")
        if form.get('soak_val', 0) <= 0:
            errors.append("Duration > 0.")
        action = {
            'action': 'soak',
            'start': form['soak_date'],
            'duration': [form['soak_val'], form['soak_unit']]
        }
    elif status == 'strat':
        if not form.get('strat_date'):
            errors.append(f"{translations['Strat start date']} required.")
        if form.get('strat_val', 0) <= 0:
            errors.append("Duration > 0.")
        action = {
            'action': 'strat',
            'start': form['strat_date'],
            'duration': [form['strat_val'], form['strat_unit']]
        }
    elif status == 'sprout':
        if not form.get('sprout_date'):
            errors.append(f"{translations['Sprouted on']} required.")
        action = {'action': 'sprout', 'start': form['sprout_date']}
    else:
        errors.append("Unknown status.")

    return errors, action

def update_action(action_id, action):
    with get_conn() as conn:
        cur = conn.cursor()
        base = (action['action'], action['start'])
        if action['action']=='sow':
            cur.execute(
              """UPDATE actions
                 SET action=?, start=?, range_min=?, range_min_u=?, range_max=?, range_max_u=?
                 WHERE id=?""",
              base + tuple(action['range']) + (action_id,)
            )
        elif action['action'] in ('soak','strat'):
            cur.execute(
              """UPDATE actions
                 SET action=?, start=?, dur_val=?, dur_unit=?
                 WHERE id=?""",
              base + tuple(action['duration']) + (action_id,)
            )
        else:
            cur.execute(
              "UPDATE actions SET action=?, start=? WHERE id=?",
              base + (action_id,)
            )
        conn.commit()



def get_action_by_id(action_id):
    """Fetch a single action dict by its ID, including plant metadata."""
    with get_conn() as conn:
        a = conn.execute(
            """
            SELECT id, plant_id, action, start,
                   range_min, range_min_u, range_max, range_max_u,
                   dur_val, dur_unit
            FROM actions
            WHERE id = ?
            """,
            (action_id,)
        ).fetchone()
        if a is None:
            return None
        # also fetch plant metadata
        p = conn.execute(
            "SELECT common, latin FROM plants WHERE id = ?",
            (a[1],)
        ).fetchone()

        act = {
            "id": a[0],
            "plant_id": a[1],
            "action": a[2],
            "start": a[3],
            "common": p[0],
            "latin": p[1]
        }
        if a[2] == "sow":
            act["range"] = [a[4], a[5], a[6], a[7]]
        elif a[2] in ("soak", "strat"):
            act["duration"] = [a[8], a[9]]
        return act

def form_keys_for(action_dict):
    """Return date field key and extras for pre-filling the form."""
    # always include plant metadata
    extras = {
        'common': action_dict.get('common', ''),
        'latin': action_dict.get('latin', '')
    }
    action = action_dict.get('action')
    if action == 'sow':
        extras.update({
            'sprout_min': action_dict['range'][0],
            'sprout_min_u': action_dict['range'][1],
            'sprout_max': action_dict['range'][2],
            'sprout_max_u': action_dict['range'][3]
        })
        return 'date_sow', extras
    elif action == 'soak':
        extras.update({
            'soak_val': action_dict['duration'][0],
            'soak_unit': action_dict['duration'][1]
        })
        return 'soak_date', extras
    elif action == 'strat':
        extras.update({
            'strat_val': action_dict['duration'][0],
            'strat_unit': action_dict['duration'][1]
        })
        return 'strat_date', extras
    elif action == 'sprout':
        return 'sprout_date', extras
    else:
        return '', extras

def process_delete_plant(plant_id, user_id):
    with get_conn() as conn:
        # Ensure the plant belongs to the user
        plant = conn.execute(
            "SELECT id FROM plants WHERE id = ? AND user_id = ?", (plant_id, user_id)
        ).fetchone()
        if not plant:
            return  # Do nothing if the plant does not belong to the user

        conn.execute("DELETE FROM actions WHERE plant_id = ?", (plant_id,))
        conn.execute("DELETE FROM plants  WHERE id       = ?", (plant_id,))
        conn.commit()

def process_delete_action(action_id, user_id):
    with get_conn() as conn:
        # Ensure the action belongs to a plant owned by the user
        action = conn.execute(
            """
            SELECT a.id FROM actions a
            JOIN plants p ON a.plant_id = p.id
            WHERE a.id = ? AND p.user_id = ?
            """,
            (action_id, user_id)
        ).fetchone()
        if not action:
            return  # Do nothing if the action does not belong to the user's plant

        conn.execute("DELETE FROM actions WHERE id = ?", (action_id,))
        conn.commit()