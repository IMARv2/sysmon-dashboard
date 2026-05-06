# routes/system.py — Crontab, GPU, processes
import subprocess
import os
import datetime
import psutil

from flask import Blueprint, jsonify, request
import state
from utils import login_required, crontab_list, crontab_add, crontab_delete

bp = Blueprint('system', __name__)


@bp.route("/api/crontab", methods=["GET"])
@login_required
def api_crontab_get():
    return jsonify({"jobs": crontab_list()})


@bp.route("/api/crontab", methods=["POST"])
@login_required
def api_crontab_post():
    data     = request.get_json(force=True)
    schedule = data.get("schedule", "").strip()
    command  = data.get("command",  "").strip()
    if not schedule or not command:
        return jsonify({"ok": False, "error": "schedule and command required"}), 400
    ok = crontab_add(schedule, command)
    return jsonify({"ok": ok})


@bp.route("/api/crontab/delete", methods=["POST"])
@login_required
def api_crontab_delete():
    data = request.get_json(force=True)
    raw  = data.get("raw", "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "raw line required"}), 400
    ok = crontab_delete(raw)
    return jsonify({"ok": ok})


@bp.route("/api/gpu")
@login_required
def api_gpu():
    gpus = []
    # Try nvidia-smi
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,"
             "memory.used,memory.total,power.draw,clocks.current.graphics",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                p = [x.strip() for x in line.split(",")]
                gpus.append({"type": "nvidia", "name": p[0],
                             "temp_c": int(p[1]), "util_pct": int(p[2]),
                             "mem_used_mb": int(p[3]), "mem_total_mb": int(p[4]),
                             "power_w": p[5], "clock_mhz": p[6]})
    except Exception:
        pass

    # Intel GPU via sysfs
    try:
        freq_path = "/sys/class/drm/card0/gt_act_freq_mhz"
        max_path  = "/sys/class/drm/card0/gt_max_freq_mhz"
        cur_freq  = int(open(freq_path).read().strip()) if os.path.exists(freq_path) else 0
        max_freq  = int(open(max_path).read().strip())  if os.path.exists(max_path)  else 0
        pkg_temp  = None
        for i in range(20):
            tp = f"/sys/class/thermal/thermal_zone{i}/type"
            tv = f"/sys/class/thermal/thermal_zone{i}/temp"
            if os.path.exists(tp) and "x86_pkg" in open(tp).read():
                pkg_temp = round(int(open(tv).read().strip()) / 1000, 1)
                break
        gpus.append({"type": "intel", "name": "Intel HD Graphics 620",
                     "cur_freq_mhz": cur_freq, "max_freq_mhz": max_freq,
                     "pkg_temp_c": pkg_temp, "util_pct": None,
                     "mem_used_mb": None, "mem_total_mb": None})
    except Exception:
        pass

    # NVIDIA via lspci if nvidia-smi failed
    if not any(g["type"] == "nvidia" for g in gpus):
        try:
            r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if "NVIDIA" in line or "GeForce" in line:
                    gpus.append({"type": "nvidia_no_driver",
                                 "name": line.split(":")[-1].strip(),
                                 "note": "nvidia-smi not available — install nvidia-driver"})
                    break
        except Exception:
            pass

    return jsonify({"gpus": gpus,
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@bp.route("/api/processes")
@login_required
def api_processes():
    sort_by = request.args.get("sort", "cpu")
    limit   = int(request.args.get("limit", "30"))
    procs   = []
    for p in psutil.process_iter(['pid', 'name', 'username', 'status',
                                  'cpu_percent', 'memory_percent',
                                  'memory_info', 'cmdline', 'create_time']):
        try:
            info = p.info
            procs.append({
                "pid":     info["pid"],
                "name":    info["name"],
                "user":    info["username"] or "",
                "status":  info["status"],
                "cpu_pct": round(info["cpu_percent"] or 0, 1),
                "mem_pct": round(info["memory_percent"] or 0, 2),
                "mem_mb":  round((info["memory_info"].rss if info["memory_info"] else 0) / 1024**2, 1),
                "cmd":     " ".join(info["cmdline"] or [])[:80] if info["cmdline"] else info["name"],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    key_map = {"cpu": "cpu_pct", "mem": "mem_pct", "name": "name"}
    key     = key_map.get(sort_by, "cpu_pct")
    reverse = sort_by != "name"
    procs.sort(key=lambda p: p[key], reverse=reverse)
    return jsonify({"processes": procs[:limit], "total": len(procs)})


@bp.route("/api/processes/<int:pid>/kill", methods=["POST"])
@login_required
def api_kill_process(pid):
    if pid <= 1:
        return jsonify({"ok": False, "error": "cannot kill system process"}), 400
    try:
        p = psutil.Process(pid)
        name = p.name()
        if p.username() != os.environ.get("USER", "imar") and os.geteuid() != 0:
            return jsonify({"ok": False, "error": "permission denied"}), 403
        p.terminate()
        state.log_event("Process killed", f"PID {pid} ({name}) terminated")
        return jsonify({"ok": True, "name": name})
    except psutil.NoSuchProcess:
        return jsonify({"ok": False, "error": "process not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
