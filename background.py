"""background.py — Background threads: scanner, history collector, alert monitor."""
import subprocess
import threading
import datetime
import socket
import time
import logging
import json
import urllib.request
import psutil

import config
import state

logger = logging.getLogger(__name__)

# ── Utility ───────────────────────────────────────────────────────────────────
def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""

def mac_vendor(mac):
    if not mac or mac == "—":
        return ""
    return config.KNOWN_VENDORS.get(mac.lower()[:8], "")

# ── Network scanner ───────────────────────────────────────────────────────────
def nmap_scan():
    results = []
    try:
        raw = subprocess.check_output(
            ["sudo", "nmap", "-sn", "--host-timeout", "5s", config.SUBNET],
            text=True, stderr=subprocess.DEVNULL)
        cur = None
        for line in raw.splitlines():
            if line.startswith("Nmap scan report for"):
                if cur:
                    results.append(cur)
                parts = line.split()
                if "(" in line:
                    cur = {"ip": parts[-1].strip("()"), "mac": "—",
                           "hostname": parts[4], "vendor": ""}
                else:
                    cur = {"ip": parts[-1], "mac": "—", "hostname": "", "vendor": ""}
            elif "MAC Address:" in line and cur:
                parts = line.split(None, 3)
                cur["mac"] = parts[2].rstrip(",")
                if len(parts) > 3:
                    cur["vendor"] = parts[3].strip("()")
        if cur:
            results.append(cur)
    except Exception as e:
        logger.error(f"[scan] nmap error: {e}")
    return results

def do_scan():
    state._scan_status["scanning"] = True
    try:
        found     = nmap_scan()
        now       = datetime.datetime.now()
        found_ips = {d["ip"] for d in found}

        with state._devices_lock:
            for ip in state._devices:
                if ip not in found_ips:
                    missed = state._devices[ip].get("missed_scans", 0) + 1
                    state._devices[ip]["missed_scans"] = missed
                    if missed >= 2 and state._devices[ip]["online"]:
                        state.log_event("Device offline", f"{ip} went offline")
                        state._devices[ip]["online"] = False
                else:
                    state._devices[ip]["missed_scans"] = 0

            for d in found:
                ip = d["ip"]
                if ip == config.MY_IP:
                    d["hostname"] = d["hostname"] or socket.gethostname()
                ts     = now.strftime("%Y-%m-%d %H:%M:%S")
                vendor = d.get("vendor") or mac_vendor(d["mac"])
                if ip not in state._devices:
                    state._devices[ip] = {
                        "ip": ip, "mac": d["mac"],
                        "hostname": d["hostname"], "vendor": vendor,
                        "first_seen": ts, "last_seen": ts,
                        "online": True, "iface": "enp2s0",
                        "missed_scans": 0,
                    }
                    state.log_event("New device", f"{ip} ({vendor or d['hostname'] or 'unknown'})")
                else:
                    if not state._devices[ip]["online"]:
                        state.log_event("Device online", f"{ip} came back online")
                    state._devices[ip]["online"]       = True
                    state._devices[ip]["missed_scans"] = 0
                    state._devices[ip]["last_seen"]    = ts
                    if d["mac"] != "—":
                        state._devices[ip]["mac"]    = d["mac"]
                        state._devices[ip]["vendor"] = vendor
                    if d["hostname"]:
                        state._devices[ip]["hostname"] = d["hostname"]

        state._scan_status["last_scan"] = now.strftime("%Y-%m-%d %H:%M:%S")
        state.persist_devices_snapshot()
    except Exception as e:
        logger.error(f"[scanner] error: {e}")
    finally:
        state._scan_status["scanning"] = False

def background_scanner():
    while True:
        do_scan()
        time.sleep(config.SCAN_INTERVAL)

# ── History collector ─────────────────────────────────────────────────────────
_net_prev       = psutil.net_io_counters()
_net_prev_time  = time.time()
_disk_prev      = psutil.disk_io_counters()
_disk_prev_time = time.time()

