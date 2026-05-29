# routes/ai.py — AI task log and Qwen bridge
import json
import os
import datetime
import time as _time
import urllib.request as _ur

from flask import Blueprint, jsonify, request
from utils import login_required

bp = Blueprint('ai', __name__)

AI_TASK_LOG = os.environ.get("QWEN_TASK_LOG", "/home/imar/qwen-bridge/task_log.jsonl")
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


@bp.route("/api/ai/tasks")
@login_required
def api_ai_tasks():
    tasks = []
    try:
        with open(AI_TASK_LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    tasks.append(json.loads(line))
    except FileNotFoundError:
        pass
    tasks.reverse()
    return jsonify(tasks[:200])


@bp.route("/api/ai/models")
@login_required
def api_ai_models():
    try:
        req = _ur.Request(f"{_OLLAMA_HOST}/api/tags")
        with _ur.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        models = [{"name": m["name"], "size_gb": round(m["size"] / 1e9, 1),
                   "params": m["details"]["parameter_size"]}
                  for m in data.get("models", [])]
        return jsonify({"online": True, "models": models})
    except Exception:
        return jsonify({"online": False, "models": []})


@bp.route("/api/ai/ask", methods=["POST"])
@login_required
def api_ai_ask():
    body   = request.get_json(force=True)
    prompt = (body.get("prompt") or "").strip()
    model  = body.get("model", "qwen2.5:14b")
    system = body.get("system", "")
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    data = json.dumps(payload).encode()
    req  = _ur.Request(f"{_OLLAMA_HOST}/api/generate",
                       data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = _time.time()
    try:
        with _ur.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    elapsed       = round(_time.time() - t0, 2)
    response_text = result.get("response", "")
    entry = {
        "ts":             datetime.datetime.utcnow().isoformat() + "Z",
        "model":          model,
        "prompt_chars":   len(prompt),
        "response_chars": len(response_text),
        "elapsed_sec":    elapsed,
        "prompt_preview": prompt[:120].replace("\n", " "),
    }
    try:
        with open(AI_TASK_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
    return jsonify({"response": response_text, "elapsed_sec": elapsed, "model": model})
