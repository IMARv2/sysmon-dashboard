# routes/docker.py — Docker container management
import subprocess
import re

from flask import Blueprint, jsonify, request
import state
from utils import login_required

bp = Blueprint('docker', __name__)


@bp.route("/api/docker/containers")
@login_required
def api_docker_containers():
    try:
        ps = subprocess.run(
            ["sudo", "docker", "ps", "-a",
             "--format", "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.CreatedAt}}"],
            capture_output=True, text=True, timeout=10)
        containers = []
        for line in ps.stdout.strip().splitlines():
            parts = line.split("|", 5)
            if len(parts) < 6:
                continue
            cid, name, image, status, ports, created = parts
            running = status.lower().startswith("up")
            containers.append({"id": cid, "name": name, "image": image,
                                "status": status, "ports": ports,
                                "created": created, "running": running})
        if containers:
            stats_r = subprocess.run(
                ["sudo", "docker", "stats", "--no-stream",
                 "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}|{{.BlockIO}}"],
                capture_output=True, text=True, timeout=15)
            stats_map = {}
            for line in stats_r.stdout.strip().splitlines():
                p = line.split("|", 5)
                if len(p) == 6:
                    stats_map[p[0]] = {"cpu": p[1], "mem_usage": p[2],
                                       "mem_pct": p[3], "net_io": p[4], "block_io": p[5]}
            for c in containers:
                c["stats"] = stats_map.get(c["name"], {})
        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"containers": [], "error": str(e)})


@bp.route("/api/docker/<name>/action", methods=["POST"])
@login_required
def api_docker_action(name):
    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return jsonify({"ok": False, "error": "invalid name"}), 400
    data   = request.get_json(force=True)
    action = data.get("action", "").lower()
    if action not in ("start", "stop", "restart", "remove"):
        return jsonify({"ok": False, "error": "invalid action"}), 400
    cmd = ["sudo", "docker", "rm" if action == "remove" else action, name]
    if action == "remove":
        cmd = ["sudo", "docker", "rm", "-f", name]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    state.log_event(f"Docker {action}", f"{name} — {action}")
    return jsonify({"ok": r.returncode == 0,
                    "output": r.stdout.strip() or r.stderr.strip()})


@bp.route("/api/docker/<name>/logs")
@login_required
def api_docker_logs(name):
    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return jsonify({"ok": False, "error": "invalid name"}), 400
    lines = request.args.get("lines", "100")
    r = subprocess.run(["sudo", "docker", "logs", "--tail", lines, "--timestamps", name],
                       capture_output=True, text=True, timeout=15)
    log_lines = (r.stdout + r.stderr).strip().splitlines()
    return jsonify({"name": name, "lines": log_lines[-200:]})
