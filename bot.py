"""Sentinel - autonomous multi-asset trading engine.

Supports stocks, ETFs, and crypto. Multi-agent LLM decision (bull / bear /
judge debate), layered memory, deterministic risk engine.
"""

import os, re, json, time, sqlite3, threading
from collections import deque
from datetime import datetime, timedelta

import numpy as np
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import (StockHistoricalDataClient,
                                     CryptoHistoricalDataClient)
from alpaca.data.requests import (StockBarsRequest, StockLatestQuoteRequest,
                                   CryptoBarsRequest, CryptoLatestQuoteRequest)
from alpaca.data.timeframe import TimeFrame

from openai import OpenAI


# ============================================================
# LAYERED MEMORY
# ============================================================

class LayeredMemory:
    def __init__(self, short=20, mid=100, long=500):
        self.short = deque(maxlen=short)
        self.mid = deque(maxlen=mid)
        self.long = deque(maxlen=long)
        self.episodic = []

    def push_tick(self, tick):
        self.short.append(tick)
        self.mid.append(tick)
        self.long.append(tick)

    def summarize(self):
        if not self.short:
            return "No market data yet."
        p = [t["price"] for t in self.short]
        ret = (p[-1] / p[0] - 1) * 100 if len(p) > 1 else 0.0
        v = [t.get("volume", 0) for t in self.short]
        return f"Recent {len(p)} ticks: move {ret:+.2f}%. Last {p[-1]:.4f}. Avg vol {np.mean(v):.0f}."


# ============================================================
# RISK ENGINE
# ============================================================

class RiskEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.start_of_day_equity = None
        self.peak_equity = None
        self.consecutive_losses = 0
        self.halted = False
        self.halt_reason = ""

    def update_equity(self, e):
        if self.start_of_day_equity is None:
            self.start_of_day_equity = e
        if self.peak_equity is None or e > self.peak_equity:
            self.peak_equity = e

    def full_report(self, account, positions, decision, entry, atr, is_crypto):
        eq = account["equity"]
        self.update_equity(eq)
        r = {"approved": False, "reason": "", "size_pct": 0.0, "qty": 0,
             "stop_loss": 0.0, "take_profit": 0.0, "risk_amount": 0.0,
             "reward_amount": 0.0, "position_value": 0.0, "checks": []}

        # ---- HARD RISK CHECKS ----
        if self.halted:
            r["reason"] = f"HALTED: {self.halt_reason}"
            r["checks"].append(("Global halt", False, self.halt_reason))
            return r
        r["checks"].append(("Global halt", True, "clear"))

        if self.start_of_day_equity:
            dd = (self.start_of_day_equity - eq) / self.start_of_day_equity
            ok = dd < self.cfg["max_daily_loss_pct"]
            r["checks"].append(("Daily drawdown", ok, f"{dd*100:.2f}%"))
            if not ok:
                self.halted = True
                self.halt_reason = f"Daily DD {dd*100:.2f}%"
                r["reason"] = self.halt_reason
                return r
        else:
            r["checks"].append(("Daily drawdown", True, "0.00%"))

        if self.peak_equity:
            dd = (self.peak_equity - eq) / self.peak_equity
            ok = dd < self.cfg["max_total_drawdown_pct"]
            r["checks"].append(("Total drawdown", ok, f"{dd*100:.2f}%"))
            if not ok:
                self.halted = True
                self.halt_reason = f"Total DD {dd*100:.2f}%"
                r["reason"] = self.halt_reason
                return r
        else:
            r["checks"].append(("Total drawdown", True, "0.00%"))

        ok = len(positions) < self.cfg["max_concurrent_positions"]
        r["checks"].append(("Concurrent positions", ok,
            f"{len(positions)}/{self.cfg['max_concurrent_positions']}"))
        if not ok:
            r["reason"] = "Max concurrent positions"
            return r

        if is_crypto:
            cc = sum(1 for p in positions if "/" in p["symbol"])
            ok = cc < self.cfg["max_crypto_positions"]
            r["checks"].append(("Crypto positions", ok,
                f"{cc}/{self.cfg['max_crypto_positions']}"))
            if not ok:
                r["reason"] = "Max crypto positions"
                return r

        if decision["action"] == "HOLD":
            r["reason"] = "LLM says HOLD"
            r["checks"].append(("LLM verdict", False, "HOLD"))
            return r
        r["checks"].append(("LLM verdict", True, decision["action"]))

        conf = decision.get("confidence", 0)
        ok = conf >= self.cfg["min_confidence"]
        r["checks"].append(("Confidence", ok, f"{conf:.2f}"))
        if not ok:
            r["reason"] = f"Low confidence {conf:.2f}"
            return r

        # ---- SIZING ----
        sp = min(self.cfg["max_position_pct"],
                 self.cfg["max_position_pct"] * conf)
        notional = eq * sp

        if is_crypto:
            qty = round(max(0.0001, notional / entry), 6)
        else:
            qty = max(1, int(notional / entry))

        # ---- ATR BRACKET ----
        d = atr * self.cfg["atr_stop_multiplier"]
        rr = self.cfg["risk_reward_ratio"]
        dec = 4 if is_crypto else 2
        if decision["action"] == "BUY":
            sl = round(entry - d, dec)
            tp = round(entry + d * rr, dec)
        else:
            sl = round(entry + d, dec)
            tp = round(entry - d * rr, dec)

        ra = abs(entry - sl) * qty
        wa = abs(tp - entry) * qty
        rra = wa / max(ra, 0.01)

        r.update({"approved": True, "reason": "approved", "size_pct": sp,
                  "qty": qty, "stop_loss": sl, "take_profit": tp,
                  "risk_amount": ra, "reward_amount": wa,
                  "position_value": qty * entry})
        r["checks"].append(("Position size", True, f"{sp*100:.2f}%"))
        r["checks"].append(("Risk/reward", rra >= rr - 0.05, f"{rra:.2f}:1"))
        return r


