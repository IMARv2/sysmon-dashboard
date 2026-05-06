# routes/db.py — SQLite database viewer
import re
import os
import csv
import io

from flask import Blueprint, jsonify, request, Response, abort
import db as _db
from utils import login_required

bp = Blueprint('db', __name__)


@bp.route("/api/dbview/tables")
@login_required
def api_db_tables():
    try:
        with _db._db_lock, _db.get_db() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
            info = {}
            for t in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                cols  = [c[1] for c in conn.execute(f"PRAGMA table_info([{t}])").fetchall()]
                info[t] = {"count": count, "columns": cols}
        return jsonify({"tables": info, "db_path": _db.DB_PATH,
                        "db_size_kb": round(os.path.getsize(_db.DB_PATH) / 1024, 1)})
    except Exception as e:
        return jsonify({"error": str(e)})


@bp.route("/api/dbview/query", methods=["POST"])
@login_required
def api_db_query():
    data  = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not re.match(r'^\s*SELECT\b', query, re.IGNORECASE):
        return jsonify({"ok": False, "error": "only SELECT queries allowed"}), 400
    forbidden = re.compile(
        r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|ATTACH|DETACH|PRAGMA\s+(?!table_info))\b', re.I)
    if forbidden.search(query):
        return jsonify({"ok": False, "error": "forbidden keyword in query"}), 400
    try:
        with _db._db_lock, _db.get_db() as conn:
            cur  = conn.execute(query)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchmany(500)]
        return jsonify({"ok": True, "columns": cols, "rows": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/dbview/export/<table>")
@login_required
def api_db_export(table):
    if not re.match(r'^[a-zA-Z0-9_]+$', table):
        abort(400)
    try:
        with _db._db_lock, _db.get_db() as conn:
            cur  = conn.execute(f"SELECT * FROM [{table}]")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        buf = io.StringIO()
        w   = csv.writer(buf)
        w.writerow(cols)
        w.writerows(rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})
    except Exception:
        abort(500)
