"""Descriptive journal statistics; these are not prop-firm compliance metrics."""

import math
import sqlite3


REQUIRED_COLUMNS = {
    "symbol", "side", "qty", "entry", "exit", "pnl", "pnl_pct",
    "close_reason",
}


def compute_stats(db_path="trades.db"):
    """Calculate descriptive trade-journal metrics from supported schemas.

    Supports the current ``closed_ts`` schema and legacy ``ts`` journals.
    This table does not contain a complete account-equity time series, so its
    P/L-curve drawdown and per-trade return ratio must not be interpreted as
    prop-firm daily/max drawdown or annualized Sharpe.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(closed_trades)")}
            if not columns:
                return {"trades": 0, "msg": "No closed-trade journal yet."}
            missing = REQUIRED_COLUMNS - columns
            if missing:
                return {"error": "closed_trades schema missing required columns: "
                                  + ", ".join(sorted(missing)), "trades": 0}

            order_column = ("closed_ts" if "closed_ts" in columns else
                            "ts" if "ts" in columns else
                            "id" if "id" in columns else None)
            fields = ("symbol", "side", "qty", "entry", "exit", "pnl",
                      "pnl_pct", "close_reason")
            sql = "SELECT " + ", ".join(fields)
            if order_column:
                sql += " FROM closed_trades ORDER BY " + order_column + " ASC"
            else:
                sql += " FROM closed_trades"
            rows = conn.execute(sql).fetchall()
    except Exception as exc:
        return {"error": str(exc), "trades": 0}

    if not rows:
        return {"trades": 0, "msg": "No closed trades yet."}

    wins, losses = [], []
    total_pnl = 0.0
    returns = []
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_pnl_curve_drawdown = 0.0
    equity_curve = [0.0]

    for row in rows:
        pnl = float(row[5] or 0.0)
        pnl_pct = float(row[6] or 0.0)
        total_pnl += pnl
        returns.append(pnl_pct)
        (wins if pnl >= 0 else losses).append(pnl)
        cumulative_pnl += pnl
        equity_curve.append(cumulative_pnl)
        peak_pnl = max(peak_pnl, cumulative_pnl)
        max_pnl_curve_drawdown = max(
            max_pnl_curve_drawdown, peak_pnl - cumulative_pnl)

    n = len(rows)
    win_count, loss_count = len(wins), len(losses)
    win_rate = win_count / n * 100 if n else 0.0
    avg_win = sum(wins) / win_count if win_count else 0.0
    avg_loss = sum(losses) / loss_count if loss_count else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
    return_volatility = math.sqrt(variance)
    return_ratio = mean_return / return_volatility if return_volatility else 0.0

    best = max(rows, key=lambda row: float(row[5] or 0.0))
    worst = min(rows, key=lambda row: float(row[5] or 0.0))
    symbol_stats = {}
    for row in rows:
        symbol, pnl, pct = row[0], float(row[5] or 0.0), float(row[6] or 0.0)
        item = symbol_stats.setdefault(
            symbol, {"wins": 0, "losses": 0, "pnl": 0.0, "n": 0,
                     "return_pct_sum": 0.0})
        item["pnl"] += pnl
        item["n"] += 1
        item["return_pct_sum"] += pct
        item["wins" if pnl >= 0 else "losses"] += 1

    return {
        "trades": n,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_pnl_curve_drawdown": max_pnl_curve_drawdown,
        "mean_trade_return_volatility_ratio": return_ratio,
        "equity_curve": equity_curve,
        "best": {"symbol": best[0], "pnl": float(best[5] or 0.0)},
        "worst": {"symbol": worst[0], "pnl": float(worst[5] or 0.0)},
        "symbol_stats": symbol_stats,
        "metric_notes": (
            "Journal-only metrics; not account-equity drawdown, daily-loss "
            "compliance, or annualized Sharpe."),
    }


def format_stats(stats):
    if stats.get("trades", 0) == 0:
        return stats.get("msg", "No closed trades yet.")

    pf = stats["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
    lines = [
        f"JOURNAL PERFORMANCE -- {stats['trades']} closed trades",
        "",
        f"  Win rate:                   {stats['win_rate']:.1f}% "
        f"({stats['wins']}W / {stats['losses']}L)",
        f"  Total P/L:                  ${stats['total_pnl']:+,.2f}",
        f"  Avg win:                    ${stats['avg_win']:+,.2f}",
        f"  Avg loss:                   ${stats['avg_loss']:+,.2f}",
        f"  Profit factor:              {pf_str}",
        f"  P/L-curve drawdown:         ${stats['max_pnl_curve_drawdown']:,.2f}",
        "  Mean trade-return/volatility ratio (not annualized): "
        f"{stats['mean_trade_return_volatility_ratio']:.2f}",
        "",
        f"  Best:  {stats['best']['symbol']}  ${stats['best']['pnl']:+,.2f}",
        f"  Worst: {stats['worst']['symbol']}  ${stats['worst']['pnl']:+,.2f}",
        "",
        "Note: journal-only metrics; not prop-firm rule compliance or account drawdown.",
    ]

    symbols = stats.get("symbol_stats", {})
    if symbols:
        lines.extend(["", "Symbols:"])
        ranked = sorted(symbols.items(), key=lambda item: item[1]["pnl"],
                        reverse=True)
        for symbol, item in ranked[:10]:
            count = item["n"]
            win_rate = item["wins"] / count * 100 if count else 0
            lines.append(f"  {symbol:12s}  {item['wins']}W/{item['losses']}L  "
                         f"{win_rate:.0f}%  ${item['pnl']:+,.2f}")
    return "\n".join(lines)
