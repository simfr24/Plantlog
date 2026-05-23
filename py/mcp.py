"""py/mcp.py — MCP server as a Flask Blueprint (Streamable HTTP transport).

Single POST endpoint — no persistent connections, WSGI-friendly.
Works on PythonAnywhere and any standard WSGI host.

Claude Desktop config  (~/.config/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "plantlog": {
          "type": "streamable-http",
          "url": "https://<host>/mcp?api_key=<your-key>"
        }
      }
    }
"""

from __future__ import annotations

import json
from datetime import date

from flask import Blueprint, Response, request

from py.db import get_conn
from py.helpers import (
    load_data,
    load_one,
    save_new_plant,
    update_plant as _update_plant,
    update_action as _update_action,
    get_action_by_id,
    process_delete_action as _delete_event,
    validate_form,
    get_empty_form,
    get_event_specs,
    get_translations,
    process_delete_plant,
    explode_plant as _explode_plant,
)
from py.users import get_user_by_api_key

blueprint = Blueprint("mcp", __name__, url_prefix="/mcp")

# ── auth ──────────────────────────────────────────────────────────────────────

def _auth():
    key = (request.headers.get("X-API-Key")
           or request.headers.get("Authorization", "").removeprefix("Bearer ")
           or request.args.get("api_key", ""))
    row = get_user_by_api_key(key) if key else None
    return dict(row) if row else None

# ── language & notes formatting guide ─────────────────────────────────────────

LANG_NAMES = {"en": "English", "fr": "French", "ru": "Russian"}


def _language_directive(lang_name: str) -> str:
    return (
        f"LANGUAGE: This user's preferred language is {lang_name}. Write all "
        f"user-facing text fields (notes, custom_label, custom_note, nickname "
        f"when descriptive, etc.) in {lang_name}, even if the current "
        f"conversation is in another language. Only switch languages if the "
        f"user explicitly asks for a different one in this particular request."
    )


def _notes_format_guide(lang_name: str) -> str:
    return (
        f"Free-form Markdown notes about the plant. Write them in {lang_name} "
        f"(the user's preferred language) unless the user explicitly asks "
        f"otherwise in this request. Unless the user specifies a different "
        f"structure, follow this house style so all plants look consistent:\n"
        "\n"
        "# Common name (*Latin name*)\n"
        "\n"
        "One short paragraph describing the species itself: origin, what "
        "makes it interesting, any context the user gave that doesn't fit "
        "another field.\n"
        "\n"
        "## Culture\n"
        "\n"
        "A Markdown table with two columns (Paramètre | Valeur) covering "
        "the rows that apply: Exposition, Température été, Température "
        "hiver, Humidité, Arrosage, Rempotage, Substrat, Engrais. Omit "
        "rows you don't have reliable info for; don't invent values.\n"
        "\n"
        "## Notes\n"
        "\n"
        "A short bulleted list of species-specific tips, quirks, common "
        "mistakes, and key triggers (e.g. flowering conditions). Use "
        "**bold** for the most important warnings or triggers.\n"
        "\n"
        "DO NOT restate data that already lives in dedicated fields: "
        "vendor / source, price, acquisition date, order date, location, "
        "rusticity, variety, count, event history. Those are recorded on "
        "the plant or its events and would just clutter the notes. The "
        "ONLY exception is when there's a meaningful extra detail the "
        "field can't hold (e.g. 'bought as part of a Scandinavian "
        "rare-seed collection, see the vendor's monograph' adds context "
        "that 'Impecta' alone doesn't).\n"
        "\n"
        "Keep it concise: no fluff, no generic plant-care boilerplate.\n"
        "\n"
        "Avoid telltale AI-writing tics. Do not use em dashes (—) or en "
        "dashes (–) for parenthetical asides; use commas, parentheses, "
        "or periods instead. Skip 'It's worth noting that...', "
        "'Importantly,', 'Overall,', 'In essence,', 'plays a key role "
        "in', and similar filler. Skip rhetorical 'not just X, but Y' "
        "constructions. Do not open sentences with vague adverbs like "
        "'Notably,' or 'Interestingly,'. Write the way a knowledgeable "
        "gardener jotting field notes would: direct, specific, no "
        "throat-clearing."
    )

