"""Regression tests for the Telegram /active panel renderer."""
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.formatting import TG_MSG_LIMIT, active_trades_card


def _trade(**overrides):
    trade = {
        "direction": "SELL",
        "entry": 4580.90,
        "sl": 4608.00,
        "tp1": 4540.25,
        "tp2": 4513.15,
        "tp3": 4486.05,
        "timeframe": "M15",
        "mode": "scalp",
        "confidence": 95,
        "opened_at": 1000,
        "status": "open",
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
    }
    trade.update(overrides)
    return trade


class ActivePanelTests(unittest.TestCase):
    def test_unavailable_price_never_becomes_zero_pnl(self):
        with patch("src.utils.formatting.time.time", return_value=1100):
            card = active_trades_card([_trade()], 0.0)

        self.assertIn("Live Price : UNAVAILABLE", card)
        self.assertIn("Move        : unavailable (no live price)", card)
        self.assertNotIn("Now         : 0.00", card)
        self.assertNotIn("P&L", card)

    def test_direction_and_milestone_are_readable(self):
        with patch("src.utils.formatting.time.time", return_value=1100):
            card = active_trades_card(
                [_trade(direction="BUY", tp1_hit=True, status="tp1_hit")],
                4590.0,
            )

        self.assertIn("M15  BUY  |  TP1 HIT — next TP2", card)
        self.assertIn("Mode        : Scalp  |  Confidence: 95%", card)
        self.assertIn("Now         : 4,590.00", card)
        self.assertIn("Move        : +9.10  (IN PROFIT)", card)
        self.assertIn("TP1         : 4,540.25", card)
        self.assertIn("✓ HIT", card)

    def test_malformed_record_does_not_break_panel(self):
        card = active_trades_card([{"direction": "BUY"}], 4500.0)

        self.assertIn("Trade 1  |  DATA ERROR", card)
        self.assertIn("incomplete price levels", card)

    def test_panel_is_limited_to_telegram_message_size(self):
        trades = [_trade(timeframe=f"M{i}") for i in range(1, 11)]
        with patch("src.utils.formatting.time.time", return_value=1100):
            card = active_trades_card(trades, 4500.0)

        self.assertLessEqual(len(card), TG_MSG_LIMIT)
        self.assertTrue(card.endswith("</pre>"))


if __name__ == "__main__":
    unittest.main()