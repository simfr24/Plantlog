import os
import sqlite3

def migrate(old_db, new_db):
    if not os.path.exists(old_db):
        print("No old_plants.db found—skipping migration.")
        return

    old = sqlite3.connect(old_db)
    old.row_factory = sqlite3.Row
    new = sqlite3.connect(new_db)
    new.row_factory = sqlite3.Row

    with new:
        # copy users
        for u in old.execute("SELECT * FROM users"):
            new.execute(
                "INSERT OR IGNORE INTO users (id,username,pw_hash,lang) VALUES (?,?,?,?)",
                (u['id'],u['username'],u['pw_hash'],u['lang'])
            )

        # copy plants
        for p in old.execute("SELECT * FROM plants"):
            new.execute(
                """INSERT OR IGNORE INTO plants
                   (id,common,latin,location,notes,user_id)
                   VALUES (?,?,?,?,?,?)""",
                (p['id'],p['common'],p['latin'],p['location'],p['notes'],p['user_id'])
            )

        # copy actions → events
        for a in old.execute("SELECT * FROM actions ORDER BY start asc"):
            et = new.execute(
                "SELECT id,new_state_id FROM event_types WHERE code=?",
                (a['action'],)
            ).fetchone()
            if not et:
                # This shouldn’t happen now that we've seeded event_types
                continue

            new.execute(
                """INSERT INTO events
                   (plant_id,event_type_id,happened_on,
                    range_min,range_min_u,range_max,range_max_u,
                    dur_val,dur_unit)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (a['plant_id'], et['id'], a['start'],
                 a['range_min'], a['range_min_u'],
                 a['range_max'], a['range_max_u'],
                 a['dur_val'], a['dur_unit'])
            )
            if et['new_state_id'] is not None:
                new.execute(
                    "UPDATE plants SET current_state_id=? WHERE id=?",
                    (et['new_state_id'], a['plant_id'])
                )

    print("Reset and migration complete.")