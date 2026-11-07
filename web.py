"""Sentinel public dashboard API with HTTPS and same-origin write safeguards."""

import os
import threading
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

from bot import TraderEngine, ChatEngine

try:
    from stats import compute_stats
except ImportError:
    compute_stats = None

app = Flask(__name__)
load_dotenv(override=True)
# Render terminates TLS at one trusted edge proxy. Do not expose this WSGI
# server directly to an untrusted network when relying on forwarded headers.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

_log_buffer = deque(maxlen=500)
_paper_setting = os.environ.get("ALPACA_PAPER_TRADE", "true").strip().lower()
_initial_mode = "PAPER" if _paper_setting == "true" else (
    "LIVE" if _paper_setting == "false" else "UNKNOWN")
_state = {
    "equity": 0.0, "buying_power": 0.0, "positions": [],
    "cycle": 0, "halted": False, "halt_reason": "",
    "mode": _initial_mode, "bars": {}, "trades": {},
    "running": False, "equity_curve": [], "account_ready": False,
    "account_updated_at": None, "bars_updated_at": {},
}
_lock = threading.Lock()
_engine_lock = threading.Lock()
_chat_lock = threading.Lock()
_engine = None
_chat = None


def _clean(obj):
    """Recursively convert objects into JSON-safe primitives."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        if isinstance(obj, float) and (obj != obj or abs(obj) == float("inf")):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_clean(x) for x in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


@app.before_request
def require_secure_same_origin():
    """Require HTTPS and block cross-origin writes; app access is intentionally public."""
    if request.endpoint == "healthz":
        return None

    if not request.is_secure:
        return jsonify({"error": "HTTPS is required."}), 400

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        source = request.headers.get("Origin") or request.headers.get("Referer")
        source_url = urlsplit(source or "")
        if (source_url.scheme != request.scheme
                or not source_url.netloc
                or source_url.netloc.lower() != request.host.lower()):
            return jsonify({"error": "Cross-origin request rejected."}), 403
    return None


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
    )
    return response


@app.route("/healthz")
def healthz():
    """Minimal unauthenticated liveness endpoint; exposes no account state."""
    return jsonify({"ok": True})


def _on_log(msg, level):
    with _lock:
        _log_buffer.append({
            "ts": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "msg": str(msg),
            "level": str(level),
        })


def _on_status(d):
    with _lock:
        for key in ("equity", "buying_power", "positions", "cycle",
                    "halted", "halt_reason", "mode", "equity_curve"):
            if key in d:
                _state[key] = _clean(d[key])
        if "equity" in d and "positions" in d:
            now = datetime.now(timezone.utc).isoformat()
            _state["account_ready"] = True
            _state["account_updated_at"] = now


def _on_bars(symbol, bars, trade):
    with _lock:
        _state["bars"][str(symbol)] = _clean(bars)
        _state["trades"][str(symbol)] = _clean(trade)
        _state["bars_updated_at"][str(symbol)] = datetime.now(timezone.utc).isoformat()


def _ensure_engine():
    global _engine, _chat
    with _engine_lock:
        if _engine is None:
            _engine = TraderEngine(on_log=_on_log, on_status=_on_status,
                                   on_bars=_on_bars)
        if _chat is None:
            _chat = ChatEngine(_engine)
        return _engine, _chat


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/favicon.ico")
def favicon():
    icon_path = os.path.join(app.root_path, "static", "favicon.svg")
    return send_file(icon_path, mimetype="image/svg+xml", conditional=True)


@app.route("/api/state")
def api_state():
    with _engine_lock:
        engine = _engine
    running = bool(engine and engine.is_running())
    with _lock:
        _state["running"] = running
        payload = {**_state, "log": list(_log_buffer)[-200:],
                   "server_time": datetime.now(timezone.utc).isoformat()}
    return jsonify(_clean(payload))


@app.route("/api/stats")
def api_stats():
    if compute_stats is None:
        return jsonify({"error": "Statistics are unavailable."}), 503
    try:
        path = os.environ.get("JOURNAL_PATH", "trades.db")
        stats = compute_stats(path)
        if "error" in stats:
            app.logger.error("Statistics query failed: %s", stats["error"])
            return jsonify({"error": "Statistics are temporarily unavailable."}), 503
        if stats.get("profit_factor") == float("inf"):
            stats["profit_factor"] = "inf"
        return jsonify(_clean(stats))
    except Exception:
        app.logger.exception("Statistics endpoint failed")
        return jsonify({"error": "Statistics are temporarily unavailable."}), 500


@app.route("/api/start", methods=["POST"])
def api_start_route():
    try:
        engine, _ = _ensure_engine()
        data = request.get_json(silent=True) or {}
        if not engine.cfg.get("paper", True) and data.get("confirm_live") != "START LIVE":
            return jsonify({"error": "Live mode requires the exact START LIVE acknowledgement."}), 400
        engine.start()
    except Exception:
        app.logger.exception("Trading engine start failed")
        return jsonify({"error": "Trading engine could not be started."}), 503
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
    try:
        engine, chat = _ensure_engine()
    except Exception:
        app.logger.exception("Trading engine initialization failed")
        return jsonify({"error": "Trading engine is not configured."}), 503
    data = request.get_json(silent=True) or {}
    msg = str(data.get("message") or "").strip()
    if not msg:
        return jsonify({"reply": "", "type": "text"})
    if len(msg) > 4000:
        return jsonify({"error": "Message is too long."}), 413

    try:
        with _chat_lock:
            result = chat.handle(msg)
    except Exception:
        app.logger.exception("Chat request failed")
        result = {"reply": "Chat request failed. Check server logs.",
                  "type": "error"}

    if not isinstance(result, dict):
        result = {"reply": str(result), "type": "text"}

    reply = result.get("reply", "")
    if not isinstance(reply, str):
        reply = str(_clean(reply))

    return jsonify({
        "reply": reply,
        "pending": _clean(result.get("pending")),
        "type": str(result.get("type", "text")),
    })


def _public_auto_start_enabled():
    return (
        os.environ.get("AUTO_START", "false").lower() == "true"
        and os.environ.get("ALLOW_PUBLIC_AUTO_START", "false").lower() == "true"
    )


def _deferred_start():
    import time
    time.sleep(3)
    try:
        engine, _ = _ensure_engine()
        engine.start()
        with _lock:
            _state["running"] = True
        app.logger.warning("AUTO_START started the trading engine")
    except Exception:
        app.logger.exception("AUTO_START failed")


if os.environ.get("AUTO_START", "false").lower() == "true":
    if _public_auto_start_enabled():
        threading.Thread(target=_deferred_start, daemon=True).start()
    else:
        app.logger.warning(
            "Ignoring AUTO_START in public-access mode; start manually from the dashboard"
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