# ============================================================
# CONFIG
# ============================================================

def load_config():
    load_dotenv(override=True)

    def get(k, d=None, req=False):
        v = os.environ.get(k, d)
        if req and not v:
            raise ValueError(f"Missing env var: {k}")
        return v

    gf = [m.strip() for m in
          get("GROQ_FALLBACKS", "openai/gpt-oss-20b").split(",") if m.strip()]
    nf = [m.strip() for m in
          get("NIM_FALLBACKS",
              "deepseek-ai/deepseek-v4.1-flash,"
              "google/gemma-4-31b-it").split(",") if m.strip()]

    return {
        "alpaca_key": get("ALPACA_API_KEY", req=True),
        "alpaca_secret": get("ALPACA_SECRET_KEY", req=True),
        "groq_key": get("GROQ_API_KEY", req=True),
        "nvidia_key": get("NVIDIA_API_KEY", ""),
        "paper": get("ALPACA_PAPER_TRADE", "true").lower() == "true",
        "model_judge": get("LLM_MODEL_JUDGE", "openai/gpt-oss-120b"),
        "model_debate": get("LLM_MODEL_DEBATE", "openai/gpt-oss-20b"),
        "model_research": get("LLM_MODEL_RESEARCH", "openai/gpt-oss-120b"),
        "groq_fallbacks": gf,
        "nim_fallbacks": nf,
        "symbols": [s.strip().upper() for s in
                    get("SYMBOLS", "AAPL,MSFT,NVDA,SPY").split(",") if s.strip()],
        "crypto_symbols": [s.strip().upper() for s in
                           get("CRYPTO_SYMBOLS", "BTC/USD,ETH/USD").split(",") if s.strip()],
        "cycle_seconds": int(get("CYCLE_SECONDS", "600")),
        "research_every_n": int(get("RESEARCH_EVERY_N", "20")),
        "screen_top_stocks": int(get("SCREEN_TOP_STOCKS", "15")),
        "screen_top_crypto": int(get("SCREEN_TOP_CRYPTO", "5")),
        "max_position_pct": float(get("MAX_POSITION_PCT", "0.05")),
        "max_daily_loss_pct": float(get("MAX_DAILY_LOSS_PCT", "0.02")),
        "max_total_drawdown_pct": float(get("MAX_TOTAL_DRAWDOWN_PCT", "0.06")),
        "max_concurrent_positions": int(get("MAX_CONCURRENT_POSITIONS", "10")),
        "max_crypto_positions": int(get("MAX_CRYPTO_POSITIONS", "5")),
        "risk_reward_ratio": float(get("RISK_REWARD_RATIO", "2.0")),
        "atr_stop_multiplier": float(get("ATR_STOP_MULTIPLIER", "1.5")),
        "min_confidence": float(get("MIN_CONFIDENCE", "0.55")),
        "enable_browser": get("ENABLE_BROWSER", "true").lower() == "true",
        "journal_path": get("JOURNAL_PATH", "trades.db"),
    }


