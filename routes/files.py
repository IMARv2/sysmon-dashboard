# routes/files.py — File manager (list, search, upload, download, mkdir, delete)
import os
import shutil

from flask import Blueprint, jsonify, request, send_file, abort
import config
from utils import login_required, safe_path, file_info

bp = Blueprint('files', __name__)


@bp.route("/api/files")
@login_required
def api_files_list():
    rel  = request.args.get("path", "")
    full = safe_path(rel)
    if not os.path.isdir(full):
        return jsonify({"error": "not a directory"}), 400
    entries = []
    try:
        for name in sorted(os.listdir(full),
                           key=lambda n: (not os.path.isdir(os.path.join(full, n)), n.lower())):
            if name.startswith("."):
                continue
            entries.append(file_info(os.path.join(full, name), rel))
    except PermissionError:
        return jsonify({"error": "permission denied"}), 403
    usage = shutil.disk_usage(config.SHARED_ROOT)
    return jsonify({"path": rel, "entries": entries,
                    "disk": {"total": usage.total, "used": usage.used, "free": usage.free}})


@bp.route("/api/files/search")
@login_required
def api_files_search():
    q = request.args.get("q", "").lower().strip()
    if not q:
        return jsonify({"results": []})
    results = []
    for root, dirs, files in os.walk(config.SHARED_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            if q in name.lower():
                full = os.path.join(root, name)
                rel  = os.path.relpath(full, config.SHARED_ROOT)
                results.append(file_info(full, os.path.dirname(rel)))
                if len(results) >= 100:
                    break
        if len(results) >= 100:
            break
    return jsonify({"results": results})


@bp.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    rel      = request.form.get("path", "")
    dest_dir = safe_path(rel)
    if not os.path.isdir(dest_dir):
        return jsonify({"ok": False, "error": "destination not found"}), 400
    saved = []
    for f in request.files.getlist("files"):
        safe_name = os.path.basename(f.filename)
        if not safe_name:
            continue
        dest = os.path.join(dest_dir, safe_name)
        f.save(dest)
        try:
            os.chown(dest, config.SHARED_UID, config.SHARED_GID)
        except Exception:
            pass
        saved.append(safe_name)
    return jsonify({"ok": True, "saved": saved})


@bp.route("/api/files/download")
@login_required
def api_files_download():
    rel  = request.args.get("path", "")
    full = safe_path(rel)
    if not os.path.isfile(full):
        abort(404)
    return send_file(full, as_attachment=True, download_name=os.path.basename(full))


@bp.route("/api/files/mkdir", methods=["POST"])
@login_required
def api_files_mkdir():
    data = request.get_json(force=True)
    rel  = data.get("path", "")
    name = os.path.basename(data.get("name", "").strip())
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    full = os.path.join(safe_path(rel), name)
    os.makedirs(full, exist_ok=True)
    try:
        os.chown(full, config.SHARED_UID, config.SHARED_GID)
    except Exception:
        pass
    return jsonify({"ok": True})


@bp.route("/api/files/delete", methods=["POST"])
@login_required
def api_files_delete():
    data = request.get_json(force=True)
    rel  = data.get("path", "")
    full = safe_path(rel)
    if full == os.path.realpath(config.SHARED_ROOT):
        return jsonify({"ok": False, "error": "cannot delete root"}), 400
    if os.path.isdir(full) and not data.get("confirm"):
        return jsonify({"ok": False, "error": "directory deletion requires confirm:true"}), 400
    if os.path.isdir(full):
        shutil.rmtree(full)
    elif os.path.isfile(full):
        os.remove(full)
    else:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})
