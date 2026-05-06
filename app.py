"""app.py — Application entry point: Flask setup, auth routes, SocketIO events."""
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, module='eventlet')
import eventlet
eventlet.monkey_patch()

import subprocess
import re
import threading
import logging
import datetime
import datetime as _dt

from flask import (Flask, jsonify, render_template, request,
                   session, redirect, url_for)
from flask_socketio import SocketIO
import psutil

import config
import db as _db
import state
import background
from utils import login_required, get_system_info, get_network_info

logger = logging.getLogger(__name__)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = _dt.timedelta(days=7)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024
if config.HTTPS_ENABLED:
    app.config['SESSION_COOKIE_SECURE'] = True

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet',
                    logger=False, engineio_logger=False)

# ── Register blueprints ───────────────────────────────────────────────────────
from routes.core     import bp as core_bp
from routes.network  import bp as network_bp
from routes.system   import bp as system_bp
from routes.files    import bp as files_bp
from routes.docker   import bp as docker_bp
from routes.security import bp as security_bp
from routes.db       import bp as db_bp
from routes.ai       import bp as ai_bp
from routes.nodes    import bp as nodes_bp
from routes.trading  import bp as trading_bp
from routes.notes    import bp as notes_bp

for bp in (core_bp, network_bp, system_bp, files_bp,
           docker_bp, security_bp, db_bp, ai_bp, nodes_bp, trading_bp, notes_bp):
    app.register_blueprint(bp)

# ── Init ──────────────────────────────────────────────────────────────────────
_db.db_init()
state.load_devices()
state.log_event("Dashboard started",
                f"Listening on 0.0.0.0:5000 — server IP {config.MY_IP}")
logger.info("Dashboard starting up")
background.start_background_threads()

# ── Auth & page routes ────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error     = None
    client_ip = request.remote_addr
    if request.method == "POST":
        allowed, wait_secs = _db.check_login_allowed(client_ip)
        if not allowed:
            mins  = wait_secs // 60
            error = f"Too many attempts. Try again in {mins} minutes."
        else:
            user = request.form.get("username", "").strip()
            pwd  = request.form.get("password", "")
            if user == config.DASH_USER and state.verify_password(pwd, config.DASH_PASS):
                _db.clear_attempts(client_ip)
                session.permanent    = True
                session["logged_in"] = True
                return redirect(url_for("index"))
            else:
                _db.record_failure(client_ip)
                allowed2, _ = _db.check_login_allowed(client_ip)
                if not allowed2:
                    error = "Too many failed attempts. Account locked for 3 hours."
                    state.log_event("Login locked",
                                    f"IP {client_ip} locked after {_db.MAX_ATTEMPTS} attempts")
                else:
                    remaining = _db.MAX_ATTEMPTS - _db.get_attempt_count(client_ip)
                    error = f"Invalid credentials. {remaining} attempt(s) remaining."
    return render_template("login.html", error=error, server_ip=config.MY_IP)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    return render_template("index.html", server_ip=config.MY_IP)


@app.route("/health")
def api_health():
    return jsonify({
        "status":     "ok",
        "timestamp":  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_sec": int((datetime.datetime.now() - state._dashboard_start).total_seconds()),
        "version":    "2.0",
    })

# ── SocketIO — Real-time push ─────────────────────────────────────────────────
_bg_started  = False
_bg_lock     = threading.Lock()
_log_streams = {}   # sid → subprocess


def _stats_emit_loop():
    """Emits full system stats to all clients every 5 s."""
    while True:
        eventlet.sleep(5)
        try:
            socketio.emit('stats', {
                "system":    get_system_info(),
                "network":   get_network_info(),
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            logger.error(f"[socketio] stats emit: {e}")


def _devices_emit_loop():
    """Emits device list to all clients every 10 s."""
    while True:
        eventlet.sleep(10)
        try:
            with state._devices_lock:
                devices = sorted(list(state._devices.values()),
                                 key=lambda d: (not d["online"], d["ip"]))
            socketio.emit('devices', {
                "devices":     devices,
                "scan_status": state._scan_status.copy(),
                "timestamp":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            logger.error(f"[socketio] devices emit: {e}")


@socketio.on('connect')
def on_ws_connect():
    if not session.get('logged_in'):
        return False
    global _bg_started
    with _bg_lock:
        if not _bg_started:
            _bg_started = True
            socketio.start_background_task(_stats_emit_loop)
            socketio.start_background_task(_devices_emit_loop)


@socketio.on('subscribe_logs')
def on_subscribe_logs(data):
    if not session.get('logged_in'):
        return
    name = (data or {}).get('name', '')
    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return
    sid = request.sid

    old = _log_streams.pop(sid, None)
    if old:
        try:
            old.terminate()
        except Exception:
            pass

    def stream_logs(container_name, client_sid):
        try:
            proc = subprocess.Popen(
                ['sudo', 'docker', 'logs', '-f', '--tail', '50',
                 '--timestamps', container_name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            _log_streams[client_sid] = proc
            for line in proc.stdout:
                if not line:
                    break
                socketio.emit('log_line', {'line': line.rstrip()}, to=client_sid)
                eventlet.sleep(0)
        except Exception as e:
            socketio.emit('log_line', {'line': f'[stream error] {e}'}, to=client_sid)
        finally:
            _log_streams.pop(client_sid, None)

    socketio.start_background_task(stream_logs, name, sid)


@socketio.on('unsubscribe_logs')
def on_unsubscribe_logs():
    sid  = request.sid
    proc = _log_streams.pop(sid, None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass


@socketio.on('disconnect')
def on_ws_disconnect():
    sid  = request.sid
    proc = _log_streams.pop(sid, None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False,
                 allow_unsafe_werkzeug=True)
