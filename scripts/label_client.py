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
import locale
import os
import sys
import time
import socket
import logging
from pathlib import Path


DEFAULT_URL           = "https://plantlog.fr"
DEFAULT_PRINTER_PORT  = 2
DEFAULT_POLL_INTERVAL = 5


TRANSLATIONS = {
    "en": {
        "setup_header":   "── Plantlog label client setup ──────────────────",
        "setup_hint":     "Press Enter to keep the current value.",
        "api_key":        "API key (from Settings → API Key)",
        "printer_mac":    "Printer Bluetooth MAC",
        "config_loaded":  "Config loaded from {path}",
        "config_saved":   "Config saved to {path}",
        "config_warn":    "Warning: could not read {path}: {err}",
        "err_missing":    "ERROR: missing config: {keys}",
        "err_hint":       "Set env vars or run interactively to create a config file.",
        "started":        "Plantlog label client started",
        "server":         "Server : {url}",
        "printer":        "Printer: {mac} (port {port})",
        "polling":        "Polling every {sec}s — Ctrl-C to stop",
        "connecting":     "Connecting to printer…",
        "connected":      "Printer connected",
        "job_printing":   "Job #{jid} — printing '{name}' ({style})",
        "job_done":       "Job #{jid} — done",
        "job_failed":     "Job #{jid} — FAILED: {err}",
        "warn_server":    "Cannot reach server — will retry",
        "unexpected":     "Unexpected error: {err}",
        "interrupted":    "Interrupted — shutting down",
        "closed":         "Printer connection closed",
    },
    "fr": {
        "setup_header":   "── Configuration du client d'étiquettes Plantlog ──",
        "setup_hint":     "Appuyez sur Entrée pour conserver la valeur actuelle.",
        "api_key":        "Clé API (depuis Paramètres → Clé API)",
        "printer_mac":    "Adresse MAC Bluetooth de l'imprimante",
        "config_loaded":  "Configuration chargée depuis {path}",
        "config_saved":   "Configuration enregistrée dans {path}",
        "config_warn":    "Attention : lecture impossible de {path} : {err}",
        "err_missing":    "ERREUR : configuration manquante : {keys}",
        "err_hint":       "Définissez les variables d'environnement ou lancez en interactif.",
        "started":        "Client d'étiquettes Plantlog démarré",
        "server":         "Serveur  : {url}",
        "printer":        "Imprimante : {mac} (port {port})",
        "polling":        "Interrogation toutes les {sec}s — Ctrl-C pour arrêter",
        "connecting":     "Connexion à l'imprimante…",
        "connected":      "Imprimante connectée",
        "job_printing":   "Tâche #{jid} — impression de '{name}' ({style})",
        "job_done":       "Tâche #{jid} — terminée",
        "job_failed":     "Tâche #{jid} — ÉCHEC : {err}",
        "warn_server":    "Serveur injoignable — nouvelle tentative",
        "unexpected":     "Erreur inattendue : {err}",
        "interrupted":    "Interrompu — arrêt en cours",
        "closed":         "Connexion imprimante fermée",
    },
    "ru": {
        "setup_header":   "── Настройка клиента печати Plantlog ──────────",
        "setup_hint":     "Нажмите Enter, чтобы оставить текущее значение.",
        "api_key":        "API-ключ (из Настройки → API-ключ)",
        "printer_mac":    "Bluetooth MAC-адрес принтера",
        "config_loaded":  "Конфигурация загружена из {path}",
        "config_saved":   "Конфигурация сохранена в {path}",
        "config_warn":    "Предупреждение: не удалось прочитать {path}: {err}",
        "err_missing":    "ОШИБКА: отсутствует конфигурация: {keys}",
        "err_hint":       "Задайте переменные окружения или запустите интерактивно.",
        "started":        "Клиент печати Plantlog запущен",
        "server":         "Сервер  : {url}",
        "printer":        "Принтер : {mac} (порт {port})",
        "polling":        "Опрос каждые {sec} с — Ctrl-C для остановки",
        "connecting":     "Подключение к принтеру…",
        "connected":      "Принтер подключён",
        "job_printing":   "Задание #{jid} — печать '{name}' ({style})",
        "job_done":       "Задание #{jid} — готово",
        "job_failed":     "Задание #{jid} — ОШИБКА: {err}",
        "warn_server":    "Сервер недоступен — повтор",
        "unexpected":     "Непредвиденная ошибка: {err}",
        "interrupted":    "Прервано — завершение работы",
        "closed":         "Соединение с принтером закрыто",
    },
}


