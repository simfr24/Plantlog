from werkzeug.security import generate_password_hash, check_password_hash 
from py.db import get_conn
from flask import session, g

def create_user(username: str, password: str, lang: str = "en"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (username, pw_hash, lang) VALUES (?,?,?)",
            (username.lower(), generate_password_hash(password), lang)
        )
        conn.commit()

def get_user_by_username(username: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.lower(),)
        ).fetchone()

def get_user_by_id(uid: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

def update_user_lang(uid: int, lang: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET lang=? WHERE id=?", (lang, uid))
        conn.commit()

def login_user(user_row):
    session["uid"] = user_row["id"]

def logout_user():
    session.pop("uid", None)