def history_collector():
    global _net_prev, _net_prev_time, _disk_prev, _disk_prev_time
    while True:
        now  = datetime.datetime.now().strftime("%H:%M:%S")
        cpu  = psutil.cpu_percent(interval=1)
        ram  = psutil.virtual_memory().percent
        temp = state.get_cpu_temp()

        net_now  = psutil.net_io_counters()
        t_now    = time.time()
        dt       = max(t_now - _net_prev_time, 0.001)
        sent_kbs = round((net_now.bytes_sent - _net_prev.bytes_sent) / dt / 1024, 1)
        recv_kbs = round((net_now.bytes_recv - _net_prev.bytes_recv) / dt / 1024, 1)
        _net_prev      = net_now
        _net_prev_time = t_now

        disk_now   = psutil.disk_io_counters()
        dt2        = max(t_now - _disk_prev_time, 0.001)
        disk_r_kbs = round((disk_now.read_bytes  - _disk_prev.read_bytes)  / dt2 / 1024, 1)
        disk_w_kbs = round((disk_now.write_bytes - _disk_prev.write_bytes) / dt2 / 1024, 1)
        _disk_prev      = disk_now
        _disk_prev_time = t_now

        with state._history_lock:
            state._history["ts"].append(now)
            state._history["cpu"].append(cpu)
            state._history["ram"].append(ram)
            state._history["net_sent"].append(sent_kbs)
            state._history["net_recv"].append(recv_kbs)
            state._history["disk_read"].append(disk_r_kbs)
            state._history["disk_write"].append(disk_w_kbs)
            state._history["cpu_temp"].append(temp)

        time.sleep(5)

# ── Alert monitor ─────────────────────────────────────────────────────────────
_svc_was_active = {}

def alert_monitor():
    cpu_streak = 0
    while True:
        try:
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory().percent
            now = time.time()

            # Per-mount disk check
            for p in psutil.disk_partitions():
                if not p.fstype or p.mountpoint.startswith('/boot'):
                    continue
                try:
                    pct = psutil.disk_usage(p.mountpoint).percent
                    key = f"disk_{p.mountpoint}"
                    if pct > config.ALERT_THRESHOLDS["disk"]:
                        last = state._alert_last.get(key, 0)
                        if now - last > config.ALERT_COOLDOWN:
                            state._alert_last[key] = now
                            msg = f"Disk {p.mountpoint} at {pct:.0f}% (threshold {config.ALERT_THRESHOLDS['disk']}%)"
                            state.tg_send(f"⚠️ Server Alert — {msg}")
                            state.log_event("Alert: DISK threshold", msg)
                except Exception:
                    pass

            # CPU streak
            if cpu > config.ALERT_THRESHOLDS["cpu"]:
                cpu_streak += 1
            else:
                cpu_streak = 0

            checks = [
                ("cpu", cpu_streak >= 3, f"CPU at {cpu:.0f}% (threshold {config.ALERT_THRESHOLDS['cpu']}%)"),
                ("ram", ram > config.ALERT_THRESHOLDS["ram"], f"RAM at {ram:.0f}% (threshold {config.ALERT_THRESHOLDS['ram']}%)"),
            ]
            for metric, triggered, msg in checks:
                if triggered:
                    last = state._alert_last.get(metric, 0)
                    if now - last > config.ALERT_COOLDOWN:
                        state._alert_last[metric] = now
                        state.tg_send(f"⚠️ Server Alert — {msg}")
                        state.log_event(f"Alert: {metric.upper()} threshold", msg)

            # CPU recovery
            if cpu_streak == 0 and state._alert_last.get("cpu", 0) > 0:
                last_recovery = state._alert_last.get("cpu_recovery", 0)
                if now - last_recovery > config.ALERT_COOLDOWN and now - state._alert_last.get("cpu", 0) < 300:
                    state._alert_last["cpu_recovery"] = now
                    state.log_event("CPU recovered", f"CPU back to {cpu:.0f}%")

            # Temperature
            try:
                temps = psutil.sensors_temperatures()
                for name in ('coretemp', 'cpu_thermal', 'k10temp', 'acpitz'):
                    if name in temps and temps[name]:
                        temp_val = temps[name][0].current
                        if temp_val > config.ALERT_THRESHOLDS.get("temp", 80):
                            last = state._alert_last.get("temp", 0)
                            if now - last > config.ALERT_COOLDOWN:
                                state._alert_last["temp"] = now
                                msg = f"CPU temp {temp_val:.0f}°C (threshold {config.ALERT_THRESHOLDS.get('temp', 80)}°C)"
                                state.tg_send(f"🌡️ Temp Alert — {msg}")
                                state.log_event("Alert: TEMP threshold", msg)
                        break
            except Exception:
                pass

            # Service health (every ~60s)
            if int(now) % 60 < 15:
                for svc in config.MONITORED_SERVICES:
                    try:
                        r = subprocess.run(["systemctl", "is-active", svc],
                                           capture_output=True, text=True, timeout=5)
                        active = r.stdout.strip() == "active"
                        was = _svc_was_active.get(svc)
                        if was is None:
                            _svc_was_active[svc] = active
                        elif active != was:
                            _svc_was_active[svc] = active
                            if active:
                                state.log_event("Service recovered", f"{svc} is now active")
                            else:
                                state.tg_send(f"🚨 Service down: {svc}")
                                state.log_event("Service down", f"{svc} is not active")
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[alert] error: {e}")
        time.sleep(15)

