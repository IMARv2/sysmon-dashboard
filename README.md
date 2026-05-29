# 🖥️ SysMon: Real-Time Infrastructure Dashboard

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.x-black?style=flat-square&logo=flask&logoColor=white)
![Socket.IO](https://img.shields.io/badge/Socket.IO-Realtime-010101?style=flat-square&logo=socket.io&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-HTTPS-009639?style=flat-square&logo=nginx&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Integration-2496ED?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

> [!NOTE]
> **PURPOSE:** A self-hosted, real-time system monitoring dashboard built with Flask and Socket.IO — providing live visibility into server health, Docker containers, network nodes, and NAS storage from a single interface.

---

## 01 — 📖 Project Overview

**SysMon** is a full-stack homelab monitoring solution that aggregates metrics from multiple sources and pushes them to the browser in real time via WebSockets. It runs as a hardened HTTPS service behind nginx, with session-based authentication and a modular blueprint architecture.

The dashboard consolidates **CPU, RAM, disk, temperature, Docker containers, network node health, NAS stats, AI task monitoring, and a live file browser** — all in one responsive interface.

---

## 02 — ✨ Key Features

| Feature | Description |
| :--- | :--- |
| ⚡ **Real-Time Push** | Live metrics streamed over Socket.IO — no polling, no page refresh |
| 🐳 **Docker Integration** | Monitor container status, restart/stop containers directly from the UI |
| 🌐 **Multi-Node Awareness** | Ping and track multiple hosts on the local network |
| 🗄️ **NAS Stats** | Pulls pool usage and health data from TrueNAS via a dedicated collector |
| 🤖 **AI Task Monitor** | Tracks active Ollama inference tasks with timing and status |
| 📁 **File Browser** | Browse, download, and manage shared files via the web interface |
| 🔐 **Security Routes** | Active session management, login hardening, and audit logging |
| 📈 **Trading Dashboard** | Embedded view of trading bot metrics and open positions |
| 📣 **Telegram Alerts** | Push notifications for critical events via Telegram bot |

---

## 03 — 🧰 Tech Stack

| Layer | Technology |
| :--- | :--- |
| **Backend** | Python 3, Flask, Flask-SocketIO |
| **WSGI Server** | Gunicorn + Eventlet (workers=1) |
| **Frontend** | Jinja2 templates, vanilla JS, CSS |
| **Database** | SQLite (via `db.py`) |
| **Reverse Proxy** | nginx (HTTPS on :8080) |
| **Auth** | SHA-256 password hashing, session cookies |
| **Deployment** | systemd service (`dashboard.service`) |

---

## 04 — 📁 Project Structure

```
dashboard/
├── app.py                      # App factory, SocketIO init
├── config.py                   # Environment-driven config
├── background.py               # Background metric collection threads
├── state.py                    # Shared in-memory state
├── db.py                       # SQLite helpers
├── utils.py                    # Shared utilities
├── extensions.py               # Flask extensions (SocketIO, etc.)
├── routes/
│   ├── core.py                 # Auth, main page
│   ├── system.py               # CPU/RAM/disk metrics
│   ├── docker.py               # Container management
│   ├── network.py              # Node ping & status
│   ├── nodes.py                # Multi-node registry
│   ├── files.py                # File browser
│   ├── ai.py                   # AI task monitor
│   ├── trading.py              # Trading bot view
│   ├── security.py             # Session & audit routes
│   └── notes.py                # Notes/log viewer
├── templates/                  # Jinja2 HTML templates
├── static/                     # CSS, JS, assets
└── nas_stats_collector.py      # TrueNAS metric collector
```

---

## 05 — 🚀 Setup & Deployment

> [!IMPORTANT]
> **Requires:** Python 3.10+, nginx, and a valid SSL certificate for HTTPS.

**1. Clone & install dependencies:**
```bash
pip install -r requirements.txt
```

**2. Configure environment:**
```bash
cp .env.example .env
# Edit .env with your values
```

**3. Run with Gunicorn:**
```bash
bash start.sh
```

**4. Or deploy as systemd service:**
```bash
sudo cp dashboard.service /etc/systemd/system/
sudo systemctl enable --now dashboard
```

> [!TIP]
> Set `workers=1` in `gunicorn.conf.py` — Socket.IO requires a single worker with Eventlet to maintain shared WebSocket state across all connected clients.

---

## 06 — ⚙️ Environment Variables

| Variable | Description |
| :--- | :--- |
| `SECRET_KEY` | Flask session secret (use a strong random key) |
| `DASHBOARD_USER` | Login username |
| `DASHBOARD_PASS` | SHA-256 hashed password |
| `TELEGRAM_BOT_TOKEN` | Telegram bot for alerts |
| `TELEGRAM_CHAT_ID` | Target chat for notifications |
| `SERVER_IP` | Host IP for internal references |
| `HTTPS_ENABLED` | Enable HTTPS mode (`true`/`false`) |

> [!CAUTION]
> Never commit your `.env` file to version control. Use `.env.example` as the public template.
