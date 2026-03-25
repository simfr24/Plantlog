"""py/mcp.py — MCP server as a Flask Blueprint (SSE transport).

Implements the MCP JSON-RPC protocol directly over Flask SSE — no separate
process, no ASGI bridge, no HTTP round-trips. Tools call helpers directly.

Claude Desktop config  (~/.config/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "plantlog": {
          "url": "http://<host>:5000/mcp/sse?api_key=<your-key>"
        }
      }
    }
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from datetime import date

from flask import Blueprint, Response, request, stream_with_context

from py.db import get_conn
from py.helpers import (
    load_data,
    load_one,
    save_new_plant,
    update_plant as _update_plant,
    validate_form,
    get_empty_form,
    get_event_specs,
    get_translations,
    process_delete_plant,
    explode_plant as _explode_plant,
)
from py.users import get_user_by_api_key

blueprint = Blueprint("mcp", __name__, url_prefix="/mcp")

# ── session registry ──────────────────────────────────────────────────────────

_sessions: dict[str, queue.Queue] = {}
_lock = threading.Lock()

# ── auth ──────────────────────────────────────────────────────────────────────

def _auth():
    key = (request.headers.get("X-API-Key")
           or request.headers.get("Authorization", "").removeprefix("Bearer ")
           or request.args.get("api_key", ""))
    return get_user_by_api_key(key) if key else None

# ── tool schemas ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "list_plants",
        "description": "Return all your plants with their current state.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
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
        "description": "Add a new plant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "common":          {"type": "string", "description": "Common name."},
                "latin":           {"type": "string", "description": "Latin name."},
                "first_event":     {"type": "string", "default": "sow",
                                    "description": "First event: sow, plant, soak, strat, sprout, flower, fruit, water, fertilize, measure, custom."},
                "event_date":      {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today."},
                "location":        {"type": "string"},
                "notes":           {"type": "string"},
                "variety":         {"type": "string"},
                "nickname":        {"type": "string"},
                "count":           {"type": "integer", "default": 1},
                "sprout_min_days": {"type": "integer", "description": "Min days to germination (required for sow — look up the species, do not guess)."},
                "sprout_max_days": {"type": "integer", "description": "Max days to germination (required for sow — look up the species, do not guess)."},
            },
            "required": ["common", "latin"],
        },
    },
    {
        "name": "log_event",
        "description": "Log a new event for a plant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plant_id":        {"type": "integer"},
                "event_type":      {"type": "string",
                                    "description": "sow, plant, soak, strat, sprout, flower, fruit, water, fertilize, measure, custom, dead."},
                "event_date":      {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today."},
                "sprout_min_days": {"type": "integer", "description": "Min germination days (sow only — look up the species, do not guess)."},
                "sprout_max_days": {"type": "integer", "description": "Max germination days (sow only — look up the species, do not guess)."},
                "duration_val":    {"type": "integer", "description": "Duration amount (soak/strat)."},
                "duration_unit":   {"type": "string",  "description": "hours, days, weeks, months."},
                "size_val":        {"type": "number",  "description": "Measurement value (measure events)."},
                "size_unit":       {"type": "string",  "description": "mm, cm, m."},
                "custom_label":    {"type": "string",  "description": "Label (custom events)."},
                "custom_note":     {"type": "string",  "description": "Note (custom events)."},
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
                "notes":    {"type": "string"},
                "variety":  {"type": "string"},
                "nickname": {"type": "string"},
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
                "style":    {"type": "string", "enum": ["classic", "circular"], "default": "classic"},
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
        "location": p.get("location"),
        "notes":    p.get("notes"),
        "count":    p.get("count", 1),
        "state":    state.get("label"),
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
    return ev

# ── tool implementations ──────────────────────────────────────────────────────

def _call_tool(name: str, args: dict, user: dict) -> str:
    uid = user["id"]
    lang = user.get("lang", "en")

    if name == "list_plants":
        plants = load_data(uid)
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
        })
        errors, event = validate_form(form, get_translations(lang), context="add_stage")
        if errors:
            raise ValueError(str(errors))
        plant_data = {k: p.get(k) or "" for k in ("common", "latin", "location", "notes", "variety")}
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

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Plantlog", "version": "1.0"},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications need no response

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": TOOLS})

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

# ── Flask routes ──────────────────────────────────────────────────────────────

@blueprint.route("/sse")
def sse():
    user = _auth()
    if user is None:
        return Response("Unauthorized", status=401)

    sid = uuid.uuid4().hex
    q: queue.Queue = queue.Queue()
    with _lock:
        _sessions[sid] = q

    def generate():
        yield f"event: endpoint\ndata: /mcp/messages?session_id={sid}\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if msg is None:
                    break
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            with _lock:
                _sessions.pop(sid, None)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@blueprint.route("/messages", methods=["POST"])
def messages():
    user = _auth()
    if user is None:
        return Response("Unauthorized", status=401)

    sid = request.args.get("session_id", "")
    with _lock:
        q = _sessions.get(sid)
    if q is None:
        return Response("Session not found", status=404)

    msg = request.get_json(silent=True)
    if not msg:
        return Response("Bad request", status=400)

    response = _handle_rpc(msg, user)
    if response is not None:
        q.put(response)

    return Response(status=202)