# ── Qwen Node monitor ────────────────────────────────────────────────────────
_qwen_was_reachable = True
_qwen_alert_last    = {}

def qwen_node_monitor():
    global _qwen_was_reachable
    while True:
        try:
            with urllib.request.urlopen("http://10.22.11.11:9101/stats", timeout=5) as resp:
                stats     = json.loads(resp.read())
                reachable = True
        except Exception:
            if _qwen_was_reachable:
                logger.warning("[qwen-monitor] Qwen Node unreachable")
                state.log_event("Qwen Node offline", "10.22.11.11:9101 not reachable")
            _qwen_was_reachable = False
            time.sleep(60)
            continue

        if not _qwen_was_reachable:
            logger.info("[qwen-monitor] Qwen Node back online")
            state.log_event("Qwen Node online", "10.22.11.11 is reachable again")
        _qwen_was_reachable = True

        now = time.time()

        checks = [
            ("cpu",      stats["cpu_pct"],  config.ALERT_THRESHOLDS.get("cpu", 85),
             f"Qwen Node CPU at {stats['cpu_pct']:.0f}%"),
            ("ram",      stats["ram_pct"],  config.ALERT_THRESHOLDS.get("ram", 90),
             f"Qwen Node RAM at {stats['ram_pct']:.0f}%"),
        ]
        for key, val, threshold, msg in checks:
            if val > threshold:
                last = _qwen_alert_last.get(key, 0)
                if now - last > config.ALERT_COOLDOWN:
                    _qwen_alert_last[key] = now
                    state.tg_send(f"⚠️ Qwen Node Alert — {msg} (threshold {threshold}%)")
                    state.log_event(f"Alert: Qwen {key.upper()}", msg)

        gpu = stats.get("gpu")
        if gpu:
            vram_pct = gpu["mem_used_mb"] / gpu["mem_total_mb"] * 100 if gpu["mem_total_mb"] else 0
            gpu_checks = [
                ("gpu_temp", gpu["temp_c"],  85,
                 f"Qwen Node GPU temp {gpu['temp_c']}°C"),
                ("vram",     vram_pct,        90,
                 f"Qwen Node VRAM {vram_pct:.0f}% ({gpu['mem_used_mb']}/{gpu['mem_total_mb']} MB)"),
            ]
            for key, val, threshold, msg in gpu_checks:
                if val > threshold:
                    last = _qwen_alert_last.get(key, 0)
                    if now - last > config.ALERT_COOLDOWN:
                        _qwen_alert_last[key] = now
                        state.tg_send(f"🎮 Qwen Node Alert — {msg}")
                        state.log_event(f"Alert: Qwen {key.upper()}", msg)

        time.sleep(60)

