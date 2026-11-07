import threading
import time
import unittest

from bot_test_support import bot


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "max_daily_loss_pct": 0.02,
            "max_total_drawdown_pct": 0.06,
            "max_concurrent_positions": 5,
            "max_crypto_positions": 3,
            "min_confidence": 0.5,
            "max_position_pct": 1.0,
            "max_trade_risk_pct": 0.005,
            "atr_stop_multiplier": 1.5,
            "risk_reward_ratio": 2.0,
        }
        self.engine = bot.RiskEngine(self.cfg)

    def report(self, action="BUY", confidence=1.0, equity=10000,
               entry=100, atr=2, is_crypto=False):
        return self.engine.full_report(
            {"equity": equity}, [],
            {"action": action, "confidence": confidence},
            entry, atr, is_crypto)

    def test_size_is_limited_by_stop_risk_budget(self):
        result = self.report()
        self.assertTrue(result["approved"], result["reason"])
        self.assertLessEqual(result["risk_amount"], 50.0)
        self.assertEqual(result["qty"], 16)

    def test_rejects_when_risk_budget_cannot_buy_minimum_unit(self):
        result = self.report(equity=1000, entry=100, atr=20)
        self.assertFalse(result["approved"])
        self.assertIn("too small", result["reason"])

    def test_rejects_short_entries_and_holds(self):
        for action in ("SELL", "HOLD"):
            result = self.report(action=action)
            self.assertFalse(result["approved"])

    def test_rejects_invalid_atr_and_confidence(self):
        for kwargs in ({"atr": 0}, {"atr": float("nan")},
                       {"confidence": 2}):
            result = self.report(**kwargs)
            self.assertFalse(result["approved"])

    def test_expired_proposal_is_never_submitted(self):
        engine = bot.SentinelEngine.__new__(bot.SentinelEngine)
        engine._execution_lock = threading.Lock()
        engine._submit = lambda *args: self.fail("stale proposal was submitted")
        proposal = {"symbol": "ABC", "action": "BUY", "qty": 1,
                    "entry": 100, "stop_loss": 97, "take_profit": 106,
                    "confidence": 1, "created_at": time.time() - 121}
        result = engine.execute_proposal(proposal)
        self.assertFalse(result["ok"])
        self.assertIn("expired", result["error"])

    def test_execution_rechecks_risk_before_submission(self):
        engine = bot.SentinelEngine.__new__(bot.SentinelEngine)
        engine._execution_lock = threading.Lock()
        engine.cfg = {"max_entry_drift_pct": 0.002,
                      "max_trade_risk_pct": 0.005,
                      "max_position_pct": 0.03,
                      "risk_reward_ratio": 2.0}
        engine._quote = lambda symbol: {"mid": 100.0}
        engine._account = lambda: {"equity": 10000.0, "buying_power": 10000.0}
        engine._positions = lambda: []
        engine._bars = lambda symbol: object()
        engine._atr = lambda bars: 2.0
        engine.risk = type("Risk", (), {"full_report": lambda self, *args: {
            "approved": False, "reason": "test halt", "qty": 0}})()
        engine._log = lambda *args: None
        engine._submit = lambda *args: self.fail("risk-denied proposal submitted")
        proposal = {"symbol": "ABC", "action": "BUY", "qty": 1,
                    "entry": 100, "stop_loss": 97, "take_profit": 106,
                    "confidence": 1, "created_at": time.time()}
        result = engine.execute_proposal(proposal)
        self.assertFalse(result["ok"])
        self.assertIn("Risk checks", result["error"])


if __name__ == "__main__":
    unittest.main()
