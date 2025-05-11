from werkzeug.security import generate_password_hash, check_password_hash 
from py.db import get_conn
from flask import session, g
from datetime import date

def create_user(username: str, password: str, lang: str = "en"):
    today = date.today().isoformat()  # YYYY-MM-DD
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (username, pw_hash, lang, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (username.lower(), generate_password_hash(password), lang, today)
        )
        conn.commit()

def get_user_by_username(username: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.lower(),)
        ).fetchone()

def get_all_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()

def get_user_by_id(uid: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

def update_user_lang(uid: int, lang: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET lang=? WHERE id=?", (lang, uid))
        conn.commit()

def login_user(user_row):
    session["uid"] = user_row["id"]

def record_user_login(user_id: int):
    """
    Ensures that `last_login` and the daily login table are updated,
    even if the user is just resuming a session.
    """
    today = date.today().isoformat()

    with get_conn() as conn:
        # Update last_login timestamp
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?",
            (user_id,),
        )

        # Insert today's login if not already present
        conn.execute(
            """
            INSERT OR IGNORE INTO user_daily_logins (user_id, day)
            VALUES (?, ?)
            """,
            (user_id, today),
        )
        conn.commit()

def logout_user():
    session.pop("uid", None)
