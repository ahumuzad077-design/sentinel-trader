# Sentinel Trader

Sentinel is an experimental trading dashboard and automated trading prototype. It is **not certified for any prop-firm evaluation**, does not promise profits, and should not be pointed at a funded/live account without a firm-specific rule review and end-to-end testing.

## Public dashboard deployment

The dashboard and API routes are intentionally public: **anyone with the Render URL can view the page and use its trading controls**. There is no password gate. Do not treat the URL as a secret or rely on it to protect account data or orders. Keep Render HTTPS enabled; non-HTTPS requests are rejected, and state-changing requests require a same-origin `Origin` or `Referer` header. These checks do not stop a person visiting the public dashboard directly.

For safety, `AUTO_START=true` alone is ignored in public mode; automatic startup requires the additional `ALLOW_PUBLIC_AUTO_START=true`. The dashboard's Start control remains available and asks for explicit confirmation. Do not enable public auto-start unless you intentionally want the trading worker to run after every restart.

The container runs as an unprivileged user. Its SQLite trade journal defaults to `/var/lib/sentinel/trades.db`, with a writable fallback working directory for configured relative journal paths. This location is local container storage and may be lost when Render replaces the instance; attach and configure a persistent disk if journal retention across restarts is required.

`/healthz` remains a minimal unauthenticated liveness check that returns `{ "ok": true }`.

## Dashboard controls and display

The dashboard is a responsive, same-origin interface with account summary cards, a high-DPI market chart, broker position table, closed-trade journal metrics, and an assistant/activity console. Account figures are blank until a broker snapshot arrives; the UI shows the age of the last snapshot rather than treating a reachable API as proof that account data is current. Journal metrics are descriptive and are not prop-firm drawdown, daily-loss, or rule-compliance calculations.

Starting automation opens a mode-aware review. PAPER mode requires a confirmation click; LIVE mode additionally requires typing `START LIVE`, and the `/api/start` route enforces that acknowledgement server-side. Stopping the engine only stops automated scanning; it does not liquidate positions. Chat-generated entries and manual close/close-all requests are shown as expiring, position-specific proposals and are not submitted until explicitly confirmed. Close proposals are rejected if the position quantity changes before confirmation. However, because the dashboard is public, any visitor can use these workflows; these confirmations are not authentication or access control. Use PAPER mode unless you fully accept that risk.

## Trading and risk limitations

- `ALPACA_PAPER_TRADE` defaults to `true`. A non-paper client is blocked unless `ALLOW_LIVE_TRADING=true` is separately set; do not set it for initial testing.
- `MAX_TRADE_RISK_PCT` defaults to `0.005` (0.5% of current equity) and caps calculated quantity using the proposed ATR stop distance. This is a sizing estimate, **not a guaranteed maximum loss**: stop gaps, slippage, fees, broker outages, stale prices, and rejected/partial orders can exceed it.
- Confirmed proposals expire after two minutes, use a fresh quote/account/position check, and are rejected if price drift exceeds `MAX_ENTRY_DRIFT_PCT` (default `0.002`, or 0.2%). A proposal is not silently repriced or resized.
- `MAX_POSITION_PCT` is a separate notional exposure cap; it is not the loss budget.
- The displayed stop/target levels are locally monitored by the running process. This code does not submit broker-native protective stop/OCO orders, so they are not guaranteed to execute if the process or data feed fails.
- The daily and total drawdown baselines and loss streak are process-memory state, not durable evaluation-account state. Restarts can reset them, and the daily rollover currently uses UTC. Do not rely on these checks for any prop firm's rule enforcement.
- Prop firms differ in daily-loss calculation, time zone, floating P/L and fees, trailing drawdown, consistency, instrument, holding-time, and news rules. Configure and test against the specific firm's current official rules before evaluation use.
- `trade_crypto.py` no longer overrides strategy thresholds and refuses non-paper mode; paper order placement is also disabled unless `ENABLE_CRYPTO_ORDER_PLACEMENT=true` is explicitly set. The UI start control enables autonomous order activity when the worker runs.

## Local checks

Run the tests with:

```sh
python -m unittest discover -s tests -v
```

The application dependencies are listed in `requirements.txt`. Tests use temporary SQLite databases and do not call broker or language-model services.
