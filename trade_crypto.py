"""Optional one-shot crypto scanner; paper-order placement is opt-in only.

The runner refuses live mode and does not weaken thresholds from the app
configuration. It is experimental and not a prop-firm compliance tool.
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import os

from bot import SentinelEngine

# Full Alpaca crypto universe (all 24/7 markets)
CRYPTO_PAIRS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD",
    "LTC/USD", "BCH/USD", "DOGE/USD", "UNI/USD", "AAVE/USD",
    "DOT/USD", "ADA/USD", "XRP/USD", "ATOM/USD", "NEAR/USD",
    "FIL/USD", "GRT/USD", "CRV/USD", "SAND/USD", "MANA/USD",
]

MAX_TRADES = 3

def main():
    if os.environ.get("ALPACA_PAPER_TRADE", "true").lower() != "true":
        print("Refusing to run crypto order placement outside paper mode.")
        return
    if os.environ.get("ENABLE_CRYPTO_ORDER_PLACEMENT", "false").lower() != "true":
        print("Crypto order placement is disabled. Set "
              "ENABLE_CRYPTO_ORDER_PLACEMENT=true to opt in to paper orders.")
        return

    print("=" * 60)
    print("  Sentinel Crypto Trader")
    print("=" * 60)
    print(f"  Scanning {len(CRYPTO_PAIRS)} crypto markets...")
    print(f"  Max trades this run: {MAX_TRADES}")
    print("=" * 60)
    print()

    e = SentinelEngine(on_log=lambda m, l: print(f"[{l}] {m}"))

    # Check existing positions first
    try:
        existing = e._positions()
        existing_crypto = [p for p in existing if "/" in p["symbol"]]
        if existing_crypto:
            print(f"Currently holding {len(existing_crypto)} crypto position(s):")
            for p in existing_crypto:
                print(f"  {p['symbol']}: {p['qty']:.6f} @ ${p['avg_entry']:.4f}  "
                      f"P/L ${p['unrealized_pl']:+.2f}")
            print()
    except Exception as ex:
        print(f"Could not fetch positions: {ex}\n")

    # Scan and trade
    placed = 0
    scanned = 0
    holds = 0
    sells_skipped = 0

    for sym in CRYPTO_PAIRS:
        if placed >= MAX_TRADES:
            print(f"\nReached MAX_TRADES ({MAX_TRADES}). Stopping.")
            break

        scanned += 1
        try:
            r = e.analyze_symbol(sym)

            if r.get("ok"):
                print(f">>> {sym}: {r['action']} {r['qty']} @ ${r['entry']:.4f}")
                print(f"    SL ${r['stop_loss']:.4f}  TP ${r['take_profit']:.4f}")
                print(f"    Risk ${r['risk_amount']:.2f}  Reward ${r['reward_amount']:.2f}")
                print(f"    Confidence: {r['confidence']:.2f}")
                print(f"    Reason: {r['reason'][:150]}")
                res = e.execute_proposal(r)
                if res.get("ok"):
                    print(f"    ORDER PLACED: {res['order_id']}\n")
                    placed += 1
                else:
                    print(f"    FAILED: {res.get('error')}\n")
            else:
                action = r.get("action", "?")
                reason = (r.get("reason") or r.get("error") or "")[:90]
                if action == "SELL" and "not held" in reason.lower():
                    print(f"  {sym}: SELL skipped (not held)")
                    sells_skipped += 1
                elif action == "HOLD":
                    print(f"  {sym}: HOLD — {reason}")
                    holds += 1
                else:
                    print(f"  {sym}: {action} — {reason}")

        except Exception as ex:
            print(f"  {sym}: ERROR {ex}")

    print()
    print("=" * 60)
    print(f"  Scan complete.")
    print(f"  Scanned:        {scanned}")
    print(f"  HOLDs:          {holds}")
    print(f"  SELLs skipped:  {sells_skipped}")
    print(f"  Trades placed:  {placed}")
    print("=" * 60)

    if placed > 0:
        print()
        print("Check your positions at:")
        print("  https://app.alpaca.markets/paper/dashboard/overview")
        print()
        print("To start Sentinel in auto mode for 24/7 crypto trading:")
        print("  python web.py")
        print("  Then open http://localhost:5000 and click 'Start Auto'")


if __name__ == "__main__":
    main()
