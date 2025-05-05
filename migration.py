# helpers.py  ──────────────────────────────────────────────────────────────
import json
from pathlib import Path
from typing import List, Dict, Union

from py.db import get_conn, init_db          # uses the existing DB helpers:contentReference[oaicite:0]{index=0}:contentReference[oaicite:1]{index=1}
from py.helpers import _insert_action        # re‑use the proven insert logic:contentReference[oaicite:2]{index=2}:contentReference[oaicite:3]{index=3}


def migrate_plants_from_json(json_source: Union[str, Path, List[Dict]]) -> None:
    """
    One‑shot migration of legacy plants.json into the database.

    Parameters
    ----------
    json_source :  • path/str/Path ‑ path to a JSON file **or**
                   • list[dict]    ‑ already‑parsed JSON array

    The function is idempotent **per run** (it never deletes rows),
    so run it only once unless you wipe the tables first.
    """
    # 1. Load the data ----------------------------------------------------------------
    if isinstance(json_source, (str, Path)):
        with open(json_source, "r", encoding="utf‑8") as fh:
            plants = json.load(fh)
    else:
        plants = json_source                      # assume already a list[dict]

    if not plants:
        print("🛈 No plants to migrate.")
        return

    # 2. Make sure the schema exists --------------------------------------------------
    init_db()                                    # safe to call twice

    # 3. Bulk‑insert plants + all their history in one transaction --------------------
    with get_conn() as conn:
        cur = conn.cursor()

        for plant in plants:
            # --- insert row into plants ---------------------------------------------
            cur.execute(
                """INSERT INTO plants (common, latin, location, notes)
                   VALUES (?,?,?,?)""",
                (
                    plant["common"],
                    plant["latin"],
                    plant.get("location"),
                    plant.get("notes"),
                ),
            )
            plant_id = cur.lastrowid

            # --- insert every history action ----------------------------------------
            for act in plant.get("history", []):
                _insert_action(cur, plant_id, act)   # uses the existing mapping

        conn.commit()

    print(f"✓ Migrated {len(plants)} plants with full history.")


# -------------------------------------------------------------------------------
# OPTIONAL command‑line entry point for convenience
#    python -m py.helpers.migrate /path/to/legacy_plants.json
# -------------------------------------------------------------------------------
if __name__ == "__main__":             # pragma: no cover
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m py.helpers.migrate path/to/plants.json")
        sys.exit(1)
    migrate_plants_from_json(Path(sys.argv[1]))
