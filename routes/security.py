# routes/security.py — Security center (UFW, fail2ban, SSH, auth log) and Samba monitor
import subprocess
import re
import shutil
import psutil

from flask import Blueprint, jsonify, request
import config
import state
import db as _db
from utils import login_required

bp = Blueprint('security', __name__)


@bp.route("/api/security")
@login_required
def api_security():
    # UFW status
    ufw_rules  = []
    ufw_status = "inactive"
    try:
        r = subprocess.run(["sudo", "ufw", "status", "verbose"],
                           capture_output=True, text=True, timeout=10)
        lines = r.stdout.splitlines()
        ufw_status = "active" if any("Status: active" in l for l in lines) else "inactive"
        in_rules = False
        for line in lines:
            if line.startswith("To ") or line.startswith("--"):
                in_rules = True
                continue
            if in_rules and line.strip():
                ufw_rules.append(line.strip())
    except Exception:
        pass

    # fail2ban
    f2b_jails = []
    try:
        r = subprocess.run(["sudo", "fail2ban-client", "status"],
                           capture_output=True, text=True, timeout=10)
        jail_match = re.search(r"Jail list:\s*(.+)", r.stdout)
        if jail_match:
            jail_names = [j.strip() for j in jail_match.group(1).split(",") if j.strip()]
            for jail in jail_names:
                jr = subprocess.run(["sudo", "fail2ban-client", "status", jail],
                                    capture_output=True, text=True, timeout=10)
                banned = re.search(r"Currently banned:\s*(\d+)", jr.stdout)
                failed = re.search(r"Currently failed:\s*(\d+)", jr.stdout)
                total  = re.search(r"Total banned:\s*(\d+)",     jr.stdout)
                ips    = re.search(r"Banned IP list:\s*(.*)",     jr.stdout)
                f2b_jails.append({
                    "jail":   jail,
                    "banned": int(banned.group(1)) if banned else 0,
                    "failed": int(failed.group(1)) if failed else 0,
                    "total":  int(total.group(1))  if total  else 0,
                    "ips":    ips.group(1).strip().split() if ips and ips.group(1).strip() else [],
                })
    except Exception:
        pass

    # Failed login attempts from DB
    with _db._db_lock, _db.get_db() as conn:
        attempts = [dict(r) for r in conn.execute(
            "SELECT ip, count, locked_until FROM login_attempts ORDER BY count DESC LIMIT 20"
        ).fetchall()]

    # Active SSH sessions
    ssh_sessions = []
    try:
        r = subprocess.run(["w", "-h"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5:
                ssh_sessions.append({"user": parts[0], "tty": parts[1],
                                     "from": parts[2], "login": parts[3], "idle": parts[4]})
    except Exception:
        pass

    # Auth log (last 50 security-relevant lines)
    auth_lines = []
    try:
        r = subprocess.run(["sudo", "tail", "-n", "200", "/var/log/auth.log"],
                           capture_output=True, text=True, timeout=10)
        keywords = ("Failed password", "Accepted password", "Accepted publickey",
                    "Invalid user", "sudo", "session opened", "session closed", "FAILED")
        for line in r.stdout.splitlines():
            if any(k in line for k in keywords):
                auth_lines.append(line)
        auth_lines = auth_lines[-50:]
    except Exception:
        pass

    return jsonify({
        "ufw_status":     ufw_status,
        "ufw_rules":      ufw_rules,
        "f2b_jails":      f2b_jails,
        "login_attempts": attempts,
        "ssh_sessions":   ssh_sessions,
        "auth_log":       auth_lines,
    })


@bp.route("/api/security/ufw", methods=["POST"])
@login_required
def api_ufw_rule():
    data   = request.get_json(force=True)
    action = data.get("action", "").lower()
    rule   = data.get("rule", "").strip()
    if not rule or not re.match(r'^[\w/\s]+$', rule):
        return jsonify({"ok": False, "error": "invalid rule"}), 400
    if action == "allow":
        cmd = ["sudo", "ufw", "allow"] + rule.split()
    elif action == "deny":
        cmd = ["sudo", "ufw", "deny"] + rule.split()
    elif action == "delete":
        cmd = ["sudo", "ufw", "delete"] + rule.split()
    else:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return jsonify({"ok": r.returncode == 0,
                    "output": r.stdout.strip() or r.stderr.strip()})


@bp.route("/api/samba")
@login_required
def api_samba():
    clients = []
    shares  = []
    try:
        r = subprocess.run(["sudo", "smbstatus", "--shares"],
                           capture_output=True, text=True, timeout=10)
        in_section = False
        for line in r.stdout.splitlines():
            if line.startswith("Service") or line.startswith("-----"):
                in_section = True
                continue
            if in_section and line.strip():
                parts = line.split()
                if len(parts) >= 5:
                    clients.append({"service": parts[0], "pid": parts[1],
                                    "machine": parts[2],
                                    "connected_at": " ".join(parts[3:])})
    except Exception:
        pass

    try:
        subprocess.run(["testparm", "-s", "--section-name", "global"],
                       capture_output=True, text=True, timeout=5)
    except Exception:
        pass

    try:
        u = shutil.disk_usage(config.SHARED_ROOT)
        shares.append({
            "name":     "Shared",
            "path":     config.SHARED_ROOT,
            "total_gb": round(u.total / 1024**3, 1),
            "used_gb":  round(u.used  / 1024**3, 1),
            "free_gb":  round(u.free  / 1024**3, 1),
            "percent":  round(u.used / u.total * 100, 1),
        })
    except Exception:
        pass

    r2 = subprocess.run(["systemctl", "is-active", "smbd"],
                        capture_output=True, text=True, timeout=5)
    svc_active = r2.stdout.strip() == "active"
    net_io     = psutil.net_io_counters()

    return jsonify({
        "service_active":  svc_active,
        "clients":         clients,
        "shares":          shares,
        "net_bytes_sent":  net_io.bytes_sent,
        "net_bytes_recv":  net_io.bytes_recv,
    })
