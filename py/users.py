import hashlib
import secrets

from werkzeug.security import generate_password_hash, check_password_hash
from py.db import get_conn
from flask import session, g
from datetime import date


def _hash_key(raw: str) -> str:
    """SHA-256 hash of a raw API key for storage."""
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key(user_id: int) -> str:
    """Generate a new random API key for the user and store it."""
    raw = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET api_key_hash = ?, api_key = ? WHERE id = ?",
            (_hash_key(raw), raw, user_id),
        )
        conn.commit()
    return raw


def revoke_api_key(user_id: int) -> None:
    """Remove the API key for a user."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET api_key_hash = NULL, api_key = NULL WHERE id = ?", (user_id,))
        conn.commit()


def get_api_key(user_id: int) -> str | None:
    """Return the raw API key for a user, or None if not set."""
    with get_conn() as conn:
        row = conn.execute("SELECT api_key FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["api_key"] if row else None


def get_user_by_api_key(raw_key: str):
    """Look up a user by raw API key. Returns the user row or None."""
    h = _hash_key(raw_key)
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE api_key_hash = ?", (h,)
        ).fetchone()


def has_api_key(user_id: int) -> bool:
    """Return True if the user currently has an API key set."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT api_key_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["api_key_hash"])

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
