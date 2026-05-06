# routes/trading.py — Trading bot management
import json
import os
import sqlite3
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

import state
from utils import login_required

bp = Blueprint("trading", __name__)

_BOT_DIR   = "/home/imar/trading-bot"
_ENV_FILE  = f"{_BOT_DIR}/.env"
_CONTAINER = "trading-bot"

_LOGS_DIR   = Path(_BOT_DIR) / "logs"
_BT_STATUS  = _LOGS_DIR / "backtest_status.json"
_BT_HISTORY = _LOGS_DIR / "backtest_history.json"
_BT_SAVED   = _LOGS_DIR / "backtest_saved.json"
_bt_lock    = threading.Lock()
_bt_running = False

_ALLOWED_CONFIG_KEYS = {
    "PAPER_TRADING", "TRADING_PAIR", "OLLAMA_MODEL", "LOOP_INTERVAL_S",
    "GRID_LEVELS", "GRID_ORDER_SIZE_USDT", "MAX_DRAWDOWN_PCT",
    "MAX_POSITION_PCT", "MAX_OPEN_POSITIONS", "PAPER_BALANCE_USDT",
    "TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "MIN_CONFIDENCE_THRESHOLD",
}


def _read_env(path: str = _ENV_FILE) -> dict:
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return result


def _write_env(env: dict, path: str = _ENV_FILE):
    with open(path, "w") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


def _get_db_path() -> str:
    env = _read_env()
    db_path = env.get("DB_PATH", "logs/trading.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(_BOT_DIR, db_path)
    return db_path


def _query(sql: str, params: tuple = (), one: bool = False):
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return None if one else []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        row = cur.fetchone() if one else cur.fetchall()
        conn.close()
        if one:
            return dict(row) if row else None
        return [dict(r) for r in row]
    except Exception:
        return None if one else []


# ── Endpoints ─────────────────────────────────────────────────────────────────

@bp.route("/api/trading/status")
@login_required
def trading_status():
    try:
        r = subprocess.run(
            ["sudo", "docker", "inspect", _CONTAINER],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return jsonify({"running": False, "status": "not_found",
                            "started_at": None, "config": _read_env()})
        data = json.loads(r.stdout)[0]["State"]
        return jsonify({
            "running":    data.get("Running", False),
            "status":     data.get("Status", "unknown"),
            "started_at": data.get("StartedAt"),
            "config":     _read_env(),
        })
    except Exception as e:
        return jsonify({"running": False, "status": "error",
                        "started_at": None, "config": {}, "error": str(e)})


@bp.route("/api/trading/summary")
@login_required
def trading_summary():
    row = _query(
        "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1", one=True
    ) or {}

    # Accurate trade count directly from trades table (snapshots may be stale)
    trade_count = _query("SELECT COUNT(*) as cnt FROM trades", one=True)
    if trade_count:
        row["total_trades"] = trade_count.get("cnt", 0)

    # Realized PnL from closed trades only
    realized = _query(
        "SELECT COALESCE(SUM(pnl),0) as total FROM trades "
        "WHERE side IN ('SELL','SHORT_CLOSE') AND pnl IS NOT NULL",
        one=True,
    )
    if realized:
        row["realized_pnl"] = realized.get("total", 0)

    # Active position from bot_state (JSON blob)
    pos_state = _query("SELECT value FROM bot_state WHERE key='active_position'", one=True)
    if pos_state and pos_state.get("value"):
        try:
            pos = json.loads(pos_state["value"])
            if pos:
                row["active_position"] = pos
        except Exception:
            pass

    # Live balance from bot_state (more current than last snapshot)
    bal_state = _query("SELECT value FROM bot_state WHERE key='balance_usdt'", one=True)
    if bal_state and bal_state.get("value"):
        try:
            row["balance_usdt"] = float(json.loads(bal_state["value"]))
        except Exception:
            pass

    return jsonify(row)


@bp.route("/api/trading/trades")
@login_required
def trading_trades():
    rows = _query("SELECT * FROM trades ORDER BY id DESC LIMIT 50")
    return jsonify({"trades": rows or []})


@bp.route("/api/trading/orders")
@login_required
def trading_orders():
    orders = []
    pos_state = _query("SELECT value FROM bot_state WHERE key='active_position'", one=True)
    if pos_state and pos_state.get("value"):
        try:
            pos = json.loads(pos_state["value"])
            if pos:
                env = _read_env()
                orders.append({
                    "order_id": pos.get("order_id"),
                    "side":     pos.get("side"),
                    "symbol":   env.get("TRADING_PAIR", "—"),
                    "price":    pos.get("entry_price"),
                    "qty":      pos.get("qty"),
                    "status":   "open",
                    "timestamp": pos.get("timestamp"),
                })
        except Exception:
            pass
    return jsonify({"orders": orders})


@bp.route("/api/trading/snapshots")
@login_required
def trading_snapshots():
    rows = _query(
        "SELECT balance_usdt, unrealized_pnl, timestamp "
        "FROM portfolio_snapshots ORDER BY id DESC LIMIT 288"
    )
    return jsonify({"snapshots": list(reversed(rows or []))})


@bp.route("/api/trading/action", methods=["POST"])
@login_required
def trading_action():
    data   = request.get_json(force=True) or {}
    action = data.get("action", "").lower()
    if action not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "error": "invalid action"}), 400
    r = subprocess.run(
        ["sudo", "docker", action, _CONTAINER],
        capture_output=True, text=True, timeout=30,
    )
    state.log_event("Trading bot", f"{action} — {'ok' if r.returncode == 0 else 'failed'}")
    return jsonify({
        "ok":     r.returncode == 0,
        "output": r.stdout.strip() or r.stderr.strip(),
    })


