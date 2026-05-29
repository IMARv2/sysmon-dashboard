"""config.py — All configuration, constants, and environment loading."""
import os
import socket
import logging

# ── Logging ───────────────────────────────────────────────────────────────────
_BASE  = os.path.dirname(__file__)
_LOG_FILE = os.path.join(_BASE, 'dashboard.log')

from logging.handlers import RotatingFileHandler
_log_fmt = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
_fh = RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_fh.setFormatter(_log_fmt)
_ch = logging.StreamHandler()
_ch.setFormatter(_log_fmt)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(_fh)
logging.getLogger().addHandler(_ch)

logger = logging.getLogger(__name__)

# ── .env loader ───────────────────────────────────────────────────────────────
def _load_env(path):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_env(os.path.join(_BASE, '.env'))

# ── Server IP ─────────────────────────────────────────────────────────────────
def _detect_server_ip():
    env_ip = os.environ.get("SERVER_IP", "").strip()
    if env_ip:
        return env_ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

MY_IP = _detect_server_ip()
logger.info(f"Server IP detected as: {MY_IP}")

# ── App constants ──────────────────────────────────────────────────────────────
SECRET_KEY   = os.environ.get("SECRET_KEY", "change-me")
DASH_USER    = os.environ.get("DASHBOARD_USER", "admin")
DASH_PASS    = os.environ.get("DASHBOARD_PASS", "")
TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
HTTPS_ENABLED = os.environ.get("HTTPS_ENABLED", "").lower() in ("1", "true", "yes")

SHARED_ROOT    = "/srv/shared"
SUBNET         = os.environ.get("SCAN_SUBNET", "192.168.1.0/24")
HISTORY_POINTS = 8640     # ~12hr at 5s
SCAN_INTERVAL  = 300      # seconds
ALERT_COOLDOWN = 300      # seconds between repeated alerts

ALERT_THRESHOLDS = {"cpu": 85, "ram": 90, "disk": 90, "temp": 80}

SHARED_UID = int(os.environ.get("SHARED_FILE_UID", os.getuid()))
SHARED_GID = int(os.environ.get("SHARED_FILE_GID", os.getgid()))

_vendors_raw = os.environ.get("KNOWN_VENDORS_JSON", "")
try:
    import json as _json
    KNOWN_VENDORS = _json.loads(_vendors_raw) if _vendors_raw else {}
except Exception:
    KNOWN_VENDORS = {}

MONITORED_SERVICES = os.environ.get(
    "MONITORED_SERVICES", "dashboard,tailscaled"
).split(",")

if SECRET_KEY == "change-me":
    logger.warning("SECRET_KEY is set to the default value — set a strong key in .env")
