# routes/core.py — Stats, devices, history, uptime, alerts, scan, services
import subprocess
import datetime
import time
import psutil
import threading

from flask import Blueprint, jsonify, request
import config
import state
import db as _db
import background
from utils import login_required, get_system_info, get_network_info

bp = Blueprint('core', __name__)


@bp.route("/api/services")
@login_required
def api_services():
    SYSTEM_SERVICES = [
        {"name": "dashboard",    "label": "SysMon Dashboard", "port": 5000,
         "url": f"http://{config.MY_IP}:5000"},
        {"name": "tailscaled",   "label": "Tailscale VPN",    "port": None, "url": None},
        {"name": "smbd",         "label": "Samba",            "port": 445,  "url": None},
        {"name": "wg-quick@wg0", "label": "WireGuard",        "port": None, "url": None},
    ]
    DOCKER_SERVICES = [
        {"name": "ghost-browser", "label": "Ghost Browser", "port": 3000,
         "url": f"http://{config.MY_IP}:3000",  "type": "docker"},
        {"name": "portainer",     "label": "Portainer",     "port": 9443,
         "url": f"https://{config.MY_IP}:9443", "type": "docker"},
        {"name": "uptime-kuma",   "label": "Uptime Kuma",   "port": 3001,
         "url": f"http://{config.MY_IP}:3001",  "type": "docker"},
    ]
    results = []
    for svc in SYSTEM_SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc["name"]],
                               capture_output=True, text=True, timeout=3)
            active = r.stdout.strip() == "active"
        except Exception:
            active = False
        results.append({**svc, "active": active, "type": "systemd"})
    for svc in DOCKER_SERVICES:
        try:
            r = subprocess.run(
                ["sudo", "docker", "inspect", "--format", "{{.State.Status}}", svc["name"]],
                capture_output=True, text=True, timeout=5)
            status = r.stdout.strip()
            active = status == "running"
        except Exception:
            active = False
            status = "unknown"
        results.append({**svc, "active": active,
                        "docker_status": status if not active else "running"})
    return jsonify({"services": results,
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@bp.route("/api/stats")
@login_required
def api_stats():
    return jsonify({
        "system":    get_system_info(),
        "network":   get_network_info(),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@bp.route("/api/devices")
@login_required
def api_devices():
    with state._devices_lock:
        devices = sorted(list(state._devices.values()),
                         key=lambda d: (not d["online"], d["ip"]))
    return jsonify({
        "devices":     devices,
        "scan_status": state._scan_status.copy(),
        "timestamp":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@bp.route("/api/history")
@login_required
def api_history():
    with state._history_lock:
        return jsonify({k: list(v) for k, v in state._history.items()})


@bp.route("/api/uptime")
@login_required
def api_uptime():
    boot     = datetime.datetime.fromtimestamp(psutil.boot_time())
    up_sec   = (datetime.datetime.now() - boot).total_seconds()
    dash_sec = (datetime.datetime.now() - state._dashboard_start).total_seconds()
    with state._uptime_lock:
        mem_events = list(reversed(state._uptime_events[-100:]))
    since_sec = request.args.get("since", type=int)
    cutoff    = (time.time() - since_sec) if since_sec else 0
    db_events = _db.query_events(since_ts=cutoff)
    return jsonify({
        "server_boot":          boot.strftime("%Y-%m-%d %H:%M:%S"),
        "server_uptime_sec":    int(up_sec),
        "dashboard_start":      state._dashboard_start.strftime("%Y-%m-%d %H:%M:%S"),
        "dashboard_uptime_sec": int(dash_sec),
        "events":               mem_events,
        "db_events":            db_events,
        "alert_thresholds":     config.ALERT_THRESHOLDS,
    })


@bp.route("/api/alerts/thresholds", methods=["POST"])
@login_required
def api_set_thresholds():
    data = request.get_json(force=True)
    for k in ("cpu", "ram", "disk", "temp"):
        if k in data:
            try:
                config.ALERT_THRESHOLDS[k] = int(data[k])
            except (ValueError, TypeError):
                pass
    return jsonify({"ok": True, "thresholds": config.ALERT_THRESHOLDS})


@bp.route("/api/scan", methods=["POST"])
@login_required
def api_scan_now():
    if state._scan_status["scanning"]:
        return jsonify({"ok": False, "message": "Scan already in progress"})
    threading.Thread(target=background.do_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "Scan started"})
