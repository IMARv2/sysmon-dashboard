"""state.py — In-memory shared state + helpers (Telegram, events, history, devices)."""
import threading
import datetime
import time
import hashlib
import hmac
import os
import logging
import urllib.request
import urllib.parse
import psutil
from collections import deque

import config
import db as _db_mod

logger = logging.getLogger(__name__)

# ── In-memory state ───────────────────────────────────────────────────────────
_devices      = {}
_devices_lock = threading.Lock()
_scan_status  = {"last_scan": None, "scanning": False}

_history_lock = threading.Lock()
_history = {
    "ts":         deque(maxlen=config.HISTORY_POINTS),
    "cpu":        deque(maxlen=config.HISTORY_POINTS),
    "ram":        deque(maxlen=config.HISTORY_POINTS),
    "net_sent":   deque(maxlen=config.HISTORY_POINTS),
    "net_recv":   deque(maxlen=config.HISTORY_POINTS),
    "disk_read":  deque(maxlen=config.HISTORY_POINTS),
    "disk_write": deque(maxlen=config.HISTORY_POINTS),
    "cpu_temp":   deque(maxlen=config.HISTORY_POINTS),
}

_uptime_lock     = threading.Lock()
_uptime_events   = []
_dashboard_start = datetime.datetime.now()

_alert_last = {}

# ── Seed devices from DB on startup ──────────────────────────────────────────
def load_devices():
    rows = _db_mod.load_devices_from_db()
    with _devices_lock:
        for r in rows:
            if r["ip"] not in _devices:
                _devices[r["ip"]] = {
                    "ip": r["ip"], "mac": r["mac"] or "—",
                    "hostname": r["hostname"] or "", "vendor": "",
                    "label": r["label"] or "",
                    "first_seen": r["first_seen"] or "",
                    "last_seen":  r["last_seen"]  or "",
                    "online": False, "iface": "enp2s0",
                }

def persist_devices_snapshot():
    with _devices_lock:
        snapshot = list(_devices.values())
    _db_mod.persist_devices(snapshot)

# ── Telegram ──────────────────────────────────────────────────────────────────
def _tg_send_sync(text):
    try:
        if not config.TG_TOKEN or not config.TG_CHAT_ID:
            return
        data = urllib.parse.urlencode({"chat_id": config.TG_CHAT_ID, "text": text}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{config.TG_TOKEN}/sendMessage", data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error(f"[tg] send error: {e}")

def tg_send(text):
    threading.Thread(target=_tg_send_sync, args=(text,), daemon=True).start()

# ── Event log ─────────────────────────────────────────────────────────────────
def log_event(event, detail=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _uptime_lock:
        _uptime_events.append({"ts": ts, "event": event, "detail": detail})
        if len(_uptime_events) > 500:
            _uptime_events.pop(0)
    _db_mod.persist_event(event, f"{event}: {detail}" if detail else event)

# ── CPU temperature ───────────────────────────────────────────────────────────
def get_cpu_temp():
    try:
        temps = psutil.sensors_temperatures()
        for name in ('coretemp', 'cpu_thermal', 'k10temp', 'acpitz'):
            if name in temps and temps[name]:
                return round(temps[name][0].current, 1)
    except Exception:
        pass
    return None

# ── Password verification ─────────────────────────────────────────────────────
def verify_password(plain, stored):
    if stored.startswith("pbkdf2:"):
        parts = stored.split(":")
        if len(parts) == 5:
            _, algo, iters_s, salt, expected = parts
            try:
                dk = hashlib.pbkdf2_hmac(algo, plain.encode(), salt.encode(), int(iters_s))
                return hmac.compare_digest(dk.hex(), expected)
            except Exception:
                return False
        return False
    if stored.startswith("sha256:"):
        parts = stored.split(":")
        if len(parts) == 3:
            _, salt, expected = parts
            h = hashlib.sha256((salt + plain).encode()).hexdigest()
            return hmac.compare_digest(h, expected)
        return False
    return hmac.compare_digest(plain, stored)

def make_password_hash(plain, iters=260000):
    salt = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    dk   = hashlib.pbkdf2_hmac('sha256', plain.encode(), salt.encode(), iters)
    return f"pbkdf2:sha256:{iters}:{salt}:{dk.hex()}"
