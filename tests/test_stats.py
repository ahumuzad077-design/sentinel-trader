import os
import sqlite3
import tempfile
import unittest

from stats import compute_stats, format_stats


class TradeStatsTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        os.unlink(self.path)

    def make_current_schema(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE closed_trades (
                id INTEGER PRIMARY KEY, opened_ts TEXT, closed_ts TEXT,
                symbol TEXT, side TEXT, qty REAL, entry REAL, exit REAL,
                pnl REAL, pnl_pct REAL, close_reason TEXT)""")
            db.executemany("""INSERT INTO closed_trades
                (opened_ts, closed_ts, symbol, side, qty, entry, exit,
                 pnl, pnl_pct, close_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
                ("a", "2026-01-01", "ABC", "BUY", 1, 10, 12, 2, 20, "tp"),
                ("b", "2026-01-02", "ABC", "BUY", 1, 12, 11, -1, -8.3, "sl"),
            ])

    def test_current_schema_and_ordering(self):
        self.make_current_schema()
        stats = compute_stats(self.path)
        self.assertNotIn("error", stats)
        self.assertEqual(stats["trades"], 2)
        self.assertEqual(stats["total_pnl"], 1)
        self.assertEqual(stats["max_pnl_curve_drawdown"], 1)
        self.assertNotIn("sharpe", stats)
        self.assertIn("not account-equity drawdown", stats["metric_notes"])
        self.assertIn("not prop-firm rule compliance", format_stats(stats))

    def test_legacy_ts_column_is_supported(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE closed_trades (
                id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT,
                qty REAL, entry REAL, exit REAL, pnl REAL, pnl_pct REAL,
                close_reason TEXT)""")
            db.execute("""INSERT INTO closed_trades
                (ts, symbol, side, qty, entry, exit, pnl, pnl_pct, close_reason)
                VALUES ('2026-01-01', 'XYZ', 'BUY', 1, 1, 2, 1, 100, 'tp')""")
        self.assertEqual(compute_stats(self.path)["trades"], 1)

    def test_missing_schema_returns_structured_error(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE closed_trades (id INTEGER PRIMARY KEY)")
        result = compute_stats(self.path)
        self.assertIn("error", result)
        self.assertEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