def _detect_lang() -> str:
    try:
        code = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "")
    except Exception:
        code = ""
    code = (code or os.environ.get("LANG", "")).lower()[:2]
    return code if code in TRANSLATIONS else "en"


T = TRANSLATIONS[_detect_lang()]

try:
    import requests
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr)
    sys.exit(1)


# ── config loading ────────────────────────────────────────────────────────────

def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _default_config_path():
    return _app_dir() / "label_client.json"


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
                print(T["config_loaded"].format(path=path))
                return data
            except Exception as e:
                print(T["config_warn"].format(path=path, err=e))
    return {}


def _save_json_config(cfg: dict):
    path = _default_config_path()
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(T["config_saved"].format(path=path))


def _prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def _interactive_setup(existing: dict) -> dict:
    print("\n" + T["setup_header"])
    print(T["setup_hint"] + "\n")
    cfg = dict(existing)
    cfg["api_key"]     = _prompt(T["api_key"],     cfg.get("api_key", ""))
    cfg["printer_mac"] = _prompt(T["printer_mac"], cfg.get("printer_mac", ""))
    _save_json_config(cfg)
    return cfg


def load_config() -> dict:
    file_cfg = _load_json_config()
    cfg = {
        "url":           os.environ.get("PLANTLOG_URL")     or file_cfg.get("url",         DEFAULT_URL),
        "api_key":       os.environ.get("PLANTLOG_API_KEY") or file_cfg.get("api_key",     ""),
        "printer_mac":   os.environ.get("PRINTER_MAC")      or file_cfg.get("printer_mac", ""),
        "printer_port":  int(os.environ.get("PRINTER_PORT",  file_cfg.get("printer_port",  DEFAULT_PRINTER_PORT))),
        "poll_interval": int(os.environ.get("POLL_INTERVAL", file_cfg.get("poll_interval", DEFAULT_POLL_INTERVAL))),
    }
    missing = [k for k in ("api_key", "printer_mac") if not cfg[k]]
    if missing:
        if sys.stdin.isatty():
            cfg = _interactive_setup(cfg)
            cfg.setdefault("url", DEFAULT_URL)
            cfg.setdefault("printer_port", DEFAULT_PRINTER_PORT)
            cfg.setdefault("poll_interval", DEFAULT_POLL_INTERVAL)
        else:
            print(T["err_missing"].format(keys=", ".join(missing)), file=sys.stderr)
            print(T["err_hint"], file=sys.stderr)
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

    log.info(T["started"])
    log.info(T["server"].format(url=base_url))
    log.info(T["printer"].format(mac=mac, port=port))
    log.info(T["polling"].format(sec=interval))

    printer = Printer(mac, port)
    log.info(T["connecting"])
    printer.connect()
    log.info(T["connected"])

    try:
        while True:
            try:
                jobs = fetch_pending(base_url, headers)
                for job in jobs:
                    jid  = job["job_id"]
                    name = job["plant"]["common"]
                    log.info(T["job_printing"].format(jid=jid, name=name, style=job["style"]))
                    try:
                        data = fetch_label_bytes(base_url, headers, jid)
                        printer.print_bytes(data)
                        mark_done(base_url, headers, jid)
                        log.info(T["job_done"].format(jid=jid))
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        log.error(T["job_failed"].format(jid=jid, err=exc))
                        mark_error(base_url, headers, jid, str(exc))
            except KeyboardInterrupt:
                raise
            except requests.exceptions.ConnectionError:
                log.warning(T["warn_server"])
            except Exception as exc:
                log.error(T["unexpected"].format(err=exc))

            time.sleep(interval)
    except KeyboardInterrupt:
        log.info(T["interrupted"])
    finally:
        printer.close()
        log.info(T["closed"])


if __name__ == "__main__":
    main()
