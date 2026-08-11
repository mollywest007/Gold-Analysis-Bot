"""Regression tests for timeframe-aware missed-entry reminders."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import alerts, trade_tracker


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.users_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.users_file.close()
        with open(self.users_file.name, "w") as f:
            json.dump({"subscribers": [123], "disabled": []}, f)

        self.trades_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.trades_file.close()

        self.users_patch = patch.object(alerts, "DATA_PATH", self.users_file.name)
        self.trades_patch = patch.object(trade_tracker, "TRADES_PATH", self.trades_file.name)
        self.users_patch.start()
        self.trades_patch.start()
        self.reminders_patch = patch.object(alerts, "_reminded_trade_ids", {})
        self.reminders_patch.start()

    def tearDown(self):
        self.reminders_patch.stop()
        self.trades_patch.stop()
        self.users_patch.stop()
        os.unlink(self.users_file.name)
        os.unlink(self.trades_file.name)

    def _write_trade(self, opened_at):
        with open(self.trades_file.name, "w") as f:
            json.dump(
                {
                    "trades": [
                        {
                            "id": "reminder-test",
                            "direction": "BUY",
                            "entry": 100.0,
                            "sl": 90.0,
                            "tp1": 110.0,
                            "tp2": 120.0,
                            "tp3": 130.0,
                            "timeframe": "M15",
                            "confidence": 85,
                            "opened_at": opened_at,
                            "status": "open",
                            "tp1_hit": False,
                            "tp2_hit": False,
                            "tp3_hit": False,
                        }
                    ]
                },
                f,
            )

    async def _run_reminder(self, price):
        bot = AsyncMock()
        context = type("Context", (), {"bot": bot})()
        with patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=price)), \
             patch.object(alerts, "_broadcast_text", new=AsyncMock(return_value=set())) as broadcast:
            await alerts.send_trade_reminder(context)
        return broadcast

    async def test_near_entry_sends_missed_alert(self):
        self._write_trade(time.time() - 15 * 60)

        broadcast = await self._run_reminder(100.10)

        self.assertEqual(broadcast.await_count, 1)
        self.assertIn("MISSED ALERT — ENTRY STILL OPEN", broadcast.await_args.args[2])

    async def test_moved_entry_does_not_send_missed_alert(self):
        self._write_trade(time.time() - 15 * 60)

        broadcast = await self._run_reminder(102.00)

        broadcast.assert_not_awaited()
        self.assertIn("entry", alerts._reminded_trade_ids["reminder-test"])


if __name__ == "__main__":
    unittest.main()