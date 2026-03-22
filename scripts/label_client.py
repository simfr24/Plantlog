#!/usr/bin/env python3
"""
Plantlog label client.

Runs on the machine physically connected to the Bluetooth thermal printer.
Polls the Plantlog server for pending print jobs, prints them, reports back.

Dependencies:
    pip install requests Pillow

Configuration is loaded in this order:
    1. Environment variables (PLANTLOG_URL, PLANTLOG_API_KEY, PRINTER_MAC, …)
    2. JSON config file  (~/.config/plantlog/label_client.json  or  ./label_client.json)
    3. Interactive prompt (saves answers to JSON for next time)
"""

import json
import os
import sys
import time
import struct
import socket
import logging
import platform
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    import PIL.Image
    import PIL.ImageDraw
    import PIL.ImageFont
    import PIL.ImageOps
except ImportError:
    print("ERROR: pip install Pillow", file=sys.stderr)
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
        Path("label_client.json"),          # current directory
        _default_config_path(),             # platform config dir
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
    cfg["url"]          = _prompt("Plantlog server URL", cfg.get("url", "https://"))
    cfg["api_key"]      = _prompt("API key (from Settings → API Key)", cfg.get("api_key", ""))
    cfg["printer_mac"]  = _prompt("Printer Bluetooth MAC", cfg.get("printer_mac", "25:00:14:00:83:5E"))
    cfg["printer_port"] = int(_prompt("Printer RFCOMM port", str(cfg.get("printer_port", 2))))
    cfg["poll_interval"]= int(_prompt("Poll interval (seconds)", str(cfg.get("poll_interval", 5))))
    save = _prompt("Save config for next time? (y/n)", "y").lower()
    if save == "y":
        _save_json_config(cfg)
    return cfg


def load_config() -> dict:
    """
    Merge config sources. Priority: env vars > JSON file > interactive prompt.
    Returns a complete config dict.
    """
    file_cfg = _load_json_config()

    cfg = {
        "url":           os.environ.get("PLANTLOG_URL")     or file_cfg.get("url",           ""),
        "api_key":       os.environ.get("PLANTLOG_API_KEY") or file_cfg.get("api_key",       ""),
        "printer_mac":   os.environ.get("PRINTER_MAC")      or file_cfg.get("printer_mac",   ""),
        "printer_port":  int(os.environ.get("PRINTER_PORT", file_cfg.get("printer_port",  2))),
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


# ── label generation ──────────────────────────────────────────────────────────

PRINTER_WIDTH = 384


def _find_font(variant="regular"):
    paths = {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            "C:/Windows/Fonts/times.ttf",
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "C:/Windows/Fonts/timesbd.ttf",
        ],
        "italic": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
            "C:/Windows/Fonts/timesi.ttf",
        ],
    }
    for f in paths.get(variant, paths["regular"]):
        try:
            PIL.ImageFont.truetype(f, 12)
            return f
        except OSError:
            continue
    return None


def _font(variant, size):
    path = _find_font(variant)
    return PIL.ImageFont.truetype(path, size) if path else PIL.ImageFont.load_default()


def _wrap(text, font, max_w):
    words, lines = text.split(), [""]
    for word in words:
        test = f"{lines[-1]} {word}".strip()
        if font.getlength(test) <= max_w:
            lines[-1] = test
        else:
            lines.append(word)
    return "\n".join(lines)