# ── tool schemas ──────────────────────────────────────────────────────────────


def _build_tools(lang_name: str) -> list:
    NOTES_FORMAT_GUIDE = _notes_format_guide(lang_name)
    LANG_HINT = _language_directive(lang_name)
    return _TOOLS_TEMPLATE(NOTES_FORMAT_GUIDE, LANG_HINT)


def _TOOLS_TEMPLATE(NOTES_FORMAT_GUIDE, LANG_HINT):
    return [
    {
        "name": "list_plants",
        "description": "Return your plants. Defaults to all active plants (living, stashed, ordered) — excludes dead plants. Pass include_dead=true to also include dead plants.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_dead": {"type": "boolean", "description": "Also include dead plants (graveyard)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_plant",
        "description": "Return full details and event history for one plant.",
        "inputSchema": {
            "type": "object",
            "properties": {"plant_id": {"type": "integer", "description": "Numeric plant ID."}},
            "required": ["plant_id"],
        },
    },
    {
        "name": "add_plant",
        "description": (
            LANG_HINT + "\n\n"
            "Add a new plant. Choosing the starting state matters; the rules below also "
            "preserve the correct chronological order in the timeline (events are sorted "
            "by date, then by insertion order on ties).\n"
            "\n"
            "Seeds:\n"
            "  • Being sown immediately → first_event='sow'.\n"
            "  • Stashed for later → first_event='acquire' (carry price/source here).\n"
            "\n"
            "Live plants (potted, bare-root, cuttings): ALWAYS ask the user whether the "
            "plant is being potted/installed now or kept aside in the stash, unless they "
            "already said so. Most live-plant purchases are installed directly, but not all. "
            "Then:\n"
            "  • Installed now → first_event='acquire' (with price, source, "
            "acquire_type='bought'). Then IMMEDIATELY follow up with a separate "
            "log_event call for event_type='plant' on the same date. This produces the "
            "natural order in the timeline: 'Acheté chez …' then 'Plantation' on the "
            "same day. NEVER use first_event='plant' just to skip the acquire step — "
            "the price has nowhere to live and the purchase context is lost.\n"
            "  • Kept in stash → first_event='acquire' (with price/source). No "
            "follow-up needed.\n"
            "  • Pre-ordered, not yet received → first_event='order' (with price, "
            "source, expected_date). When it later arrives, the user marks it received "
            "from the UI (or a separate log_event with acquire_type='received').\n"
            "\n"
            "Rule of thumb: the purchase event (acquire or order) ALWAYS comes first "
            "in the timeline. Other events (plant, sow, etc.) follow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "common":          {"type": "string", "description": "Common name."},
                "latin":           {"type": "string", "description": "Latin name."},
                "first_event":     {"type": "string", "default": "sow",
                                    "description": "First event. Pick ONE and put the purchase price on THAT one event — never log both an order and an acquire for the same purchase just to split the price. Rules:\n  • If the plant was ordered and has not arrived yet → 'order' (state: Ordered). Price + source go here.\n  • If the plant was ordered AND already received and the user wants both events recorded → still use 'order' here with the order date + price + source, then log a separate 'acquire' event (no price, acquire_type='received') for the arrival date.\n  • If the plant is simply in-hand now (bought in a shop, gift, foraged) with no prior order step → 'acquire'. Price + source go here.\n  • Seeds being sown immediately → 'sow'. Live plants being potted up immediately → 'plant'."},
                "event_date":      {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today."},
                "location":        {"type": "string"},
                "notes":           {"type": "string", "description": NOTES_FORMAT_GUIDE},
                "variety":         {"type": "string"},
                "nickname":        {"type": "string"},
                "rusticity":       {"type": "string", "description": "Cold hardiness as the minimum tolerated temperature in °C (e.g. '−18 °C', '−5 °C', '+5 °C'). Populate this by default — look up the species. Do NOT use USDA zones. Do not invent values; if uncertain, leave empty and tell the user."},
                "count":           {"type": "integer", "default": 1},
                "sprout_min_days": {"type": "integer", "description": "Min days to germination (required for sow — look up the species, do not guess)."},
                "sprout_max_days": {"type": "integer", "description": "Max days to germination (required for sow — look up the species, do not guess)."},
                "source":          {"type": "string", "description": "Vendor / person / place. Recorded on the first_event when it is 'order' or 'acquire' (e.g. 'Impecta Fröhandel', 'gift from neighbour', 'roadside foraging')."},
                "acquire_type":    {"type": "string", "enum": ["bought", "gift", "foraged", "swap", "other"], "description": "How the plant was acquired. Only meaningful when first_event is 'acquire'."},
                "expected_date":   {"type": "string", "description": "ISO date YYYY-MM-DD. Expected arrival date. Only meaningful when first_event is 'order'."},
                "price":           {"type": "number", "description": "Purchase price. Set this on first_event='order' OR first_event='acquire' (with acquire_type='bought'). NEVER set both an order and an acquire price for the same purchase."},
                "price_currency":  {"type": "string", "description": "ISO 4217 currency code, e.g. 'EUR', 'USD'. Only saved when price is also set."},
            },
            "required": ["common", "latin"],
        },
    },
    {
        "name": "log_event",
        "description": (
            "Log a new event for a plant. Reminder: the purchase event "
            "(order or acquire) always comes first chronologically; sow/plant/sprout/etc. "
            "follow. When potting a freshly-acquired live plant on the same day, log the "
            "acquire first (via add_plant or log_event) and the plant event second; "
            "events with the same date sort by insertion order, so the order of your calls matters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plant_id":        {"type": "integer"},
                "event_type":      {"type": "string",
                                    "description": "Event to log. Key distinctions:\n  • 'order' = an order was placed (state → Ordered). Carries the purchase price + vendor.\n  • 'acquire' = the plant is physically in hand (state → Stashed). For a previously-ordered plant arriving, use acquire_type='received' and DO NOT repeat the price (it already lives on the order event).\n  • For a one-step purchase with no prior order, use 'acquire' alone and put the price here.\n  Other types: sow, plant, soak, strat, sprout, flower, fruit, water, fertilize, measure, custom, dead."},
                "event_date":      {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today."},
                "sprout_min_days": {"type": "integer", "description": "Min germination days (sow only — look up the species, do not guess)."},
                "sprout_max_days": {"type": "integer", "description": "Max germination days (sow only — look up the species, do not guess)."},
                "duration_val":    {"type": "integer", "description": "Duration amount (soak/strat)."},
                "duration_unit":   {"type": "string",  "description": "hours, days, weeks, months."},
                "size_val":        {"type": "number",  "description": "Measurement value (measure events)."},
                "size_unit":       {"type": "string",  "description": "mm, cm, m."},
                "custom_label":    {"type": "string",  "description": "Label (custom events)."},
                "custom_note":     {"type": "string",  "description": "Note (custom events)."},
                "source":          {"type": "string",  "description": "Vendor/person/place (order and acquire events). Always capture this when known."},
                "acquire_type":    {"type": "string",  "description": "How acquired: bought, gift, foraged, swap, other (acquire events only)."},
                "expected_date":   {"type": "string",  "description": "ISO date YYYY-MM-DD. Expected arrival date (order events only)."},
                "price":           {"type": "number",  "description": "Purchase price. Set this on the order event for ordered plants, or on the acquire event for in-shop / gift-with-known-value purchases. NEVER set it on both events for the same purchase."},
                "price_currency":  {"type": "string",  "description": "Currency code, e.g. EUR, USD. Only meaningful when price is also set."},
            },
            "required": ["plant_id", "event_type"],
        },
    },
    {
        "name": "update_plant",
        "description": "Update plant metadata. Only supplied fields are changed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "integer"},
                "common":   {"type": "string"},
                "latin":    {"type": "string"},
                "location": {"type": "string"},
                "notes":    {"type": "string", "description": NOTES_FORMAT_GUIDE},
                "variety":  {"type": "string"},
                "nickname": {"type": "string"},
                "rusticity": {"type": "string", "description": "Cold hardiness as the minimum tolerated temperature in °C (e.g. '−18 °C')."},
                "count":    {"type": "integer"},
            },
            "required": ["plant_id"],
        },
    },
    {
        "name": "delete_plant",
        "description": "Permanently delete a plant and all its events.",
        "inputSchema": {
            "type": "object",
            "properties": {"plant_id": {"type": "integer"}},
            "required": ["plant_id"],
        },
    },
    {
        "name": "explode_plant",
        "description": "Split a batch plant record into individual plant records sharing a batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "integer"},
                "count":    {"type": "integer", "description": "Total individuals to create (min 2)."},
            },
            "required": ["plant_id", "count"],
        },
    },
    {
        "name": "update_event",
        "description": "Patch fields on an existing event. Only supplied fields are changed; omitted fields keep their current value. Useful for retroactively adding source, price, or correcting a date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id":       {"type": "integer"},
                "date":           {"type": "string", "description": "ISO date (YYYY-MM-DD) to change the event date."},
                "source":         {"type": "string", "description": "Vendor / person / place (order and acquire events)."},
                "acquire_type":   {"type": "string", "enum": ["bought", "gift", "foraged", "swap", "received", "other"], "description": "Acquire sub-type (acquire events only)."},
                "expected_on":    {"type": "string", "description": "ISO date — expected arrival (order events only)."},
                "price":          {"type": "number", "description": "Price paid (order and acquire events)."},
                "price_currency": {"type": "string", "description": "ISO 4217 currency code, e.g. EUR, USD (only saved when price is also set)."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Permanently delete a single event from a plant's history.",
        "inputSchema": {
            "type": "object",
            "properties": {"event_id": {"type": "integer"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "list_event_types",
        "description": "Return all available event types and the state each one triggers.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "print_label",
        "description": "Queue a label print job for a plant on the server's Bluetooth printer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plant_id": {"type": "integer"},
                "style":    {"type": "string", "enum": ["classic", "circular", "minimal", "detailed_v", "detailed_h", "qr", "stake_wrap"], "default": "classic"},
            },
            "required": ["plant_id"],
        },
    },
    ]


# ── serialisation helpers ─────────────────────────────────────────────────────

def _plant_summary(p: dict) -> dict:
    state   = p.get("state") or {}
    current = p.get("current") or {}
    return {
        "id":       p["id"],
        "common":   p["common"],
        "latin":    p["latin"],
        "variety":  p.get("variety"),
        "nickname": p.get("nickname"),
        "rusticity": p.get("rusticity"),
        "location": p.get("location"),
        "notes":    p.get("notes"),
        "count":    p.get("count", 1),
        "state":    state.get("label"),
        "state_code": state.get("code"),
        "last_event": {
            "type": current.get("action"),
            "date": current.get("start"),
        } if current else None,
    }


def _event_detail(h: dict) -> dict:
    ev = {"id": h["id"], "type": h["action"], "date": h["start"]}
    if h["action"] == "sow" and "range" in h:
        ev["sprout_range"] = {
            "min": h["range"][0], "min_unit": h["range"][1],
            "max": h["range"][2], "max_unit": h["range"][3],
        }
    elif h["action"] in ("soak", "strat") and "duration" in h:
        ev["duration"] = {"val": h["duration"][0], "unit": h["duration"][1]}
    elif h["action"] == "measure" and "size" in h:
        ev["size"] = {"val": h["size"][0], "unit": h["size"][1]}
    elif h["action"] == "custom":
        ev["label"] = h.get("custom_label")
        ev["note"]  = h.get("custom_note")
    elif h["action"] == "acquire":
        ev["source"] = h.get("source")
        ev["acquire_type"] = h.get("acquire_type")
        if h.get("price") is not None:
            ev["price"] = h["price"]
            ev["price_currency"] = h.get("price_currency")
    elif h["action"] == "order":
        ev["source"] = h.get("source")
        ev["expected_on"] = h.get("expected_on")
        if h.get("price") is not None:
            ev["price"] = h["price"]
            ev["price_currency"] = h.get("price_currency")
    return ev

# ── tool implementations ──────────────────────────────────────────────────────

def _call_tool(name: str, args: dict, user: dict) -> str:
    uid = user["id"]
    lang = user.get("lang", "en")

    if name == "list_plants":
        plants = load_data(uid)
        include_dead = args.get("include_dead", False)
        if not include_dead:
            plants = [p for p in plants if (p.get("state") or {}).get("code") != "dead"]
        return json.dumps([_plant_summary(p) for p in plants], indent=2)

    if name == "get_plant":
        p = load_one(args["plant_id"])
        if p is None or p["user_id"] != uid:
            raise ValueError("Plant not found.")
        result = _plant_summary(p)
        result["history"] = [_event_detail(h) for h in p.get("history", [])]
        return json.dumps(result, indent=2)

    if name == "add_plant":
        form = get_empty_form()
        form.update({
            "common":            args.get("common", ""),
            "latin":             args.get("latin", ""),
            "variety":           args.get("variety", ""),
            "nickname":          args.get("nickname", ""),
            "rusticity":         args.get("rusticity", ""),
            "count":             max(1, int(args.get("count", 1))),
            "location":          args.get("location", ""),
            "notes":             args.get("notes", ""),
            "status":            args.get("first_event", "sow"),
            "event_date":        args.get("event_date") or date.today().isoformat(),
            "event_range_min":   args.get("sprout_min_days", 14),
            "event_range_min_u": "days",
            "event_range_max":   args.get("sprout_max_days", 30),
            "event_range_max_u": "days",
            "event_dur_val":     args.get("duration_val", 24),
            "event_dur_unit":    args.get("duration_unit", "hours"),
            "event_source":              args.get("source", ""),
            "event_acquire_type":         args.get("acquire_type", "bought"),
            "event_ended_on":             args.get("expected_date", ""),
            "event_price":                str(args["price"]) if args.get("price") is not None else "",
            "event_price_currency":       args.get("price_currency", ""),
            "event_order_price":          str(args["price"]) if args.get("price") is not None else "",
            "event_order_price_currency": args.get("price_currency", ""),
        })
        errors, event = validate_form(form, get_translations(lang), context="add")
        if errors:
            raise ValueError(str(errors))
        plant_id = save_new_plant(form, event, uid)
        return json.dumps({"ok": True, "id": plant_id, "message": f"Plant '{form['common']}' added."})

    if name == "log_event":
        plant_id = args["plant_id"]
        p = load_one(plant_id)
        if p is None or p["user_id"] != uid:
            raise ValueError("Plant not found.")
        form = get_empty_form()
        form.update({
            "status":             args.get("event_type", "water"),
            "event_date":         args.get("event_date") or date.today().isoformat(),
            "event_range_min":    args.get("sprout_min_days", 14),
            "event_range_min_u":  "days",
            "event_range_max":    args.get("sprout_max_days", 30),
            "event_range_max_u":  "days",
            "event_dur_val":      args.get("duration_val", 24),
            "event_dur_unit":     args.get("duration_unit", "hours"),
            "event_size_val":     args.get("size_val", 0),
            "event_size_unit":    args.get("size_unit", "cm"),
            "event_custom_label": args.get("custom_label", ""),
            "event_custom_note":  args.get("custom_note", ""),
            "event_source":              args.get("source", ""),
            "event_acquire_type":         args.get("acquire_type", "bought"),
            "event_ended_on":             args.get("expected_date", ""),
            "event_price":                str(args["price"]) if args.get("price") is not None else "",
            "event_price_currency":       args.get("price_currency", ""),
            "event_order_price":          str(args["price"]) if args.get("price") is not None else "",
            "event_order_price_currency": args.get("price_currency", ""),
        })
        errors, event = validate_form(form, get_translations(lang), context="add_stage")
        if errors:
            raise ValueError(str(errors))
        plant_data = {k: p.get(k) or "" for k in ("common", "latin", "location", "notes", "variety", "nickname", "rusticity")}
        plant_data["count"] = p.get("count", 1) or 1
        _update_plant(plant_id, plant_data, new_event=event)
        return json.dumps({"ok": True})

    if name == "update_plant":
        plant_id = args["plant_id"]
        p = load_one(plant_id)
        if p is None or p["user_id"] != uid:
            raise ValueError("Plant not found.")
        plant_data = {
            "common":   args.get("common",   p["common"]),
            "latin":    args.get("latin",    p["latin"]),
            "location": args.get("location", p.get("location") or ""),
            "notes":    args.get("notes",    p.get("notes") or ""),
            "variety":  args.get("variety",  p.get("variety") or ""),
            "nickname": args.get("nickname", p.get("nickname") or ""),
            "rusticity": args.get("rusticity", p.get("rusticity") or ""),
            "count":    args.get("count",    p.get("count", 1)),
        }
        _update_plant(plant_id, plant_data)
        return json.dumps({"ok": True})

    if name == "delete_plant":
        plant_id = args["plant_id"]
        p = load_one(plant_id)
        if p is None or p["user_id"] != uid:
            raise ValueError("Plant not found.")
        process_delete_plant(plant_id, uid)
        return json.dumps({"ok": True})

    if name == "explode_plant":
        ids = _explode_plant(args["plant_id"], max(2, int(args.get("count", 2))), uid)
        if not ids:
            raise ValueError("Plant not found or not authorized.")
        return json.dumps({"ok": True, "plant_ids": ids})

    if name == "update_event":
        ev = get_action_by_id(args["event_id"])
        if ev is None:
            raise ValueError("Event not found.")
        p = load_one(ev["plant_id"])
        if p is None or p["user_id"] != uid:
            raise ValueError("Event not found.")
        # Merge supplied fields over current values
        if "date" in args:
            ev["start"] = args["date"]
        if "source" in args:
            ev["source"] = args["source"]
        if "acquire_type" in args:
            ev["acquire_type"] = args["acquire_type"]
        if "expected_on" in args:
            ev["expected_on"] = args["expected_on"]
        if "price" in args:
            ev["price"] = args["price"]
            ev["price_currency"] = args.get("price_currency", ev.get("price_currency"))
        _update_action(args["event_id"], ev)
        return json.dumps({"ok": True})

    if name == "delete_event":
        ev = get_action_by_id(args["event_id"])
        if ev is None:
            raise ValueError("Event not found.")
        p = load_one(ev["plant_id"])
        if p is None or p["user_id"] != uid:
            raise ValueError("Event not found.")
        _delete_event(args["event_id"], uid)
        return json.dumps({"ok": True})

    if name == "list_event_types":
        specs = get_event_specs()
        return json.dumps([{"code": s["code"], "label": s["label"]} for s in specs])

    if name == "print_label":
        plant_id = args["plant_id"]
        p = load_one(plant_id)
        if p is None or p["user_id"] != uid:
            raise ValueError("Plant not found.")
        style = args.get("style", "classic")
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO print_jobs (user_id, plant_id, style) VALUES (?,?,?)",
                (uid, plant_id, style),
            )
            job_id = cur.lastrowid
            conn.commit()
        return json.dumps({"ok": True, "job_id": job_id})

    raise ValueError(f"Unknown tool: {name}")

# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────

def _handle_rpc(msg: dict, user: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    lang_name = LANG_NAMES.get(user.get("lang") or "en", "English")

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Plantlog", "version": "1.0"},
            "instructions": _language_directive(lang_name),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications need no response

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": _build_tools(lang_name)})

    if method == "tools/call":
        params = msg.get("params", {})
        try:
            text = _call_tool(params.get("name", ""), params.get("arguments", {}), user)
            return ok({"content": [{"type": "text", "text": text}]})
        except ValueError as e:
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        except Exception as e:
            return err(-32603, str(e))

    if msg_id is not None:
        return err(-32601, f"Method not found: {method}")
    return None

# ── Flask route ───────────────────────────────────────────────────────────────

@blueprint.route("", methods=["POST"])
def handle():
    user = _auth()
    if user is None:
        return Response("Unauthorized", status=401)

    msg = request.get_json(silent=True)
    if not msg:
        return Response("Bad request", status=400)

    # Support JSON-RPC batch requests
    if isinstance(msg, list):
        responses = [r for r in (_handle_rpc(m, user) for m in msg) if r is not None]
        return Response(json.dumps(responses), status=200, content_type="application/json")

    response = _handle_rpc(msg, user)
    if response is None:
        return Response(status=202)

    return Response(json.dumps(response), status=200, content_type="application/json")