# ============================================================
# JOURNAL
# ============================================================

class TradeJournal:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, symbol TEXT, action TEXT, confidence REAL,
                reason TEXT, bull TEXT, bear TEXT, approved INTEGER,
                qty REAL, entry REAL, stop_loss REAL,
                take_profit REAL, order_id TEXT)""")
            c.commit()

    def log(self, **kw):
        with self._lock, sqlite3.connect(self.path) as c:
            c.execute("""INSERT INTO decisions
                (ts,symbol,action,confidence,reason,bull,bear,approved,
                 qty,entry,stop_loss,take_profit,order_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                datetime.utcnow().isoformat(),
                kw.get("symbol",""), kw.get("action",""),
                kw.get("confidence",0.0), kw.get("reason",""),
                kw.get("bull",""), kw.get("bear",""),
                1 if kw.get("approved") else 0,
                kw.get("qty",0), kw.get("entry",0.0),
                kw.get("stop_loss",0.0), kw.get("take_profit",0.0),
                kw.get("order_id","")))
            c.commit()


# ============================================================
# SENTINEL ENGINE
# ============================================================

class SentinelEngine:
    SYS_BULL = (
        "You are a bullish trading analyst. Argue concisely WHY this trade "
        "should be taken. Focus on momentum, catalysts, and upside potential. "
        "Max 100 words."
    )

    SYS_BEAR = (
        "You are a bearish risk analyst. Argue concisely WHY this trade "
        "should NOT be taken. Focus on downside risk, bad entry points, and "
        "recent losses. Max 100 words."
    )

    SYS_JUDGE = (
        'You are the portfolio risk manager for Sentinel. You see bull and '
        'bear arguments plus hard risk constraints. '
        'Your ONLY job is to decide direction and confidence. '
        'DO NOT calculate position sizes, dollar amounts, or share quantities '
        '- the risk engine handles all sizing automatically. '
        'DO NOT reject trades for "position too large" - that is not your role. '
        'Reject only if the market setup itself is bad. '
        'If the bull case is stronger than the bear case, output BUY. '
        'If the bear case is stronger, output SELL or HOLD. '
        'Only output HOLD if the cases are truly balanced. '
        'Output ONLY valid JSON: '
        '{"action": "BUY"|"SELL"|"HOLD", "confidence": 0-1, "reason": "..."}'
    )

    SYS_RESEARCH = (
        "You are a market research analyst with live web access. Find the "
        "2-3 most important recent news items or catalysts for this asset. "
        "Max 80 words."
    )

    def __init__(self, on_log=None, on_status=None, on_bars=None):
        self.on_log = on_log or (lambda m, l="info": None)
        self.on_status = on_status or (lambda d: None)
        self.on_bars = on_bars or (lambda s, b, t: None)

        self.cfg = load_config()
        self.trade = TradingClient(self.cfg["alpaca_key"],
            self.cfg["alpaca_secret"], paper=self.cfg["paper"])
        self.data = StockHistoricalDataClient(self.cfg["alpaca_key"],
            self.cfg["alpaca_secret"])
        self.crypto_data = CryptoHistoricalDataClient()

        self.llm = OpenAI(api_key=self.cfg["groq_key"],
                          base_url="https://api.groq.com/openai/v1")
        self.llm_nim = None
        if self.cfg["nvidia_key"]:
            self.llm_nim = OpenAI(api_key=self.cfg["nvidia_key"],
                base_url="https://integrate.api.nvidia.com/v1")

        self.memory = {}
        self.risk = RiskEngine(self.cfg)
        self.journal = TradeJournal(self.cfg["journal_path"])
        self._active_trades = {}
        self._stop_event = threading.Event()
        self._thread = None
        self._last_day = None
        self._equity_curve = []
        self._cycle_count = 0
        self._research_cache = {}

    # ---------- lifecycle ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ---------- reporting ----------

    def _log(self, m, l="info"):
        try:
            self.on_log(m, l)
        except Exception:
            pass

    def _emit(self, **kw):
        try:
            self.on_status(kw)
        except Exception:
            pass

    def _bars_emit(self, s, b, t):
        try:
            self.on_bars(s, b, t)
        except Exception:
            pass

    # ---------- LLM cascade ----------

    def _llm_call(self, client, model, system, user, temp=0.3, json_mode=False):
        kwargs = {"model": model,
                  "messages": [{"role":"system","content":system},
                               {"role":"user","content":user}],
                  "temperature": temp, "max_tokens": 300}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        r = client.chat.completions.create(**kwargs)
        return (r.choices[0].message.content or "").strip()

    def _call(self, primary, system, user, temp=0.3, json_mode=False):
        """NIM first, Groq second, then fallback chains."""
        chain = []
        if self.llm_nim:
            chain.append(("nim", self.llm_nim, primary))
            for m in self.cfg["nim_fallbacks"]:
                if m != primary:
                    chain.append(("nim", self.llm_nim, m))
        chain.append(("groq", self.llm, primary))
        for m in self.cfg["groq_fallbacks"]:
            if m != primary:
                chain.append(("groq", self.llm, m))

        last = None
        for prov, cli, mod in chain:
            try:
                return self._llm_call(cli, mod, system, user, temp, json_mode)
            except Exception as e:
                last = e
                s = str(e).lower()
                if any(x in str(e) for x in ("429","410","404")) or any(
                    x in s for x in ("rate_limit","tokens per day",
                    "not found","model_not_found","end of life",
                    "decommissioned","no longer available")):
                    self._log(f"{prov}:{mod} unavailable, trying next", "warn")
                    continue
                raise
        raise last

    # ---------- debate ----------

    def _debate(self, symbol, context, constraints, research=""):
        asset = "CRYPTO" if "/" in symbol else "STOCK/ETF"
        bu = f"Symbol: {symbol} ({asset})\nContext:\n{context}"
        be = f"Symbol: {symbol} ({asset})\nContext:\n{context}"
        if research:
            bu += f"\n\nResearch:\n{research}"
            be += f"\n\nResearch:\n{research}"
        bull = self._call(self.cfg["model_debate"], self.SYS_BULL, bu)
        bear = self._call(self.cfg["model_debate"], self.SYS_BEAR, be)
        ji = (f"Symbol: {symbol}\n\nBULL:\n{bull}\n\nBEAR:\n{bear}\n\n"
              f"CONSTRAINTS:\n{constraints}\n\nDecide. JSON only.")
        try:
            v = self._call(self.cfg["model_judge"], self.SYS_JUDGE, ji, 0.0, True)
            v = re.sub(r"```json|```", "", v).strip()
            d = json.loads(v)
        except Exception:
            d = {"action": "HOLD", "confidence": 0.0, "reason": "parse_fail"}
        d["bull"] = bull
        d["bear"] = bear
        return d

    # ---------- broker ----------

    def _account(self):
        a = self.trade.get_account()
        return {"equity": float(a.equity), "buying_power": float(a.buying_power)}

    def _positions(self):
        return [{"symbol": p.symbol, "qty": float(p.qty),
                 "avg_entry": float(p.avg_entry_price),
                 "current_price": float(p.current_price) if p.current_price else 0.0,
                 "market_value": float(p.market_value),
                 "unrealized_pl": float(p.unrealized_pl),
                 "unrealized_plpc": float(p.unrealized_plpc) * 100}
                for p in self.trade.get_all_positions()]

    def _bars(self, symbol, days=90):
        if "/" in symbol:
            req = CryptoBarsRequest(symbol_or_symbols=symbol,
                                     timeframe=TimeFrame.Day,
                                     start=datetime.now() - timedelta(days=days))
            return self.crypto_data.get_crypto_bars(req).df
        req = StockBarsRequest(symbol_or_symbols=symbol,
                                timeframe=TimeFrame.Day,
                                start=datetime.now() - timedelta(days=days))
        return self.data.get_stock_bars(req).df

    def _quote(self, symbol):
        if "/" in symbol:
            req = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
            q = self.crypto_data.get_crypto_latest_quote(req)[symbol]
        else:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            q = self.data.get_stock_latest_quote(req)[symbol]
        bid = float(q.bid_price)
        ask = float(q.ask_price)
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}

    def _submit(self, symbol, qty, side):
        is_crypto = "/" in symbol
        tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY
        o = MarketOrderRequest(symbol=symbol, qty=qty,
            side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            time_in_force=tif)
        return self.trade.submit_order(o)

    @staticmethod
    def _atr(df, p=14):
        h, l, c = df["high"].values, df["low"].values, df["close"].values
        tr = np.maximum(h[1:]-l[1:],
                        np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
        return float(np.mean(tr[-p:])) if len(tr) >= p else float(np.mean(tr))

    def _df_to_bars(self, symbol, df, n=30):
        out = []
        try:
            for ts, row in df.tail(n).iterrows():
                stamp = ts[1] if isinstance(ts, tuple) else ts
                out.append({"t": str(stamp),
                            "o": float(row["open"]), "h": float(row["high"]),
                            "l": float(row["low"]), "c": float(row["close"]),
                            "v": float(row["volume"]), "symbol": symbol})
        except Exception:
            pass
        return out

    # ---------- analysis ----------

    def analyze_symbol(self, symbol):
        symbol = symbol.upper().strip()
        try:
            account = self._account()
            positions = self._positions()
        except Exception as e:
            return {"ok": False, "error": f"Broker error: {e}"}

        try:
            bars = self._bars(symbol)
        except Exception as e:
            return {"ok": False, "error": f"No data for {symbol}: {e}"}
        if len(bars) < 20:
            return {"ok": False, "error": f"Not enough history for {symbol}"}

        try:
            q = self._quote(symbol)
        except Exception as e:
            return {"ok": False, "error": f"No quote for {symbol}: {e}"}

        atr = self._atr(bars)
        is_crypto = "/" in symbol

        if symbol not in self.memory:
            self.memory[symbol] = LayeredMemory()
        self.memory[symbol].push_tick({
            "symbol": symbol, "price": q["mid"],
            "volume": int(bars["volume"].iloc[-1]),
            "timestamp": datetime.utcnow().isoformat(), "atr": atr})

        mem = self.memory[symbol].summarize()
        pos_str = ", ".join(f"{p['symbol']}:{p['qty']:.4f}" for p in positions) or "none"
        context = (f"Equity: ${account['equity']:.2f}\n"
                   f"Buying power: ${account['buying_power']:.2f}\n"
                   f"Open: {pos_str}\nMemory: {mem}")
        c = self.cfg
        cons = (f"Note: risk engine handles position sizing automatically.\n"
                f"- Max {c['max_position_pct']*100:.0f}% per position (handled by engine)\n"
                f"- Max {c['max_concurrent_positions']} total positions\n"
                f"- Max {c['max_crypto_positions']} crypto positions\n"
                f"- Daily loss limit {c['max_daily_loss_pct']*100:.0f}%")

        decision = self._debate(symbol, context, cons, "")

        # ---- SELL only when we hold the asset ----
        if decision["action"] == "SELL":
            held = any(p["symbol"] == symbol and float(p["qty"]) > 0
                       for p in positions)
            if not held:
                self._log(f"[{symbol}] SELL signal but not held — skip", "info")
                bar_list = self._df_to_bars(symbol, bars, 30)
                self._bars_emit(symbol, bar_list, None)
                return {"ok": False, "symbol": symbol,
                        "action": "SELL",
                        "confidence": decision.get("confidence", 0),
                        "reason": f"Cannot short {symbol} (not held)",
                        "decision_reason": decision.get("reason", ""),
                        "bull": decision.get("bull", ""),
                        "bear": decision.get("bear", "")}

        report = self.risk.full_report(account, positions, decision,
                                        q["mid"], atr, is_crypto)

        bar_list = self._df_to_bars(symbol, bars, 30)
        self._bars_emit(symbol, bar_list, None)

        if not report["approved"]:
            return {"ok": False, "symbol": symbol,
                    "action": decision["action"],
                    "confidence": decision.get("confidence", 0),
                    "reason": report["reason"],
                    "decision_reason": decision.get("reason", ""),
                    "bull": decision.get("bull", ""),
                    "bear": decision.get("bear", "")}

        return {"ok": True, "symbol": symbol,
                "action": decision["action"],
                "confidence": decision.get("confidence", 0),
                "reason": decision.get("reason", ""),
                "bull": decision.get("bull", ""),
                "bear": decision.get("bear", ""),
                "entry": q["mid"], "qty": report["qty"],
                "stop_loss": report["stop_loss"],
                "take_profit": report["take_profit"],
                "risk_amount": report["risk_amount"],
                "reward_amount": report["reward_amount"],
                "is_crypto": is_crypto,
                "checks": report["checks"]}

    def execute_proposal(self, p):
        symbol = p["symbol"]
        try:
            order = self._submit(symbol, p["qty"], p["action"])
        except Exception as e:
            return {"ok": False, "error": str(e)}

        self._active_trades[symbol] = {
            "symbol": symbol, "side": p["action"], "qty": p["qty"],
            "entry": p["entry"], "sl": p["stop_loss"], "tp": p["take_profit"],
            "order_id": str(order.id),
            "opened_at": datetime.utcnow().isoformat()}

        try:
            self.journal.log(symbol=symbol, action=p["action"],
                confidence=p.get("confidence", 0),
                reason=p.get("reason", ""),
                bull=p.get("bull", ""), bear=p.get("bear", ""),
                approved=True, qty=p["qty"], entry=p["entry"],
                stop_loss=p["stop_loss"], take_profit=p["take_profit"],
                order_id=str(order.id))
        except Exception:
            pass

        return {"ok": True, "order_id": str(order.id),
                "symbol": symbol, "qty": p["qty"],
                "side": p["action"], "entry": p["entry"]}

    # ---------- main loop ----------

    def _run(self):
        mode = "PAPER" if self.cfg["paper"] else "LIVE"
        self._log(f"Sentinel auto-mode started - {mode}", "info")
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception as e:
                self._log(f"Cycle error: {e}", "error")
            for _ in range(self.cfg["cycle_seconds"] * 10):
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)
        self._log("Sentinel auto-mode stopped.", "info")

    def _cycle(self):
        self._cycle_count += 1
        try:
            a = self._account()
            ps = self._positions()
        except Exception as e:
            self._log(f"Broker error: {e}", "error")
            return
        self._equity_curve.append((datetime.utcnow().isoformat(), a["equity"]))
        if len(self._equity_curve) > 500:
            self._equity_curve = self._equity_curve[-500:]
        live = {p["symbol"] for p in ps}
        for s in list(self._active_trades.keys()):
            if s not in live:
                del self._active_trades[s]
        self._emit(equity=a["equity"], buying_power=a["buying_power"],
                   positions=ps, halted=self.risk.halted,
                   halt_reason=self.risk.halt_reason,
                   mode="PAPER" if self.cfg["paper"] else "LIVE",
                   equity_curve=list(self._equity_curve),
                   cycle=self._cycle_count)
        self._log(f"Cycle #{self._cycle_count} - equity "
                  f"${a['equity']:.2f}, {len(ps)} positions", "info")

        candidates = self.cfg["symbols"][:self.cfg["screen_top_stocks"]]
        candidates += self.cfg["crypto_symbols"][:self.cfg["screen_top_crypto"]]
        for p in ps:
            if p["symbol"] not in candidates:
                candidates.append(p["symbol"])

        for s in candidates:
            if self._stop_event.is_set():
                break
            try:
                r = self.analyze_symbol(s)
                if r.get("ok"):
                    self._log(f"[{s}] APPROVED {r['action']} qty={r['qty']}", "trade")
                    res = self.execute_proposal(r)
                    if res.get("ok"):
                        self._log(f"[{s}] order submitted: {res['order_id']}", "trade")
                else:
                    self._log(f"[{s}] {r.get('reason','no trade')}", "info")
            except Exception as e:
                self._log(f"[{s}] error: {e}", "error")
            time.sleep(1.0)


