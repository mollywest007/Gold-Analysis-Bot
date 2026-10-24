"""Closed-market response and opening-time regressions."""
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram.ext import ApplicationHandlerStop

from src import market_hours
from src.handlers import market_gate


ET = ZoneInfo("America/New_York")


class MarketHoursTests(unittest.TestCase):
    def test_weekend_closed_time_names_exact_sunday_open(self):
        status = market_hours.market_status(
            datetime(2026, 10, 24, 12, 0, tzinfo=ET)
        )

        self.assertFalse(status["is_open"])
        self.assertEqual(
            status["next_open_at"], "2026-10-25T18:00:00-04:00"
        )
        self.assertEqual(
            status["next_open_label"],
            "Sunday, October 25 at 6:00 PM ET",
        )
        self.assertIn(status["next_open_label"], status["note"])

    def test_weekday_maintenance_names_same_day_reopen(self):
        status = market_hours.market_status(
            datetime(2026, 10, 27, 17, 30, tzinfo=ET)
        )

        self.assertFalse(status["is_open"])
        self.assertEqual(status["status_text"], "DAILY MAINTENANCE")
        self.assertEqual(
            status["next_open_at"], "2026-10-27T18:00:00-04:00"
        )
        self.assertEqual(
            status["next_open_label"],
            "Tuesday, October 27 at 6:00 PM ET",
        )

    def test_market_is_open_outside_scheduled_breaks(self):
        status = market_hours.market_status(
            datetime(2026, 10, 27, 16, 59, tzinfo=ET)
        )

        self.assertTrue(status["is_open"])
        self.assertIsNone(status["next_open_at"])


class MarketClosedGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.closed_status = market_hours.market_status(
            datetime(2026, 10, 24, 12, 0, tzinfo=ET)
        )
        self.open_status = market_hours.market_status(
            datetime(2026, 10, 27, 12, 0, tzinfo=ET)
        )

    async def test_dashboard_message_gets_closed_notice_and_menu(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=None,
            effective_message=message,
        )

        with patch.object(
            market_gate, "market_status", return_value=self.closed_status
        ):
            with self.assertRaises(ApplicationHandlerStop):
                await market_gate.stop_if_market_closed(update)

        message.reply_text.assert_awaited_once()
        args, kwargs = message.reply_text.await_args
        self.assertIn("MARKET CLOSED", args[0])
        self.assertIn("Sunday, October 25 at 6:00 PM ET", args[0])
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertIsNotNone(kwargs["reply_markup"])

    async def test_inline_dashboard_button_gets_reopening_alert(self):
        query = SimpleNamespace(answer=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_message=None,
        )

        with patch.object(
            market_gate, "market_status", return_value=self.closed_status
        ):
            with self.assertRaises(ApplicationHandlerStop):
                await market_gate.stop_if_market_closed(update)

        query.answer.assert_awaited_once()
        args, kwargs = query.answer.await_args
        self.assertIn("MARKET CLOSED", args[0])
        self.assertIn("Sunday, October 25 at 6:00 PM ET", args[0])
        self.assertTrue(kwargs["show_alert"])

    async def test_open_market_allows_handler_to_continue(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=None,
            effective_message=message,
        )

        with patch.object(
            market_gate, "market_status", return_value=self.open_status
        ):
            await market_gate.stop_if_market_closed(update)

        message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
