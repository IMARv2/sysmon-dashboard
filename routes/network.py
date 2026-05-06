# routes/network.py — Ping, port scan, Wake-on-LAN, bandwidth
import subprocess
import socket
import re
import json

from flask import Blueprint, jsonify, request
import config
import state
from utils import login_required, _send_wol

bp = Blueprint('network', __name__)


@bp.route("/api/ping")
@login_required
def api_ping():
    ip = request.args.get("ip", "").strip()
    if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
        return jsonify({"error": "invalid ip"}), 400
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "1", ip],
            capture_output=True, text=True, timeout=6)
        m = re.search(r'min/avg/max.*?=\s*[\d.]+/([\d.]+)/', result.stdout)
        if m:
            return jsonify({"ip": ip, "avg_ms": float(m.group(1)), "online": True})
        return jsonify({"ip": ip, "avg_ms": None, "online": False})
    except Exception:
        return jsonify({"ip": ip, "avg_ms": None, "online": False})


@bp.route("/api/ports")
@login_required
def api_ports():
    ip = request.args.get("ip", "").strip()
    if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
        return jsonify({"error": "invalid ip"}), 400
    PORTS = {22: "SSH", 80: "HTTP", 443: "HTTPS", 445: "SMB",
             3389: "RDP", 8080: "HTTP-Alt", 5000: "Dashboard", 21: "FTP"}
    results = []
    for port, name in PORTS.items():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            open_ = s.connect_ex((ip, port)) == 0
            s.close()
        except Exception:
            open_ = False
        results.append({"port": port, "name": name, "open": open_})
    return jsonify({"ip": ip, "ports": results})


@bp.route("/api/wol", methods=["POST"])
@login_required
def api_wol():
    data = request.get_json(force=True)
    mac  = data.get("mac", "").strip()
    if not mac or mac == "—":
        return jsonify({"ok": False, "error": "MAC address required"}), 400
    try:
        ok = _send_wol(mac)
        if ok:
            state.log_event("Wake-on-LAN", f"Magic packet sent to {mac}")
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/bandwidth")
@login_required
def api_bandwidth():
    iface  = request.args.get("iface", "enp2s0")
    period = request.args.get("period", "d")   # d=daily, m=monthly
    try:
        r = subprocess.run(["vnstat", "--json", period, "-i", iface],
                           capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
        ifaces = data.get("interfaces", [])
        if not ifaces:
            return jsonify({"error": "no data yet — vnstat needs time to collect"})
        traffic = ifaces[0].get("traffic", {})
        entries = traffic.get({"d": "day", "m": "month"}.get(period, "day"), [])
        result  = []
        for e in entries[-30:]:
            date_str = f"{e['date']['year']}-{e['date']['month']:02d}-{e['date'].get('day',1):02d}"
            result.append({
                "date": date_str,
                "rx_mb": round(e["rx"] / 1024**2, 1),
                "tx_mb": round(e["tx"] / 1024**2, 1),
            })
        iface_list_r = subprocess.run(["vnstat", "--iflist"],
                                      capture_output=True, text=True, timeout=5)
        ifaces_available = re.findall(
            r'\b(\w[\w.]+)\b',
            iface_list_r.stdout.split(":")[-1]) if ":" in iface_list_r.stdout else []
        return jsonify({"iface": iface, "period": period,
                        "entries": result, "interfaces": ifaces_available[:10]})
    except Exception as e:
        return jsonify({"error": str(e), "entries": []})
