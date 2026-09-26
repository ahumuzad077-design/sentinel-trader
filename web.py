"""Second Brain Trader - web server."""
import os, threading
from collections import deque
from datetime import datetime
from flask import Flask, render_template, jsonify
from bot import TraderEngine

app = Flask(__name__)
_log_buffer = deque(maxlen=500)
_state = {"equity": 0.0, "buying_power": 0.0, "positions": [], "cycle": 0,
          "halted": False, "halt_reason": "", "mode": "PAPER", "bars": {},
          "trades": {}, "running": False, "equity_curve": []}
_lock = threading.Lock()
_engine = None


def _on_log(msg, level):
    with _lock:
        _log_buffer.append({"ts": datetime.now().strftime("%H:%M:%S"),
                             "msg": msg, "level": level})


def _on_status(d):
    with _lock:
        for k in ("equity", "buying_power", "positions", "cycle", "halted",
                  "halt_reason", "mode", "equity_curve"):
            if k in d: _state[k] = d[k]


def _on_bars(symbol, bars, trade):
    with _lock:
        _state["bars"][symbol] = bars
        _state["trades"][symbol] = trade


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/state")
def api_state():
    with _lock:
        return jsonify({**_state, "log": list(_log_buffer)[-200:]})


@app.route("/api/start", methods=["POST"])
def api_start():
    global _engine
    if _engine is None or not _engine.is_running():
        _engine = TraderEngine(on_log=_on_log, on_status=_on_status,
                                on_bars=_on_bars)
        _engine.start()
        with _lock: _state["running"] = True
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _engine
    if _engine: _engine.stop()
    with _lock: _state["running"] = False
    return jsonify({"ok": True})


if os.environ.get("AUTO_START", "false").lower() == "true":
    threading.Timer(3.0, lambda: api_start()).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
