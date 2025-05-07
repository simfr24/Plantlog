from datetime import datetime, timedelta
from py.helpers import duration_to_days

def sort_key(plant):
    """
    Primary key  ➜ state_rank   (comes from state_types.sort_rank)
    Secondary key➜ remaining days until the *next* interesting date
                  (so more urgent plants bubble up inside a rank)
    """
    rank = plant.get("state_rank", 999)

    # ── compute “remaining days” only for time-sensitive states ────────────
    curr = plant.get("current") or {}
    action = curr.get("action", "")
    today  = datetime.now()

    remaining = 9999  # default: bottom within the same rank

    try:
        start = datetime.strptime(curr["start"], "%Y-%m-%d")
    except Exception:
        return (rank, remaining)          # no valid date ⇒ keep default

    if action == "sow" and "range" in curr:
        min_days = duration_to_days(curr["range"][0], curr["range"][1])
        remaining = max(0, (start + timedelta(days=min_days) - today).days)

    elif action in ("soak", "strat") and "duration" in curr:
        dur_days  = duration_to_days(curr["duration"][0], curr["duration"][1])
        remaining = max(0, (start + timedelta(days=dur_days) - today).days)

    # sprout, dead, etc. keep remaining = 9999

    return (rank, remaining)

def get_unique_locations(plants):
    return list({plant.get('location') for plant in plants if plant.get('location')})
