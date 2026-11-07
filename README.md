# Sentinel Trader

Sentinel is an experimental trading dashboard and automated trading prototype. It is **not certified for any prop-firm evaluation**, does not promise profits, and should not be pointed at a funded/live account without a firm-specific rule review and end-to-end testing.

## Secure dashboard deployment

The dashboard and all API routes require HTTP Basic Authentication. Configure both of these environment variables in the Render service before using the app:

- `DASHBOARD_USERNAME`: a unique dashboard username.
- `DASHBOARD_PASSWORD`: a long, random password (use a password manager).

Set them in Render's protected Environment settings—never commit them, include them in a Docker image, or send them in a URL. Without both values, the app denies dashboard/API access. Protected routes also reject plain HTTP; the app trusts one forwarded-protocol value from Render's TLS-terminating edge. Do not expose the WSGI listener directly or copy this proxy trust setting to an environment with a different proxy topology without reviewing it. `/healthz` remains unauthenticated and returns only `{ "ok": true }`.

The browser will request credentials when opening the dashboard. The dashboard's same-origin API calls use that authenticated session. Keep Render HTTPS enabled. `AUTO_START` should remain `false`; start/stop and chat controls are privileged operations.

**Important:** these code changes are local to a review branch until deliberately published. The currently deployed Render app is not protected by them unless you deploy a reviewed build. The repository alone does not show whether an external authentication layer is already in front of the current deployment.

## Dashboard controls and display

The dashboard is a responsive, same-origin interface with account summary cards, a high-DPI market chart, broker position table, closed-trade journal metrics, and an assistant/activity console. Account figures are blank until a broker snapshot arrives; the UI shows the age of the last snapshot rather than treating a reachable API as proof that account data is current. Journal metrics are descriptive and are not prop-firm drawdown, daily-loss, or rule-compliance calculations.

Starting automation opens a mode-aware review. PAPER mode requires a confirmation click; LIVE mode additionally requires typing `START LIVE`, and the `/api/start` route enforces that acknowledgement server-side. Stopping the engine only stops automated scanning; it does not liquidate positions. Chat-generated entries and manual close/close-all requests are shown as expiring, position-specific proposals and are not submitted until explicitly confirmed. Close proposals are rejected if the position quantity changes before confirmation. These controls reduce accidental actions but do not replace broker-side protections, firm-specific risk validation, or monitoring.

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