def _reset_paper_state(db_path: str):
    """Wipe paper-trading runtime state so a pair switch starts clean."""
    try:
        import sqlite3 as _sq
        initial_bal = _read_env().get("PAPER_BALANCE_USDT", "1000.0")
        conn = _sq.connect(db_path)
        conn.execute("UPDATE bot_state SET value=? WHERE key='balance_usdt'", (initial_bal,))
        conn.execute("UPDATE bot_state SET value='null' WHERE key='active_position'")
        conn.execute("UPDATE bot_state SET value='0'    WHERE key='order_counter'")
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM portfolio_snapshots")
        conn.commit()
        conn.close()
    except Exception:
        pass


@bp.route("/api/trading/config", methods=["POST"])
@login_required
def trading_config():
    data    = request.get_json(force=True) or {}
    old_env = _read_env()
    new_env = dict(old_env)
    for k, v in data.items():
        if k in _ALLOWED_CONFIG_KEYS:
            new_env[k] = str(v)

    pair_changed = new_env.get("TRADING_PAIR") != old_env.get("TRADING_PAIR")
    paper_mode   = old_env.get("PAPER_TRADING", "true").lower() == "true"

    try:
        _write_env(new_env)
        if pair_changed and paper_mode:
            _reset_paper_state(_get_db_path())
            state.log_event("Trading bot", f"pair changed → paper state reset")
        subprocess.run(
            ["sudo", "docker", "compose", "up", "-d", "--force-recreate", _CONTAINER],
            capture_output=True, timeout=60, cwd=_BOT_DIR,
        )
        state.log_event("Trading bot", "config updated + container recreated")
        return jsonify({"ok": True, "pair_reset": pair_changed and paper_mode})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Backtest ──────────────────────────────────────────────────────────────────

def _load_saved_ids() -> set:
    try:
        if _BT_SAVED.exists():
            return set(json.loads(_BT_SAVED.read_text()))
    except Exception:
        pass
    return set()


@bp.route("/api/trading/backtest", methods=["POST"])
@login_required
def trading_backtest_run():
    global _bt_running
    if not _bt_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Backtest already running"}), 409

    data      = request.get_json(force=True) or {}
    days      = int(data.get("days", 7))
    timeframe = str(data.get("timeframe", "1m"))
    rsi_buy   = float(data.get("rsi_buy", 35))
    rsi_sell  = float(data.get("rsi_sell", 65))

    extra_env = {}
    for key in ("TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "MIN_CONFIDENCE_THRESHOLD",
                "MAX_POSITION_PCT", "MAX_DRAWDOWN_PCT"):
        val = data.get(key)
        if val is not None:
            extra_env[key] = str(val)

    _bt_running = True
    try:
        _BT_STATUS.unlink(missing_ok=True)
    except Exception:
        pass

    def _run():
        global _bt_running
        try:
            cmd = ["sudo", "docker", "exec"]
            for k, v in extra_env.items():
                cmd += ["-e", f"{k}={v}"]
            cmd += [
                _CONTAINER, "python3", "/app/backtest.py",
                "--days",      str(days),
                "--timeframe", timeframe,
                "--rsi-buy",   str(rsi_buy),
                "--rsi-sell",  str(rsi_sell),
            ]
            subprocess.run(cmd, timeout=600, capture_output=True, text=True)
        except Exception:
            pass
        finally:
            _bt_running = False
            _bt_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@bp.route("/api/trading/backtest/status")
@login_required
def trading_backtest_status():
    try:
        if _BT_STATUS.exists():
            data = json.loads(_BT_STATUS.read_text())
            if _bt_running and data.get("status") != "running":
                data["status"] = "running"
            return jsonify(data)
    except Exception:
        pass
    if _bt_running:
        return jsonify({"status": "running", "progress": 0, "total": 1, "message": "Launching…"})
    return jsonify({"status": "idle"})


@bp.route("/api/trading/backtest/history")
@login_required
def trading_backtest_history():
    if not _BT_HISTORY.exists():
        return jsonify({"history": []})
    try:
        saved_ids = _load_saved_ids()
        history   = json.loads(_BT_HISTORY.read_text())
        for r in history:
            r["saved"] = r.get("timestamp", "") in saved_ids
        return jsonify({"history": history})
    except Exception:
        return jsonify({"history": []})


@bp.route("/api/trading/backtest/saved")
@login_required
def trading_backtest_saved():
    try:
        saved_ids = _load_saved_ids()
        if not _BT_HISTORY.exists():
            return jsonify({"saved": []})
        history = json.loads(_BT_HISTORY.read_text())
        saved   = [r for r in history if r.get("timestamp", "") in saved_ids]
        for r in saved:
            r["saved"] = True
        return jsonify({"saved": saved})
    except Exception:
        return jsonify({"saved": []})


@bp.route("/api/trading/backtest/save", methods=["POST"])
@login_required
def trading_backtest_toggle_save():
    data = request.get_json(force=True) or {}
    ts   = data.get("timestamp", "")
    if not ts:
        return jsonify({"ok": False, "error": "missing timestamp"}), 400
    saved_ids = _load_saved_ids()
    if ts in saved_ids:
        saved_ids.discard(ts)
        is_saved = False
    else:
        saved_ids.add(ts)
        is_saved = True
    _BT_SAVED.write_text(json.dumps(list(saved_ids), indent=2))
    return jsonify({"ok": True, "saved": is_saved})
