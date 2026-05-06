# utils.py — Shared helpers and auth decorator for all blueprints
import subprocess
import socket
import os
import re
import mimetypes
import datetime
import psutil
from functools import wraps

from flask import abort, jsonify, request, redirect, url_for, session
import config
import state


def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def get_system_info():
    cpu_freq = psutil.cpu_freq()
    mem  = psutil.virtual_memory()
    swap = psutil.swap_memory()
    boot = datetime.datetime.fromtimestamp(psutil.boot_time())
    up   = datetime.datetime.now() - boot
    h, r = divmod(int(up.total_seconds()), 3600)
    m, s = divmod(r, 60)
    parts = []
    for p in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(p.mountpoint)
            parts.append({
                "device": p.device, "mountpoint": p.mountpoint, "fstype": p.fstype,
                "total": round(u.total / 1024**3, 1),
                "used":  round(u.used  / 1024**3, 1),
                "free":  round(u.free  / 1024**3, 1),
                "percent": u.percent,
            })
        except Exception:
            continue
    return {
        "hostname":         socket.gethostname(),
        "os_pretty":        run("grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"'"),
        "kernel":           __import__('platform').release(),
        "cpu_model":        run("grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2").strip(),
        "cpu_cores":        psutil.cpu_count(logical=False),
        "cpu_threads":      psutil.cpu_count(logical=True),
        "cpu_percent":      psutil.cpu_percent(interval=0.5),
        "cpu_freq_current": round(cpu_freq.current, 0) if cpu_freq else 0,
        "cpu_freq_max":     round(cpu_freq.max, 0)     if cpu_freq else 0,
        "cpu_temp":         state.get_cpu_temp(),
        "mem_total":        round(mem.total     / 1024**3, 2),
        "mem_used":         round(mem.used      / 1024**3, 2),
        "mem_available":    round(mem.available / 1024**3, 2),
        "mem_percent":      mem.percent,
        "swap_total":       round(swap.total / 1024**3, 2),
        "swap_used":        round(swap.used  / 1024**3, 2),
        "swap_percent":     swap.percent,
        "uptime":           f"{h}h {m}m {s}s",
        "boot_time":        boot.strftime("%Y-%m-%d %H:%M:%S"),
        "disk_partitions":  parts,
    }


def get_network_info():
    interfaces = []
    for name, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(name)
        ipv4  = next((a.address for a in addrs if a.family == socket.AF_INET),  None)
        ipv6  = next((a.address for a in addrs if a.family == socket.AF_INET6), None)
        mac   = next((a.address for a in addrs if a.family == psutil.AF_LINK),  None)
        interfaces.append({
            "name": name, "ipv4": ipv4, "ipv6": ipv6, "mac": mac,
            "is_up": stats.isup  if stats else False,
            "speed": stats.speed if stats else 0,
        })
    io = psutil.net_io_counters()
    with state._devices_lock:
        devices = sorted(list(state._devices.values()),
                         key=lambda d: (not d["online"], d["ip"]))
    return {
        "interfaces":   interfaces,
        "bytes_sent":   round(io.bytes_sent / 1024**2, 2),
        "bytes_recv":   round(io.bytes_recv / 1024**2, 2),
        "packets_sent": io.packets_sent,
        "packets_recv": io.packets_recv,
        "devices":      devices,
        "scan_status":  state._scan_status.copy(),
    }


def crontab_list():
    raw  = run("crontab -l 2>/dev/null")
    jobs = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 5)
        if len(parts) >= 6:
            jobs.append({"schedule": " ".join(parts[:5]), "command": parts[5], "raw": stripped})
        elif len(parts) == 5:
            jobs.append({"schedule": " ".join(parts[:4]), "command": parts[4], "raw": stripped})
    return jobs


def crontab_add(schedule, command):
    current  = run("crontab -l 2>/dev/null")
    new_line = f"{schedule} {command}"
    updated  = (current.strip() + "\n" + new_line).strip() + "\n"
    proc = subprocess.run(["crontab", "-"], input=updated, text=True, capture_output=True)
    return proc.returncode == 0


def crontab_delete(raw_line):
    current = run("crontab -l 2>/dev/null")
    lines   = [l for l in current.splitlines() if l.strip() != raw_line.strip()]
    updated = "\n".join(lines).strip() + "\n"
    proc = subprocess.run(["crontab", "-"], input=updated, text=True, capture_output=True)
    return proc.returncode == 0


def _send_wol(mac):
    mac_clean = re.sub(r'[^0-9a-fA-F]', '', mac)
    if len(mac_clean) != 12:
        return False
    magic = bytes.fromhex('FF' * 6 + mac_clean * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(magic, ('<broadcast>', 9))
    return True


def safe_path(rel):
    full = os.path.realpath(os.path.join(config.SHARED_ROOT, rel.lstrip("/")))
    if not full.startswith(os.path.realpath(config.SHARED_ROOT)):
        abort(403)
    return full


def file_info(path, rel_base=""):
    name   = os.path.basename(path)
    is_dir = os.path.isdir(path)
    stat   = os.stat(path)
    rel    = os.path.join(rel_base, name).lstrip("/")
    mime, _ = mimetypes.guess_type(path)
    return {
        "name":     name,
        "path":     rel,
        "is_dir":   is_dir,
        "size":     0 if is_dir else stat.st_size,
        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "mime":     mime or ("inode/directory" if is_dir else "application/octet-stream"),
    }
