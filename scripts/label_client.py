#!/usr/bin/env python3
"""
Plantlog label client.

Runs on the machine physically connected to the Bluetooth thermal printer.
Polls the Plantlog server for pending print jobs, fetches rendered bytes,
sends them to the printer, and reports back.

Dependencies:
    pip install requests

Configuration is loaded in this order:
    1. Environment variables (PLANTLOG_URL, PLANTLOG_API_KEY, PRINTER_MAC, …)
    2. JSON config file  (~/.config/plantlog/label_client.json  or  ./label_client.json)
    3. Interactive prompt (saves answers to JSON for next time)
"""

import json
import os
import sys
import time
import socket
import logging
import platform
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr)
    sys.exit(1)


# ── config loading ────────────────────────────────────────────────────────────

def _default_config_path():
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "plantlog" / "label_client.json"


def _load_json_config():
    candidates = [
        Path("label_client.json"),
        _default_config_path(),
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                print(f"Config loaded from {path}")
                return data
            except Exception as e:
                print(f"Warning: could not read {path}: {e}")
    return {}


def _save_json_config(cfg: dict):
    path = _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Config saved to {path}")


def _prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def _interactive_setup(existing: dict) -> dict:
    print("\n── Plantlog label client setup ──────────────────")
    print("Press Enter to keep the current value.\n")
    cfg = dict(existing)
    cfg["url"]           = _prompt("Plantlog server URL", cfg.get("url", "https://"))
    cfg["api_key"]       = _prompt("API key (from Settings → API Key)", cfg.get("api_key", ""))
    cfg["printer_mac"]   = _prompt("Printer Bluetooth MAC", cfg.get("printer_mac", "25:00:14:00:83:5E"))
    cfg["printer_port"]  = int(_prompt("Printer RFCOMM port", str(cfg.get("printer_port", 2))))
    cfg["poll_interval"] = int(_prompt("Poll interval (seconds)", str(cfg.get("poll_interval", 5))))
    save = _prompt("Save config for next time? (y/n)", "y").lower()
    if save == "y":
        _save_json_config(cfg)
    return cfg


def load_config() -> dict:
    file_cfg = _load_json_config()
    cfg = {
        "url":           os.environ.get("PLANTLOG_URL")     or file_cfg.get("url",           ""),
        "api_key":       os.environ.get("PLANTLOG_API_KEY") or file_cfg.get("api_key",       ""),
        "printer_mac":   os.environ.get("PRINTER_MAC")      or file_cfg.get("printer_mac",   ""),
        "printer_port":  int(os.environ.get("PRINTER_PORT",  file_cfg.get("printer_port",  2))),
        "poll_interval": int(os.environ.get("POLL_INTERVAL", file_cfg.get("poll_interval", 5))),
    }
    missing = [k for k in ("url", "api_key", "printer_mac") if not cfg[k]]
    if missing:
        if sys.stdin.isatty():
            cfg = _interactive_setup(cfg)
        else:
            print(f"ERROR: missing config: {', '.join(missing)}", file=sys.stderr)
            print("Set env vars or run interactively to create a config file.", file=sys.stderr)
            sys.exit(1)
    return cfg


# ── Bluetooth printing ────────────────────────────────────────────────────────

class Printer:
    """Persistent Bluetooth RFCOMM connection with auto-reconnect."""

    def __init__(self, mac, port):
        self.mac   = mac
        self.port  = port
        self._sock = None

    def _connect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.connect((self.mac, self.port))
        s.send(b"\x1e\x47\x03"); s.recv(38); time.sleep(0.5)
        s.send(b"\x1D\x67\x39"); s.recv(21); time.sleep(0.5)
        self._sock = s

    def connect(self):
        self._connect()

    def print_bytes(self, data: bytes):
        # Height is encoded at bytes 6-7 (little-endian) in the ESC/POS GS v 0 header
        height = data[6] | (data[7] << 8)
        try:
            self._send(data, height)
        except (OSError, BrokenPipeError):
            self._connect()
            self._send(data, height)

    def _send(self, buf: bytes, img_height: int):
        s = self._sock
        s.send(b"\x1b\x40");          time.sleep(0.5)
        s.send(b"\x1d\x49\xf0\x19"); time.sleep(0.5)
        for i in range(0, len(buf), 512):
            s.send(buf[i:i + 512]); time.sleep(0.02)
        time.sleep(max(3, img_height * 0.03))
        s.send(b"\x0a\x0a\x0a\x0a"); time.sleep(1)

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


# ── API helpers ───────────────────────────────────────────────────────────────

def fetch_pending(base_url, headers):
    r = requests.get(f"{base_url}/api/print_queue/pending", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_label_bytes(base_url, headers, job_id) -> bytes:
    r = requests.get(f"{base_url}/api/print_queue/{job_id}/bytes", headers=headers, timeout=30)
    r.raise_for_status()
    return r.content


def mark_done(base_url, headers, job_id):
    requests.post(f"{base_url}/api/print_queue/{job_id}/done", headers=headers, timeout=10)


def mark_error(base_url, headers, job_id, msg):
    requests.post(
        f"{base_url}/api/print_queue/{job_id}/error",
        json={"error": msg}, headers=headers, timeout=10,
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    base_url = cfg["url"].rstrip("/")
    headers  = {"X-API-Key": cfg["api_key"]}
    mac      = cfg["printer_mac"]
    port     = cfg["printer_port"]
    interval = cfg["poll_interval"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("label_client")

    log.info("Plantlog label client started")
    log.info("Server : %s", base_url)
    log.info("Printer: %s (port %s)", mac, port)
    log.info("Polling every %ss — Ctrl-C to stop", interval)

    printer = Printer(mac, port)
    log.info("Connecting to printer…")
    printer.connect()
    log.info("Printer connected")

    try:
        while True:
            try:
                jobs = fetch_pending(base_url, headers)
                for job in jobs:
                    jid  = job["job_id"]
                    name = job["plant"]["common"]
                    log.info("Job #%s — printing '%s' (%s)", jid, name, job["style"])
                    try:
                        data = fetch_label_bytes(base_url, headers, jid)
                        printer.print_bytes(data)
                        mark_done(base_url, headers, jid)
                        log.info("Job #%s — done", jid)
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        log.error("Job #%s — FAILED: %s", jid, exc)
                        mark_error(base_url, headers, jid, str(exc))
            except KeyboardInterrupt:
                raise
            except requests.exceptions.ConnectionError:
                log.warning("Cannot reach server — will retry")
            except Exception as exc:
                log.error("Unexpected error: %s", exc)

            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
    finally:
        printer.close()
        log.info("Printer connection closed")


if __name__ == "__main__":
    main()
