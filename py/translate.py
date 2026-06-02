"""Lazy, cached machine translation of user-generated free text.

Free-text fields (plant notes, event custom notes) are stored in whatever
language the user typed them. When a viewer's UI language differs, we translate
on demand and cache the result keyed by a hash of the source text + target
language, so each string is translated at most once per language.

Translation is keyless via deep-translator's GoogleTranslator. Any failure
(missing dependency, network error, unsupported language) degrades gracefully to
the original text and never blocks rendering.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import flask

from py.db import get_conn

# Languages the UI (and therefore translation targets) supports.
SUPPORTED_LANGS = {"en", "fr", "ru"}

# Don't bother translating trivially short strings (numbers, single tokens).
_MIN_LEN = 2


def _hash(text: str, target_lang: str) -> str:
    return hashlib.sha256(f"{target_lang}:{text}".encode("utf-8")).hexdigest()


def _cache_get(text: str, target_lang: str) -> str | None:
    h = _hash(text, target_lang)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT translated FROM translations_cache "
            "WHERE source_hash = ? AND target_lang = ?",
            (h, target_lang),
        ).fetchone()
    return row["translated"] if row else None


def _cache_put(text: str, target_lang: str, translated: str) -> None:
    h = _hash(text, target_lang)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO translations_cache "
            "(source_hash, target_lang, translated) VALUES (?, ?, ?)",
            (h, target_lang, translated),
        )
        conn.commit()


def translate_content(text, target_lang: str) -> str:
    """Return ``text`` translated into ``target_lang`` (cached).

    Returns the original text unchanged when translation is unnecessary or
    unavailable. Never raises.
    """
    if not text or target_lang not in SUPPORTED_LANGS or len(text.strip()) < _MIN_LEN:
        return text

    cached = _cache_get(text, target_lang)
    if cached is not None:
        return cached

    try:
        from deep_translator import GoogleTranslator

        result = GoogleTranslator(source="auto", target=target_lang).translate(text)
    except Exception:
        return text  # graceful degradation: show the original

    if not result:
        return text

    _cache_put(text, target_lang, result)
    return result


def tr(text):
    """Template helper: translate ``text`` into the current UI language.

    Returns a namespace with ``.text`` (possibly translated) and
    ``.translated`` (whether it actually differs from the original), so
    templates can show a "translated" indicator.
    """
    target = getattr(flask.g, "lang", None)
    if not text or not target:
        return SimpleNamespace(text=text, translated=False)
    out = translate_content(text, target)
    return SimpleNamespace(text=out, translated=(out.strip() != text.strip()))