def _make_classic(common, latin, date_str, variety=None):
    W = PRINTER_WIDTH
    margin, pad_t, pad_b = 20, 30, 25
    nf = _font("bold", 40);   lf = _font("italic", 22)
    df = _font("regular", 16); vf = _font("regular", 14)
    tmp = PIL.Image.new("1", (1, 1)); td = PIL.ImageDraw.Draw(tmp)
    nl = _wrap(common, nf, W - margin * 2 - 20)
    nb = td.multiline_textbbox((0, 0), nl, font=nf)
    nw, nh = nb[2] - nb[0], nb[3] - nb[1]
    lb = td.textbbox((0, 0), latin, font=lf);       lh = lb[3] - lb[1]
    db = td.textbbox((0, 0), date_str, font=df);    dw, dh = db[2] - db[0], db[3] - db[1]
    vh = (td.textbbox((0, 0), variety, font=vf)[3] - td.textbbox((0, 0), variety, font=vf)[1] + 6) if variety else 0
    total_h = pad_t + nh + 12 + lh + vh + 18 + 10 + dh + pad_b
    img = PIL.Image.new("1", (W, total_h), 1); d = PIL.ImageDraw.Draw(img)
    bx0, by0, bx1, by1 = margin - 5, 5, W - margin + 5, total_h - 6
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=3)
    d.rectangle([bx0 + 6, by0 + 6, bx1 - 6, by1 - 6], outline=0, width=1)
    y = pad_t
    d.multiline_text(((W - nw) // 2, y), nl, font=nf, fill=0, align="center"); y += nh + 8
    d.text((W // 2, y), latin, font=lf, fill=0, anchor="ma");                  y += lh + 6
    if variety:
        d.text((W // 2, y), variety, font=vf, fill=0, anchor="ma");            y += vh
    dm = margin + 15
    d.line([(dm, y - 1), (W - dm, y - 1)], fill=0, width=1)
    d.line([(dm, y + 1), (W - dm, y + 1)], fill=0, width=1);                   y += 15
    d.text(((W - dw) // 2, y), date_str, font=df, fill=0)
    return img


def _make_circular(common, latin, date_str, variety=None):
    diameter = int(PRINTER_WIDTH * 0.66)
    img = PIL.Image.new("1", (PRINTER_WIDTH, diameter + 20), 1)
    d = PIL.ImageDraw.Draw(img)
    cx, cy, r = PRINTER_WIDTH // 2, diameter // 2 + 10, diameter // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=2)
    d.ellipse([cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5], outline=0, width=1)
    nf = _font("bold", 28); lf = _font("italic", 14)
    df = _font("regular", 12); vf = _font("regular", 11)
    nl = _wrap(common, nf, int(r * 1.4))
    nb = d.multiline_textbbox((0, 0), nl, font=nf); nh = nb[3] - nb[1]
    lb = d.textbbox((0, 0), latin, font=lf)
    db_b = d.textbbox((0, 0), date_str, font=df)
    vh = (d.textbbox((0, 0), variety, font=vf)[3] - d.textbbox((0, 0), variety, font=vf)[1] + 4) if variety else 0
    total_h = nh + 8 + (lb[3] - lb[1]) + vh + 6 + (db_b[3] - db_b[1])
    y = cy - total_h // 2
    d.multiline_text((cx, y), nl, font=nf, fill=0, anchor="ma", align="center"); y += nh + 8
    d.text((cx, y), latin, font=lf, fill=0, anchor="ma");                        y += (lb[3] - lb[1]) + 6
    if variety:
        d.text((cx, y), variety, font=vf, fill=0, anchor="ma");                  y += vh
    d.text((cx, y), date_str, font=df, fill=0, anchor="ma")
    return img


def make_label(job):
    plant    = job["plant"]
    style    = job.get("style", "classic")
    date_str = date.today().strftime("%d-%m-%Y")
    return (_make_circular if style == "circular" else _make_classic)(
        plant["common"], plant["latin"], date_str, plant.get("variety") or None
    )


# ── Bluetooth printing ────────────────────────────────────────────────────────

def print_image(img, mac, port):
    if img.width < PRINTER_WIDTH:
        padded = PIL.Image.new("1", (PRINTER_WIDTH, img.height), 1)
        padded.paste(img); img = padded
    if img.size[0] % 8:
        img2 = PIL.Image.new("1", (img.size[0] + 8 - img.size[0] % 8, img.size[1]), "white")
        img2.paste(img, (0, 0)); img = img2
    img = PIL.ImageOps.invert(img.convert("L")).convert("1")
    buf = (
        b"\x1d\x76\x30\x00"
        + struct.pack("2B", img.size[0] // 8 % 256, img.size[0] // 8 // 256)
        + struct.pack("2B", img.size[1] % 256, img.size[1] // 256)
        + img.tobytes()
    )
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.connect((mac, port))
    try:
        s.send(b"\x1e\x47\x03"); s.recv(38); time.sleep(0.5)
        s.send(b"\x1D\x67\x39"); s.recv(21); time.sleep(0.5)
        s.send(b"\x1b\x40");                  time.sleep(0.5)
        s.send(b"\x1d\x49\xf0\x19");         time.sleep(0.5)
        for i in range(0, len(buf), 512):
            s.send(buf[i:i + 512]); time.sleep(0.02)
        time.sleep(max(3, img.height * 0.03))
        s.send(b"\x0a\x0a\x0a\x0a"); time.sleep(1)
    finally:
        s.close()


# ── API helpers ───────────────────────────────────────────────────────────────

def fetch_pending(base_url, headers):
    r = requests.get(f"{base_url}/api/print_queue/pending", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


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
    headers  = {"Authorization": f"Bearer {cfg['api_key']}"}
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

    while True:
        try:
            jobs = fetch_pending(base_url, headers)
            for job in jobs:
                jid  = job["job_id"]
                name = job["plant"]["common"]
                log.info("Job #%s — printing '%s' (%s)", jid, name, job["style"])
                try:
                    img = make_label(job)
                    print_image(img, mac, port)
                    mark_done(base_url, headers, jid)
                    log.info("Job #%s — done", jid)
                except Exception as exc:
                    log.error("Job #%s — FAILED: %s", jid, exc)
                    mark_error(base_url, headers, jid, str(exc))
        except requests.exceptions.ConnectionError:
            log.warning("Cannot reach server — will retry")
        except Exception as exc:
            log.error("Unexpected error: %s", exc)

        time.sleep(interval)


if __name__ == "__main__":
    main()