# Keep backward-compat alias so web.py doesn't break
TraderEngine = SentinelEngine


# ============================================================
# CHAT ENGINE
# ============================================================

class ChatEngine:
    """Conversational interface to Sentinel."""

    SYS = (
        "You are Sentinel, an AI trading assistant. You respond in ONE of "
        "two ways:\n\n"
        "1. PLAIN TEXT for greetings, questions, explanations.\n"
        "2. JSON ACTION when the system needs to do something. Output ONLY "
        "the JSON, no other text, no markdown.\n\n"
        "Actions:\n"
        '- {"action":"analyze","symbol":"AAPL"}\n'
        '- {"action":"quote","symbol":"AAPL"}\n'
        '- {"action":"positions"}\n'
        '- {"action":"cash"}\n'
        '- {"action":"confirm"}\n'
        '- {"action":"cancel"}\n\n'
        "Rules:\n"
        "- 'analyze X', 'look at X', 'should I buy X', 'what about X' -> analyze\n"
        "- 'yes', 'do it', 'execute', 'go ahead', 'confirm' -> confirm\n"
        "- 'no', 'cancel', 'never mind' -> cancel\n"
        "- 'positions', 'holdings' -> positions\n"
        "- 'cash', 'balance' -> cash\n"
        "- 'quote X', 'price of X' -> quote\n"
        "- If the user sends JUST a symbol (like 'BTC/USD', 'AAPL'), treat it "
        "as an analyze request.\n"
        "- Otherwise -> plain text reply. Be concise. No filler."
    )

    def __init__(self, engine):
        self.engine = engine
        self.history = deque(maxlen=30)
        self.pending = None

    def handle(self, user_msg):
        # Bare symbol → force analyze
        bare = user_msg.strip().upper().replace(" ", "")
        if re.fullmatch(r"[A-Z]{1,6}(/[A-Z]{2,4})?", bare):
            action = {"action": "analyze", "symbol": bare}
            self.history.append({"role": "user", "content": user_msg})
            result = self._run_action(action)
            self.history.append({"role": "assistant", "content": result["reply"]})
            return result

        try:
            account = self.engine._account()
            positions = self.engine._positions()
            pos_str = ", ".join(f"{p['symbol']}:{p['qty']:.4f}" for p in positions) or "none"
            context = (f"Equity: ${account['equity']:.2f}, "
                       f"Buying power: ${account['buying_power']:.2f}\n"
                       f"Positions: {pos_str}")
        except Exception as e:
            context = f"(Broker error: {e})"

        pending_txt = ""
        if self.pending:
            p = self.pending
            pending_txt = (f"\n\nPENDING awaiting confirmation:\n"
                           f"  {p['action']} {p['qty']} {p['symbol']} "
                           f"@ ~{p['entry']:.4f}\n"
                           f"  SL {p['stop_loss']:.4f}  TP {p['take_profit']:.4f}\n"
                           f"  Confidence: {p.get('confidence', 0):.2f}")

        full_sys = self.SYS + "\n\nCONTEXT:\n" + context + pending_txt

        try:
            reply = self.engine._call(
                self.engine.cfg["model_judge"],
                full_sys, user_msg, temp=0.3)
        except Exception as e:
            reply = f"[LLM error: {e}]"

        self.history.append({"role": "user", "content": user_msg})
        action = self._parse_action(reply)
        if action:
            result = self._run_action(action)
            self.history.append({"role": "assistant", "content": result["reply"]})
            return result
        self.history.append({"role": "assistant", "content": reply})
        return {"reply": reply, "pending": self.pending, "type": "text"}

    def _parse_action(self, reply):
        reply = reply.strip()
        if not reply.startswith("{"):
            return None
        try:
            clean = re.sub(r"```json|```", "", reply).strip()
            d = json.loads(clean)
            if "action" in d:
                return d
        except Exception:
            pass
        return None

    def _run_action(self, action):
        a = action.get("action", "").lower()

        if a == "analyze":
            return self._do_analyze(action.get("symbol", "").upper())

        if a == "quote":
            sym = action.get("symbol", "").upper()
            try:
                q = self.engine._quote(sym)
                return {"reply": f"{sym}: bid ${q['bid']:.4f} / ask ${q['ask']:.4f}",
                        "pending": self.pending, "type": "text"}
            except Exception as e:
                return {"reply": f"Cannot quote {sym}: {e}",
                        "pending": self.pending, "type": "text"}

        if a == "positions":
            try:
                ps = self.engine._positions()
                if not ps:
                    return {"reply": "No open positions.",
                            "pending": self.pending, "type": "text"}
                lines = [f"{p['symbol']}: {p['qty']:.4f} @ ${p['avg_entry']:.4f}  "
                         f"now ${p['current_price']:.4f}  "
                         f"P/L ${p['unrealized_pl']:+.2f}"
                         for p in ps]
                return {"reply": "\n".join(lines),
                        "pending": self.pending, "type": "text"}
            except Exception as e:
                return {"reply": f"Error: {e}", "pending": self.pending, "type": "text"}

        if a == "cash":
            try:
                acc = self.engine._account()
                return {"reply": f"Equity ${acc['equity']:.2f}, "
                                  f"buying power ${acc['buying_power']:.2f}",
                        "pending": self.pending, "type": "text"}
            except Exception as e:
                return {"reply": f"Error: {e}", "pending": self.pending, "type": "text"}

        if a == "confirm":
            if not self.pending:
                return {"reply": "Nothing pending.",
                        "pending": None, "type": "text"}
            res = self.engine.execute_proposal(self.pending)
            if res["ok"]:
                msg = (f"EXECUTED: {res['side']} {res['qty']} {res['symbol']} "
                       f"@ ~{res['entry']:.4f}\nOrder ID: {res['order_id']}")
                self.pending = None
                return {"reply": msg, "pending": None, "type": "trade"}
            else:
                return {"reply": f"Execution failed: {res.get('error')}",
                        "pending": self.pending, "type": "error"}

        if a == "cancel":
            self.pending = None
            return {"reply": "Cancelled.", "pending": None, "type": "text"}

        return {"reply": f"Unknown action: {a}",
                "pending": self.pending, "type": "text"}

    def _do_analyze(self, symbol):
        if not symbol:
            return {"reply": "Which symbol?",
                    "pending": self.pending, "type": "text"}

        result = self.engine.analyze_symbol(symbol)

        if not result.get("ok"):
            reason = result.get("reason") or result.get("error") or "unknown"
            if result.get("action") == "HOLD":
                msg = (f"{symbol}: HOLD at {result.get('confidence', 0):.0%} "
                       f"confidence.\n{result.get('decision_reason', '')}")
                return {"reply": msg, "pending": None, "type": "text"}
            return {"reply": f"{symbol}: no trade. {reason}",
                    "pending": None, "type": "text"}

        self.pending = result
        arrow = "BUY" if result["action"] == "BUY" else "SELL"
        notional = result["entry"] * result["qty"]
        msg = (
            f"{symbol} - {arrow} signal, {result['confidence']:.0%} confidence.\n\n"
            f"Entry: ~${result['entry']:.4f}\n"
            f"Qty: {result['qty']} (~${notional:,.2f})\n"
            f"Stop loss: ${result['stop_loss']:.4f}\n"
            f"Take profit: ${result['take_profit']:.4f}\n"
            f"Risk: ${result['risk_amount']:.2f}  "
            f"Reward: ${result['reward_amount']:.2f}\n\n"
            f"Reason: {result['reason'][:250]}\n\n"
            f"Reply 'yes' to execute, 'no' to cancel."
        )
        return {"reply": msg, "pending": self.pending, "type": "proposal"}