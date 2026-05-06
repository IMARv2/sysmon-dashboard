"""db.py — SQLite helpers: init, login protection, device/event persistence."""
import os
import time
import sqlite3
import threading
import datetime
import logging

logger  = logging.getLogger(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'dashboard.db')

_db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    with _db_lock, get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS login_attempts (
            ip           TEXT PRIMARY KEY,
            count        INTEGER DEFAULT 0,
            locked_until REAL    DEFAULT 0
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS known_devices (
            mac          TEXT PRIMARY KEY,
            ip           TEXT,
            hostname     TEXT,
            label        TEXT,
            first_seen   REAL,
            last_seen    REAL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           REAL,
            level        TEXT,
            msg          TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)')
        conn.execute('''CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT    NOT NULL,
            content    TEXT    NOT NULL DEFAULT '',
            created_at REAL    NOT NULL,
            updated_at REAL    NOT NULL
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at)')
        conn.commit()

# ── Login protection ──────────────────────────────────────────────────────────
MAX_ATTEMPTS = 5
LOCKOUT_SECS = 3 * 3600

def check_login_allowed(ip):
    now = time.time()
    with _db_lock, get_db() as conn:
        row = conn.execute("SELECT count, locked_until FROM login_attempts WHERE ip=?", (ip,)).fetchone()
    if row and row["locked_until"] > now:
        return False, int(row["locked_until"] - now)
    return True, 0

def record_failure(ip):
    now = time.time()
    with _db_lock, get_db() as conn:
        conn.execute('''INSERT INTO login_attempts (ip, count, locked_until)
                        VALUES (?, 1, 0)
                        ON CONFLICT(ip) DO UPDATE SET
                          count = count + 1,
                          locked_until = CASE WHEN count + 1 >= ? THEN ? ELSE 0 END
                     ''', (ip, MAX_ATTEMPTS, now + LOCKOUT_SECS))
        conn.commit()

def clear_attempts(ip):
    with _db_lock, get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
        conn.commit()

def get_attempt_count(ip):
    with _db_lock, get_db() as conn:
        row = conn.execute("SELECT count FROM login_attempts WHERE ip=?", (ip,)).fetchone()
    return row["count"] if row else 0

# ── Event persistence ─────────────────────────────────────────────────────────
def persist_event(level, msg):
    try:
        cutoff = time.time() - 30 * 86400
        with _db_lock, get_db() as conn:
            conn.execute("INSERT INTO events (ts, level, msg) VALUES (?,?,?)",
                         (time.time(), level, msg))
            conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            conn.commit()
    except Exception:
        pass

def query_events(since_ts=0, limit=500):
    try:
        with _db_lock, get_db() as conn:
            rows = conn.execute(
                "SELECT ts, level, msg FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (since_ts, limit)
            ).fetchall()
        result = []
        for r in rows:
            dt = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            result.append({"ts": dt, "event": r["level"], "detail": r["msg"], "db": True})
        return result
    except Exception:
        return []

# ── Device persistence ────────────────────────────────────────────────────────
def load_devices_from_db():
    try:
        with _db_lock, get_db() as conn:
            return conn.execute("SELECT * FROM known_devices").fetchall()
    except Exception as e:
        logger.warning(f"[db] device load error: {e}")
        return []

def persist_devices(snapshot):
    try:
        with _db_lock, get_db() as conn:
            for d in snapshot:
                conn.execute('''INSERT INTO known_devices (mac,ip,hostname,label,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(mac) DO UPDATE SET
                        ip=excluded.ip, hostname=excluded.hostname,
                        last_seen=excluded.last_seen''',
                    (d.get("mac", "—"), d["ip"], d.get("hostname", ""),
                     d.get("label", ""), d.get("first_seen", ""), d.get("last_seen", "")))
            conn.commit()
    except Exception as e:
        logger.warning(f"[db] device persist error: {e}")

# ── Notes CRUD ────────────────────────────────────────────────────────────────
def _note_row(row):
    return {"id": row["id"], "title": row["title"], "content": row["content"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}

def notes_list():
    with _db_lock, get_db() as conn:
        rows = conn.execute(
            "SELECT id,title,content,created_at,updated_at FROM notes ORDER BY updated_at DESC"
        ).fetchall()
    return [_note_row(r) for r in rows]

def notes_create(title, content):
    now = time.time()
    with _db_lock, get_db() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title,content,created_at,updated_at) VALUES (?,?,?,?)",
            (title, content, now, now))
        conn.commit()
        row = conn.execute(
            "SELECT id,title,content,created_at,updated_at FROM notes WHERE id=?",
            (cur.lastrowid,)).fetchone()
    return _note_row(row)

def notes_get(note_id):
    with _db_lock, get_db() as conn:
        row = conn.execute(
            "SELECT id,title,content,created_at,updated_at FROM notes WHERE id=?",
            (note_id,)).fetchone()
    return _note_row(row) if row else None

def notes_update(note_id, title, content):
    now = time.time()
    with _db_lock, get_db() as conn:
        existing = conn.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone()
        if not existing:
            return None
        if title is not None and content is not None:
            conn.execute("UPDATE notes SET title=?,content=?,updated_at=? WHERE id=?",
                         (title, content, now, note_id))
        elif title is not None:
            conn.execute("UPDATE notes SET title=?,updated_at=? WHERE id=?", (title, now, note_id))
        elif content is not None:
            conn.execute("UPDATE notes SET content=?,updated_at=? WHERE id=?", (content, now, note_id))
        conn.commit()
        row = conn.execute(
            "SELECT id,title,content,created_at,updated_at FROM notes WHERE id=?",
            (note_id,)).fetchone()
    return _note_row(row)

def notes_delete(note_id):
    with _db_lock, get_db() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        conn.commit()
    return cur.rowcount > 0

def notes_search(q):
    pattern = f"%{q}%"
    with _db_lock, get_db() as conn:
        rows = conn.execute(
            "SELECT id,title,content,created_at,updated_at FROM notes "
            "WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC",
            (pattern, pattern)).fetchall()
    return [_note_row(r) for r in rows]
