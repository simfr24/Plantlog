#!/usr/bin/env python3
"""
Plantlog MCP Server — thin HTTP client edition.

Runs on the user's machine. Calls the Plantlog Flask API over HTTP using
a per-user API key stored in plants.db.

Setup:
    pip install mcp requests

Environment variables:
    PLANTLOG_URL      Base URL of the Plantlog app  (e.g. http://192.168.1.10:5000)
    PLANTLOG_API_KEY  Your personal API key (generate it in Settings → API Key)

Claude Desktop config  (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "plantlog": {
          "command": "python3",
          "args": ["/path/to/Plantlog/mcp_server.py"],
          "env": {
            "PLANTLOG_URL":     "http://your-server:5000",
            "PLANTLOG_API_KEY": "your-key-here"
          }
        }
      }
    }
"""

import os
import sys

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: 'mcp' not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)


# ── configuration ────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("PLANTLOG_URL", "http://localhost:5000").rstrip("/")
API_KEY  = os.environ.get("PLANTLOG_API_KEY", "")

if not API_KEY:
    print("ERROR: PLANTLOG_API_KEY env var not set.", file=sys.stderr)
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {API_KEY}"}

mcp = FastMCP("Plantlog")


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get(path: str) -> dict | list:
    r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict | None = None) -> dict:
    r = requests.post(f"{BASE_URL}{path}", json=body or {}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: dict) -> dict:
    r = requests.patch(f"{BASE_URL}{path}", json=body, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> dict:
    r = requests.delete(f"{BASE_URL}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


# ── MCP tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def list_plants() -> list:
    """Return all your plants with their current state."""
    return _get("/api/plants")


@mcp.tool()
def get_plant(plant_id: int) -> dict:
    """
    Return full details and event history for one plant.

    Args:
        plant_id: Numeric plant ID.
    """
    return _get(f"/api/plants/{plant_id}")


@mcp.tool()
def add_plant(
    common: str,
    latin: str,
    first_event: str = "sow",
    event_date: str = "",
    location: str = "",
    notes: str = "",
    variety: str = "",
    sprout_min_days: int = 14,
    sprout_max_days: int = 30,
) -> dict:
    """
    Add a new plant.

    Args:
        common:          Common name (e.g. "Tomato").
        latin:           Latin name (e.g. "Solanum lycopersicum").
        first_event:     First event type: sow, plant, soak, strat, sprout,
                         flower, fruit, water, fertilize, measure, custom.
                         Defaults to "sow".
        event_date:      ISO date YYYY-MM-DD. Defaults to today.
        location:        Where the plant lives (e.g. "Greenhouse").
        notes:           Free-text notes.
        variety:         Optional variety or batch label.
        sprout_min_days: Min days to germination (for sow events).
        sprout_max_days: Max days to germination (for sow events).
    """
    return _post("/api/plants", {
        "common":          common,
        "latin":           latin,
        "variety":         variety,
        "location":        location,
        "notes":           notes,
        "first_event":     first_event,
        "event_date":      event_date,
        "sprout_min_days": sprout_min_days,
        "sprout_max_days": sprout_max_days,
    })


@mcp.tool()
def log_event(
    plant_id: int,
    event_type: str,
    event_date: str = "",
    sprout_min_days: int = 14,
    sprout_max_days: int = 30,
    duration_val: int = 24,
    duration_unit: str = "hours",
    size_val: float = 0,
    size_unit: str = "cm",
    custom_label: str = "",
    custom_note: str = "",
) -> dict:
    """
    Log a new event for a plant.

    Args:
        plant_id:        ID of the plant.
        event_type:      Event type: sow, plant, soak, strat, sprout, flower,
                         fruit, water, fertilize, measure, custom, dead.
        event_date:      ISO date YYYY-MM-DD. Defaults to today.
        sprout_min_days: Min germination days (for sow events).
        sprout_max_days: Max germination days (for sow events).
        duration_val:    Duration amount (for soak/strat events).
        duration_unit:   Duration unit: hours, days, weeks, months.
        size_val:        Measurement value (for measure events).
        size_unit:       Measurement unit (cm, mm, m…).
        custom_label:    Label text (for custom events).
        custom_note:     Additional note (for custom events).
    """
    return _post(f"/api/plants/{plant_id}/events", {
        "event_type":      event_type,
        "event_date":      event_date,
        "sprout_min_days": sprout_min_days,
        "sprout_max_days": sprout_max_days,
        "duration_val":    duration_val,
        "duration_unit":   duration_unit,
        "size_val":        size_val,
        "size_unit":       size_unit,
        "custom_label":    custom_label,
        "custom_note":     custom_note,
    })


@mcp.tool()
def update_plant(
    plant_id: int,
    common: str = "",
    latin: str = "",
    location: str = "",
    notes: str = "",
    variety: str = "",
) -> dict:
    """
    Update plant metadata. Only non-empty fields are changed.

    Args:
        plant_id:  ID of the plant.
        common:    New common name.
        latin:     New latin name.
        location:  New location.
        notes:     New notes.
        variety:   New variety/batch label.
    """
    payload = {k: v for k, v in {
        "common": common, "latin": latin, "location": location,
        "notes": notes, "variety": variety,
    }.items() if v}
    return _patch(f"/api/plants/{plant_id}", payload)


@mcp.tool()
def delete_plant(plant_id: int) -> dict:
    """
    Permanently delete a plant and all its events.

    Args:
        plant_id: ID of the plant to delete.
    """
    return _delete(f"/api/plants/{plant_id}")


@mcp.tool()
def list_event_types() -> list:
    """Return all available event types and the state each one triggers."""
    return _get("/api/event_types")


@mcp.tool()
def print_label(plant_id: int, style: str = "classic") -> dict:
    """
    Print a label for a plant on the server's Bluetooth thermal printer.

    Args:
        plant_id: ID of the plant.
        style:    "classic" (rectangular) or "circular".
    """
    return _post(f"/api/plants/{plant_id}/print", {"style": style})


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
