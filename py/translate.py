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

import functools
import hashlib
import html
import json
import os
from types import SimpleNamespace

import flask

from py.db import get_conn
from py.helpers import AVAILABLE_LANGS

# Languages the UI (and therefore translation targets) supports. Derived from
# the single language list so adding a language needs no change here.
SUPPORTED_LANGS = set(AVAILABLE_LANGS)

# Don't bother translating trivially short strings (numbers, single tokens).
_MIN_LEN = 2

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)


@functools.lru_cache(maxsize=1)
def _api_key():
    """Google Cloud Translation API key from config.json, or None."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return (json.load(f).get("google_translation") or {}).get("key") or None
    except Exception:
        return None


def _machine_translate(text, source_lang, target_lang, fmt="text"):
    """Translate via the official Cloud Translation API when a key is set,
    otherwise via the keyless deep-translator endpoint. Returns None on failure.

    ``fmt`` is "text" or "html". In "html" mode the API preserves tags and only
    translates text nodes, which keeps rendered-Markdown structure (tables,
    lists) intact — translating raw Markdown garbles it. HTML mode requires the
    official API; there is no keyless fallback for it.

    The official API uses Google's full, current model — it knows niche
    botanical terms (e.g. "Asiminier" -> "Pawpaw") that the free endpoint, which
    runs a smaller/older model, leaves untranslated.
    """
    key = _api_key()
    if key:
        try:
            import requests

            params = {"q": text, "target": target_lang, "format": fmt, "key": key}
            if source_lang:
                params["source"] = source_lang
            resp = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                data=params,
                timeout=10,
            )
            resp.raise_for_status()
            translated = resp.json()["data"]["translations"][0]["translatedText"]
            # In HTML mode the entities are meaningful markup; leave them be.
            return translated if fmt == "html" else html.unescape(translated)
        except Exception:
            pass  # fall through to the keyless endpoint

    if fmt == "html":
        return None  # keyless endpoint can't safely translate HTML

    try:
        from deep_translator import GoogleTranslator

        return GoogleTranslator(source=source_lang or "auto", target=target_lang).translate(text)
    except Exception:
        return None


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


def _cache_put(text: str, target_lang: str, translated: str, source_lang=None) -> None:
    h = _hash(text, target_lang)
    with get_conn() as conn:
        # Never clobber an admin-corrected entry with a fresh machine result.
        existing = conn.execute(
            "SELECT edited FROM translations_cache "
            "WHERE source_hash = ? AND target_lang = ?",
            (h, target_lang),
        ).fetchone()
        if existing and existing["edited"]:
            return
        conn.execute(
            "INSERT OR REPLACE INTO translations_cache "
            "(source_hash, target_lang, source_lang, source_text, translated, edited) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (h, target_lang, source_lang, text, translated),
        )
        conn.commit()


def translate_content(text, target_lang: str, source_lang=None) -> str:
    """Return ``text`` translated into ``target_lang`` (cached).

    ``source_lang`` is the language the content is assumed to be in (the
    content owner's account language). When it matches ``target_lang`` we never
    touch the translator and return the pristine original — translating text
    into the language it is already in only risks garbling it.

    Returns the original text unchanged when translation is unnecessary or
    unavailable. Never raises.
    """
    if not text or target_lang not in SUPPORTED_LANGS or len(text.strip()) < _MIN_LEN:
        return text

    # Already in the desired language: show the original verbatim.
    if source_lang and source_lang == target_lang:
        return text

    cached = _cache_get(text, target_lang)
    if cached is not None:
        return cached

    # A standalone capitalized word can be misparsed as a proper noun (e.g.
    # "Avocatier" -> "Lawyer" instead of "Avocado"). Translating it lowercased
    # avoids that; we restore the leading capital on the result.
    leading_upper = text[:1].isupper()
    to_send = (text[:1].lower() + text[1:]) if leading_upper else text

    result = _machine_translate(to_send, source_lang, target_lang)
    if not result:
        return text  # graceful degradation: show the original

    if leading_upper and result[:1].islower():
        result = result[:1].upper() + result[1:]

    _cache_put(text, target_lang, result, source_lang=source_lang)
    return result


def translate_html(html_text, target_lang: str, source_lang=None):
    """Translate already-rendered HTML, preserving its markup (cached).

    Returns the translated HTML, or None when translation is unnecessary or
    unavailable (caller should then show the original rendered HTML).
    """
    if not html_text or target_lang not in SUPPORTED_LANGS:
        return None
    if source_lang and source_lang == target_lang:
        return None

    cached = _cache_get(html_text, target_lang)
    if cached is not None:
        return cached

    result = _machine_translate(html_text, source_lang, target_lang, fmt="html")
    if not result:
        return None

    _cache_put(html_text, target_lang, result, source_lang=source_lang)
    return result


# ---------------------------------------------------------------------------
# Admin: browse / correct cached content translations
# ---------------------------------------------------------------------------

def list_cached_translations(query=None, target_lang=None, limit=500):
    """Return cached content translations for the admin editor, newest first.

    Filters: ``query`` (substring match on source or translated text) and
    ``target_lang``. Rows missing a stored source text (cached before that
    column existed) can't be meaningfully edited and are skipped."""
    sql = [
        "SELECT source_hash, target_lang, source_lang, source_text, translated, "
        "edited, created_at FROM translations_cache WHERE source_text IS NOT NULL"
    ]
    params = []
    if target_lang:
        sql.append("AND target_lang = ?")
        params.append(target_lang)
    if query:
        sql.append("AND (source_text LIKE ? OR translated LIKE ?)")
        like = f"%{query}%"
        params += [like, like]
    sql.append("ORDER BY edited DESC, created_at DESC LIMIT ?")
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()
    return [dict(r) for r in rows]


def backfill_source_text(candidates) -> int:
    """Fill in ``source_text`` for cache rows created before that column existed.

    The cache is keyed by a one-way hash of the source text, so the original
    can't be recovered from the row itself. Instead we take ``candidates`` (every
    string the app might have translated — plant names, notes, rendered note
    HTML, event notes…), hash each against every target language, and stamp the
    source text onto any matching row that is still missing it. Returns the number
    of rows updated."""
    updated = 0
    with get_conn() as conn:
        for text in candidates:
            if not text:
                continue
            for lang in SUPPORTED_LANGS:
                cur = conn.execute(
                    "UPDATE translations_cache SET source_text = ? "
                    "WHERE source_hash = ? AND target_lang = ? AND source_text IS NULL",
                    (text, _hash(text, lang), lang),
                )
                updated += cur.rowcount
        conn.commit()
    return updated


def update_cached_translation(source_hash: str, target_lang: str, translated: str) -> bool:
    """Overwrite a cached translation with an admin-supplied value and mark it
    edited so future machine runs won't clobber it. Returns True if a row matched."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE translations_cache SET translated = ?, edited = 1 "
            "WHERE source_hash = ? AND target_lang = ?",
            (translated, source_hash, target_lang),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_cached_translation(source_hash: str, target_lang: str) -> bool:
    """Drop a cached entry so it is re-translated from scratch next time."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM translations_cache WHERE source_hash = ? AND target_lang = ?",
            (source_hash, target_lang),
        )
        conn.commit()
        return cur.rowcount > 0


def tr(text):
    """Template helper: translate ``text`` into the current UI language.

    Returns a namespace with ``.text`` (possibly translated) and
    ``.translated`` (whether it actually differs from the original), so
    templates can show a "translated" indicator.
    """
    target = getattr(flask.g, "lang", None)
    source = getattr(flask.g, "content_lang", None)
    if not text or not target:
        return SimpleNamespace(text=text, translated=False, source=source)
    out = translate_content(text, target, source)
    return SimpleNamespace(text=out, translated=(out.strip() != text.strip()), source=source)
