# routes/nodes.py — Remote node stats (Qwen machine + Claude machine + NAS)
import subprocess
import datetime
import json
import socket
import time
import urllib.request as _ur
import psutil

from flask import Blueprint, jsonify
import state
from utils import login_required

bp = Blueprint('nodes', __name__)

NAS_IP     = "10.22.11.120"
NAS_SSH    = ["ssh", "-i", "/home/imar/.ssh/id_ed25519",
              "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
              f"root@{NAS_IP}", "cat /tmp/nas_stats.json"]

_nas_full_cache      = None
_nas_full_cache_time = 0.0


def _tcp_check(host, port, timeout=3):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


@bp.route("/api/nodes/nas-full")
@login_required
def api_nodes_nas_full():
    global _nas_full_cache, _nas_full_cache_time
    smb_up = _tcp_check(NAS_IP, 445)
    nfs_up = _tcp_check(NAS_IP, 2049)
    now    = time.time()
    if _nas_full_cache and (now - _nas_full_cache_time) < 30:
        d = dict(_nas_full_cache)
        d["smb_up"]     = smb_up
        d["nfs_up"]     = nfs_up
        d["cache_age_s"] = int(now - _nas_full_cache_time)
        return jsonify(d)
    try:
        r = subprocess.run(NAS_SSH, capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        data = json.loads(r.stdout)
        data["smb_up"]      = smb_up
        data["nfs_up"]      = nfs_up
        data["cache_age_s"] = 0
        data["reachable"]   = True
        _nas_full_cache      = data
        _nas_full_cache_time = now
        return jsonify(data)
    except Exception as e:
        # SSH or stats file failed — fall back to TCP reachability
        tcp_up = smb_up or nfs_up
        return jsonify({"reachable": tcp_up, "smb_up": smb_up, "nfs_up": nfs_up,
                        "stats_error": str(e)})


@bp.route("/api/nodes/nas")
@login_required
def api_nodes_nas():
    web_up      = False
    response_ms = None
    try:
        t0  = time.time()
        req = _ur.Request(f"http://{NAS_IP}/", method="HEAD")
        with _ur.urlopen(req, timeout=5) as resp:
            response_ms = round((time.time() - t0) * 1000)
            web_up = resp.status < 500
    except Exception:
        pass

    smb_up    = _tcp_check(NAS_IP, 445)
    nfs_up    = _tcp_check(NAS_IP, 2049)
    reachable = web_up or smb_up or nfs_up

    return jsonify({
        "reachable":   reachable,
        "web_up":      web_up,
        "smb_up":      smb_up,
        "nfs_up":      nfs_up,
        "response_ms": response_ms,
        "timestamp":   datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
    })


@bp.route("/api/nodes/qwen-machine")
@login_required
def api_nodes_qwen_machine():
    try:
        with _ur.urlopen("http://10.22.11.11:9101/stats", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            data["reachable"] = True
            return jsonify(data)
    except Exception as e:
        return jsonify({"reachable": False, "error": str(e)})


@bp.route("/api/nodes/claude-machine")
@login_required
def api_nodes_claude_machine():
    try:
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures()
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps and temps[key]:
                    cpu_temp = round(temps[key][0].current, 1)
                    break
        except Exception:
            pass
        gpu = None
        try:
            r = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                p = [x.strip() for x in r.stdout.strip().split(",")]
                gpu = {"name": p[0], "temp_c": int(p[1]), "util_pct": int(p[2]),
                       "mem_used_mb": int(p[3]), "mem_total_mb": int(p[4]), "power_w": p[5]}
        except Exception:
            pass
        return jsonify({
            "reachable":     True,
            "cpu_pct":       psutil.cpu_percent(interval=0.5),
            "cpu_temp_c":    cpu_temp,
            "ram_used_gb":   round(mem.used  / 1e9, 2),
            "ram_total_gb":  round(mem.total / 1e9, 2),
            "ram_pct":       mem.percent,
            "disk_used_gb":  round(disk.used  / 1e9, 2),
            "disk_total_gb": round(disk.total / 1e9, 2),
            "disk_pct":      disk.percent,
            "gpu":           gpu,
            "ollama_online": False,
            "ollama_models": [],
            "timestamp":     datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"reachable": False, "error": str(e)})
