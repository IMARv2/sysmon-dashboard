import os
import re
import time
import json
import glob
import subprocess


def get_cpu_pct():
    def read_stat():
        with open('/proc/stat') as f:
            line = f.readline()
        vals = list(map(int, line.split()[1:]))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle
    t1, i1 = read_stat()
    time.sleep(0.2)
    t2, i2 = read_stat()
    diff_total = t2 - t1
    diff_idle  = i2 - i1
    if diff_total == 0:
        return 0.0
    return round((1.0 - diff_idle / diff_total) * 100, 1)


def get_cpu_temp():
    for path in glob.glob('/sys/class/thermal/thermal_zone*/temp'):
        try:
            with open(path) as f:
                val = int(f.read().strip())
                if val > 0:
                    return round(val / 1000.0, 1)
        except Exception:
            pass
    return None


def get_memory():
    info = {}
    with open('/proc/meminfo') as f:
        for line in f:
            parts = line.split()
            if parts:
                info[parts[0].rstrip(':')] = int(parts[1])
    total_kb   = info.get('MemTotal', 0)
    free_kb    = info.get('MemFree', 0)
    buffers_kb = info.get('Buffers', 0)
    cached_kb  = info.get('Cached', 0) + info.get('SReclaimable', 0)
    used_kb    = max(total_kb - free_kb - buffers_kb - cached_kb, 0)
    total_gb   = round(total_kb / 1024 / 1024, 2)
    used_gb    = round(used_kb  / 1024 / 1024, 2)
    pct        = round(used_gb / total_gb * 100, 1) if total_gb else 0
    return {'pct': pct, 'used_gb': used_gb, 'total_gb': total_gb}


def get_disks():
    seen  = set()
    disks = []
    candidates = ['/mnt']
    for base in ['/mnt']:
        try:
            for entry in os.listdir(base):
                full = os.path.join(base, entry)
                if os.path.isdir(full) and not entry.startswith('.'):
                    candidates.append(full)
                    for sub in os.listdir(full):
                        sfull = os.path.join(full, sub)
                        if os.path.isdir(sfull) and not sub.startswith('.'):
                            candidates.append(sfull)
        except Exception:
            pass
    for mount in candidates:
        try:
            st       = os.statvfs(mount)
            total_gb = round(st.f_blocks * st.f_frsize / 1024**3, 1)
            if total_gb < 1:
                continue
            key = (st.f_blocks, st.f_bsize, st.f_fsid)
            if key in seen:
                continue
            seen.add(key)
            used_gb = round((st.f_blocks - st.f_bfree) * st.f_frsize / 1024**3, 1)
            pct     = round(used_gb / total_gb * 100, 1) if total_gb else 0
            disks.append({'mount': mount, 'total_gb': total_gb, 'used_gb': used_gb, 'pct': pct})
        except Exception:
            pass
    return disks


def get_zfs_pools():
    pools = []
    try:
        r = subprocess.run(['zpool', 'status', '-P'], capture_output=True, text=True, timeout=10)
        current_pool = None
        health = None
        errors = 0
        for line in r.stdout.splitlines():
            m = re.match(r'\s*pool:\s+(\S+)', line)
            if m:
                if current_pool and current_pool != 'boot-pool':
                    pools.append({'name': current_pool, 'health': health, 'errors': errors})
                current_pool = m.group(1)
                health = None
                errors = 0
            m2 = re.match(r'\s*state:\s+(\S+)', line)
            if m2:
                health = m2.group(1)
            if 'DEGRADED' in line or 'FAULTED' in line or 'OFFLINE' in line:
                errors += 1
        if current_pool and current_pool != 'boot-pool':
            pools.append({'name': current_pool, 'health': health, 'errors': errors})
    except Exception:
        pass
    return pools


def get_samba_active():
    try:
        r = subprocess.run(['systemctl', 'is-active', 'smbd'], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == 'active'
    except Exception:
        pass
    for proc in os.listdir('/proc'):
        if not proc.isdigit():
            continue
        try:
            with open(f'/proc/{proc}/comm') as f:
                if f.read().strip() == 'smbd':
                    return True
        except Exception:
            pass
    return False


def main():
    pools = get_zfs_pools()
    # Build raid-compatible structure from ZFS pools for dashboard compatibility
    raid = []
    for p in pools:
        degraded = p['health'] != 'ONLINE'
        raid.append({
            'name':          p['name'],
            'level':         'zfs',
            'bitmask':       'OK' if not degraded else 'DEGRADED',
            'active_drives': 0 if degraded else 3,
            'total_drives':  3,
            'degraded':      degraded,
        })

    stats = {
        'cpu_pct':      get_cpu_pct(),
        'cpu_temp_c':   get_cpu_temp(),
        'memory':       get_memory(),
        'disks':        get_disks(),
        'raid':         raid,
        'samba_active': get_samba_active(),
        'timestamp':    int(time.time()),
    }
    tmp = '/tmp/nas_stats.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(stats, f)
    os.rename(tmp, '/tmp/nas_stats.json')


if __name__ == '__main__':
    main()
