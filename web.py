"""Sentinel - web server."""

import os
import threading
from collections import deque
from datetime import datetime

from flask import Flask, render_template, jsonify, request

from bot import TraderEngine, ChatEngine

app = Flask(__name__)

_log_buffer = deque(maxlen=500)
_state = {
    "equity": 0.0, "buying_power": 0.0, "positions": [],
    "cycle": 0, "halted": False, "halt_reason": "",
    "mode": "PAPER", "bars": {}, "trades": {},
    "running": False, "equity_curve": [],
}
_lock = threading.Lock()
_engine = None
_chat = None


def _on_log(msg, level):
    with _lock:
        _log_buffer.append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "msg": msg, "level": level})


def _on_status(d):
    with _lock:
        for k in ("equity", "buying_power", "positions", "cycle",
                  "halted", "halt_reason", "mode", "equity_curve"):
            if k in d:
                _state[k] = d[k]


def _on_bars(symbol, bars, trade):
    with _lock:
        _state["bars"][symbol] = bars
        _state["trades"][symbol] = trade


def _ensure_engine():
    """Create the engine + chat if they don't exist yet."""
    global _engine, _chat
    if _engine is None:
        _engine = TraderEngine(
            on_log=_on_log, on_status=_on_status, on_bars=_on_bars)
    if _chat is None:
        _chat = ChatEngine(_engine)
    return _engine, _chat


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/state")
def api_state():
    with _lock:
        return jsonify({**_state, "log": list(_log_buffer)[-200:]})


@app.route("/api/start", methods=["POST"])
def api_start_route():
    engine, _ = _ensure_engine()
    engine.start()
    with _lock:
        _state["running"] = True
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _engine
    if _engine:
        _engine.stop()
    with _lock:
        _state["running"] = False
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    engine, chat = _ensure_engine()
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"reply": "", "type": "text"})

    try:
        result = chat.handle(msg)
    except Exception as e:
        import traceback
        traceback.print_exc()
        result = {"reply": f"Chat error: {e}", "type": "error"}

    return jsonify(result)


def _deferred_start():
    import time as _t
    _t.sleep(3)
    try:
        engine, _ = _ensure_engine()
        engine.start()
        with _lock:
            _state["running"] = True
        print("[AUTO_START] engine started", flush=True)
    except Exception as e:
        print(f"[AUTO_START] failed: {e}", flush=True)


if os.environ.get("AUTO_START", "false").lower() == "true":
    threading.Thread(target=_deferred_start, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)