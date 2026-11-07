import time
import unittest

from bot_test_support import bot


class FakeEngine:
    def __init__(self, positions):
        self.positions = positions
        self.close_calls = []
        self.account = {"equity": 10000.0, "buying_power": 5000.0}
        self._active_trades = {}
        self.learner = None
        self.snapshots = []

    def _positions(self):
        return [dict(position) for position in self.positions]

    def _account(self):
        return dict(self.account)

    def _close_position(self, symbol, reason):
        self.close_calls.append((symbol, reason))
        return True

    def _emit_account_snapshot(self, account, positions):
        self.snapshots.append((account, positions))


class CloseConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.initial_positions = [
            {"symbol": "AAPL", "qty": 2.0, "avg_entry": 100.0,
             "current_price": 110.0, "unrealized_pl": 20.0,
             "unrealized_plpc": 10.0},
            {"symbol": "MSFT", "qty": 1.5, "avg_entry": 200.0,
             "current_price": 190.0, "unrealized_pl": -15.0,
             "unrealized_plpc": -5.0},
        ]
        self.engine = FakeEngine(self.initial_positions)
        self.chat = bot.ChatEngine(self.engine)

    def test_close_only_creates_review_and_does_not_submit(self):
        response = self.chat._run_action({"action": "close", "symbol": "AAPL"})
        self.assertEqual(response["type"], "exit-proposal")
        self.assertEqual(response["pending"]["pending_type"], "exit")
        self.assertEqual([p["symbol"] for p in response["pending"]["positions"]], ["AAPL"])
        self.assertEqual(self.engine.close_calls, [])

    def test_close_is_sent_only_after_confirm(self):
        self.chat._run_action({"action": "close", "symbol": "AAPL"})
        self.assertEqual(self.engine.close_calls, [])
        result = self.chat._run_action({"action": "confirm"})
        self.assertEqual(self.engine.close_calls[0][0], "AAPL")
        self.assertIsNone(result["pending"])
        self.assertIn("Verify fills", result["reply"])

    def test_close_all_proposes_every_position_without_sending(self):
        response = self.chat._run_action({"action": "close_all"})
        self.assertEqual(response["type"], "exit-proposal")
        self.assertEqual([p["symbol"] for p in response["pending"]["positions"]],
                         ["AAPL", "MSFT"])
        self.assertEqual(self.engine.close_calls, [])

    def test_changed_position_is_rejected(self):
        self.chat._run_action({"action": "close", "symbol": "AAPL"})
        self.engine.positions[0]["qty"] = 3.0
        result = self.chat._run_action({"action": "confirm"})
        self.assertEqual(self.engine.close_calls, [])
        self.assertIsNone(result["pending"])
        self.assertIn("changed since review", result["reply"])

    def test_expired_exit_proposal_does_not_submit(self):
        self.chat._run_action({"action": "close", "symbol": "AAPL"})
        self.chat.pending["created_at"] = time.time() - 121
        result = self.chat._run_action({"action": "confirm"})
        self.assertEqual(self.engine.close_calls, [])
        self.assertIsNone(result["pending"])
        self.assertIn("expired", result["reply"])

    def test_cancel_clears_exit_proposal_without_submitting(self):
        self.chat._run_action({"action": "close_all"})
        result = self.chat._run_action({"action": "cancel"})
        self.assertEqual(self.engine.close_calls, [])
        self.assertIsNone(result["pending"])
        self.assertIn("Cancelled", result["reply"])


if __name__ == "__main__":
    unittest.main()
