"""Regression coverage for Telegram account/session isolation."""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import alerts, trade_tracker


class AccountIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trades_file = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False
        )
        self.trades_file.close()
        with open(self.trades_file.name, "w") as f:
            json.dump({"trades": []}, f)
        self.state_file = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False
        )
        self.state_file.close()
        with open(self.state_file.name, "w") as f:
            json.dump({"accounts": {}}, f)
        self.trade_path_patch = patch.object(
            trade_tracker, "TRADES_PATH", self.trades_file.name
        )
        self.state_path_patch = patch.object(
            alerts, "SIGNAL_STATE_PATH", self.state_file.name
        )
        self.trade_path_patch.start()
        self.state_path_patch.start()

    def tearDown(self):
        self.state_path_patch.stop()
        self.trade_path_patch.stop()
        os.unlink(self.trades_file.name)
        os.unlink(self.state_file.name)

    def _open(self, account_id, direction="BUY"):
        if direction == "BUY":
            levels = (100.0, 90.0, 110.0, 120.0, 130.0)
        else:
            levels = (100.0, 110.0, 90.0, 80.0, 70.0)
        entry, sl, tp1, tp2, tp3 = levels
        return trade_tracker.open_trade(
            direction=direction,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            timeframe="M15",
            confidence=85,
            rr_ratio=2.0,
            account_id=account_id,
        )

    def test_same_timeframe_trades_are_independent_between_accounts(self):
        self.assertTrue(self._open(101, "BUY"))
        self.assertTrue(self._open(202, "SELL"))

        first = trade_tracker.get_active_trades(101)
        second = trade_tracker.get_active_trades(202)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0]["account_id"], "101")
        self.assertEqual(second[0]["account_id"], "202")
        self.assertNotEqual(first[0]["id"], second[0]["id"])

    def test_exit_scan_changes_only_the_requested_account(self):
        self.assertTrue(self._open(101, "BUY"))
        self.assertTrue(self._open(202, "BUY"))

        events = trade_tracker.check_trades(89.0, account_id=101)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trade"]["account_id"], "101")
        self.assertEqual(len(trade_tracker.get_active_trades(101)), 0)
        self.assertEqual(len(trade_tracker.get_active_trades(202)), 1)

    def test_alert_state_is_persisted_under_separate_account_namespaces(self):
        first = alerts.AccountAlertState(active_signal={"M15": "BUY"})
        second = alerts.AccountAlertState(active_signal={"M15": "SELL"})
        alerts._save_account_state(101, first)
        alerts._save_account_state(202, second)

        self.assertEqual(
            alerts._load_account_state(101).active_signal, {"M15": "BUY"}
        )
        self.assertEqual(
            alerts._load_account_state(202).active_signal, {"M15": "SELL"}
        )

    async def test_scheduler_invokes_one_isolated_scan_per_subscriber(self):
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))
        state = alerts.AccountAlertState()
        with patch.object(alerts, "_load", return_value={202, 101}), \
             patch.object(alerts, "_load_account_state", return_value=state), \
             patch.object(alerts, "_check_and_alert_once", new=AsyncMock()) as scan:
            await alerts.check_and_alert(context)

        self.assertEqual(
            [call.kwargs["account_id"] for call in scan.await_args_list],
            [101, 202],
        )

    async def test_delayed_sl_result_is_not_sent_while_new_plan_is_active(self):
        closed = {
            "id": "old-sl",
            "account_id": "101",
            "direction": "BUY",
            "timeframe": "M15",
            "entry": 100.0,
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "status": "sl_hit",
            "closed_at": 10.0,
            "close_reason": "stop_loss",
            "result_notification_pending": True,
        }
        active = {
            "id": "new-open",
            "account_id": "101",
            "direction": "SELL",
            "timeframe": "M15",
            "entry": 100.0,
            "sl": 110.0,
            "tp1": 90.0,
            "tp2": 80.0,
            "status": "open",
        }
        with open(self.trades_file.name, "w") as f:
            json.dump({"trades": [closed, active]}, f)

        bot = AsyncMock()
        sent = await alerts._send_verified_result_event(
            bot, {101}, closed, "SL", 90.0, account_id=101
        )

        self.assertFalse(sent)
        bot.send_message.assert_not_awaited()
        bot.send_photo.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()