# ── NAS Monitor ───────────────────────────────────────────────────────────────
_nas_was_reachable   = True
_nas_raid_alerted    = False   # True while we've already sent a degraded alert
_nas_offline_alerted = False

NAS_SSH_CMD = ["ssh", "-i", "/home/imar/.ssh/id_ed25519",
               "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
               "root@10.22.11.120", "cat /tmp/nas_stats.json"]

def _nas_tcp_reachable():
    for port in (445, 2049, 22):
        try:
            s = socket.create_connection(("10.22.11.120", port), timeout=3)
            s.close()
            return True
        except Exception:
            pass
    return False

def nas_monitor():
    global _nas_was_reachable, _nas_raid_alerted, _nas_offline_alerted
    while True:
        try:
            r = subprocess.run(NAS_SSH_CMD, capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                raise RuntimeError("SSH failed")
            data = json.loads(r.stdout)
            reachable = True
        except Exception:
            # Only alert offline if TCP ports are also unreachable
            if not _nas_tcp_reachable():
                if _nas_was_reachable:
                    logger.warning("[nas-monitor] NAS unreachable")
                    state.tg_send("🔴 NAS Offline — 10.22.11.120 is not reachable")
                    state.log_event("NAS offline", "10.22.11.120 not reachable")
                    _nas_offline_alerted = True
                _nas_was_reachable = False
            else:
                logger.warning("[nas-monitor] NAS SSH/stats failed but TCP ports reachable — skipping offline alert")
            time.sleep(120)
            continue

        if not _nas_was_reachable:
            logger.info("[nas-monitor] NAS back online")
            state.tg_send("✅ NAS Online — 10.22.11.120 is reachable again")
            state.log_event("NAS online", "10.22.11.120 is reachable again")
            _nas_offline_alerted = False
        _nas_was_reachable = True

        # RAID check
        for arr in data.get("raid", []):
            if arr.get("degraded"):
                if not _nas_raid_alerted:
                    bm  = arr.get("bitmask", "?")
                    msg = (f"⚠️ NAS RAID DEGRADED — {arr['name']} ({arr['level']}) "
                           f"[{bm}] {arr['active_drives']}/{arr['total_drives']} drives active")
                    state.tg_send(msg)
                    state.log_event("RAID Degraded", f"{arr['name']} bitmask=[{bm}]")
                    _nas_raid_alerted = True
            else:
                if _nas_raid_alerted:
                    state.tg_send(f"✅ NAS RAID Recovered — {arr['name']} is now healthy")
                    state.log_event("RAID Healthy", f"{arr['name']} recovered")
                    _nas_raid_alerted = False

        time.sleep(120)

# ── Supervised thread starter ─────────────────────────────────────────────────
def _supervise(target, name):
    """Restart target if it crashes, with 10s delay and logging."""
    while True:
        try:
            target()
        except Exception as e:
            logger.error(f"[supervisor] {name} crashed: {e}, restarting in 10s")
            time.sleep(10)

def start_background_threads():
    for target, name in (
        (background_scanner, "background_scanner"),
        (history_collector,  "history_collector"),
        (alert_monitor,      "alert_monitor"),
        (qwen_node_monitor,  "qwen_node_monitor"),
        (nas_monitor,        "nas_monitor"),
    ):
        threading.Thread(target=_supervise, args=(target, name),
                         daemon=True, name=f"supervisor-{name}").start()
        logger.info(f"Started supervised thread: {name}")
    logger.info("All background threads started")
