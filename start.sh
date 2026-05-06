#!/bin/bash
cd /home/imar/dashboard

# Use gunicorn if available, fall back to Flask dev server
if command -v gunicorn &>/dev/null; then
    exec gunicorn -c gunicorn.conf.py app:app
else
    echo "[warn] gunicorn not found, using Flask dev server (install: pip install gunicorn)"
    exec python3 app.py
fi
