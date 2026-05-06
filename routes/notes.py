from flask import Blueprint, jsonify, request
import db as _db
from utils import login_required

bp = Blueprint('notes', __name__)


@bp.route('/api/notes', methods=['GET'])
@login_required
def api_notes_list():
    return jsonify(_db.notes_list())


@bp.route('/api/notes/search', methods=['GET'])
@login_required
def api_notes_search():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    return jsonify(_db.notes_search(q))


@bp.route('/api/notes', methods=['POST'])
@login_required
def api_notes_create():
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    return jsonify(_db.notes_create(title, data.get('content', ''))), 201


@bp.route('/api/notes/<int:note_id>', methods=['GET'])
@login_required
def api_notes_get(note_id):
    note = _db.notes_get(note_id)
    if not note:
        return jsonify({'error': 'not found'}), 404
    return jsonify(note)


@bp.route('/api/notes/<int:note_id>', methods=['PUT'])
@login_required
def api_notes_update(note_id):
    data = request.get_json() or {}
    note = _db.notes_update(note_id, data.get('title'), data.get('content'))
    if not note:
        return jsonify({'error': 'not found'}), 404
    return jsonify(note)


@bp.route('/api/notes/<int:note_id>', methods=['DELETE'])
@login_required
def api_notes_delete(note_id):
    if not _db.notes_delete(note_id):
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})
