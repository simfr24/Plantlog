from datetime import datetime, timedelta
from py.helpers import duration_to_days

def sort_key(plant):
    now = datetime.now()
    order = {'soak': 0, 'strat': 1, 'sow': 2, 'sprout': 3}

    curr = plant["current"] or {}
    action = curr.get("action", "")
    stage_rank = order.get(action, 99)

    # compute remaining exactly as before
    if action == "sow" and "range" in curr:
        min_d = duration_to_days(*curr["range"][:2])
        start = datetime.strptime(curr["start"], "%Y-%m-%d")
        remaining = max(0, (start + timedelta(days=min_d) - now).days)
    elif action in ("soak", "strat"):
        days = duration_to_days(*curr["duration"])
        start = datetime.strptime(curr["start"], "%Y-%m-%d")
        remaining = max(0, (start + timedelta(days=days) - now).days)
    elif action == "sprout":
        remaining = 9999
    else:
        remaining = 9999

    return (stage_rank, remaining)

def get_unique_locations(plants):
    return list({plant.get('location') for plant in plants if plant.get('location')})
