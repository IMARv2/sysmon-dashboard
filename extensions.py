# extensions.py — Shared Flask extensions (lazy init to avoid circular imports)
from flask_socketio import SocketIO

socketio = SocketIO()
