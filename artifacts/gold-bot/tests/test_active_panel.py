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
        self.assertIn("Price Move  : unavailable (no live price)", card)
        self.assertNotIn("Now         : 0.00", card)
        self.assertNotIn("P&L", card)

    def test_direction_and_milestone_are_readable(self):
        with patch("src.utils.formatting.time.time", return_value=1100):
            card = active_trades_card(
                [
                    _trade(
                        direction="BUY",
                        sl=4553.80,
                        tp1=4621.55,
                        tp2=4648.65,
                        tp3=4675.75,
                        tp1_hit=True,
                        status="tp1_hit",
                    )
                ],
                4590.0,
            )

        self.assertIn("M15  BUY  |  TP1 HIT — next TP2", card)
        self.assertIn("Mode        : Scalp  |  Confidence: 95%", card)
        self.assertIn("Now         : 4,590.00", card)
        self.assertIn("Price Move  : +9.10  (IN PROFIT)", card)
        self.assertIn("TP1         : 4,621.55", card)
        self.assertIn("✓ recorded", card)

    def test_exact_saved_entry_drives_move_and_all_target_distances(self):
        card = active_trades_card(
            [
                _trade(
                    direction="BUY",
                    entry=4441.53,
                    sl=4428.30,
                    tp1=4454.76,
                    tp2=4468.00,
                    tp3=4481.23,
                )
            ],
            4450.53,
        )

        self.assertIn("Market Entry: 4,441.53  (tracked basis)", card)
        self.assertIn("Price Move  : +9.00  (IN PROFIT)", card)
        self.assertIn("SL          : 4,428.30  (distance 13.23)", card)
        self.assertIn("TP1         : 4,454.76  (distance 13.23", card)
        self.assertIn("TP2         : 4,468.00  (distance 26.47", card)
        self.assertIn("TP3         : 4,481.23  (distance 39.70", card)

    def test_crossed_target_is_not_presented_as_a_pip_conversion(self):
        with patch("src.utils.formatting.time.time", return_value=1100):
            card = active_trades_card([_trade()], 4456.40)

        self.assertIn("TP3 REACHED — tracker update pending", card)
        self.assertIn("Price Move  : +124.50  (IN PROFIT)", card)
        self.assertIn("Unit        : XAU/USD price difference (not broker pips)", card)
        self.assertIn("TP1         : 4,540.25  (distance 40.65", card)
        self.assertIn("⚠ crossed", card)
        self.assertNotIn("124.50 pips", card)

    def test_limit_and_market_entries_are_shown_separately(self):
        card = active_trades_card(
            [
                _trade(
                    entry=4431.20,
                    limit_entry=4444.46,
                    sl=4450.00,
                    tp1=4418.00,
                    tp2=4405.00,
                    tp3=4392.00,
                )
            ],
            4426.80,
        )

        self.assertIn("Market Entry: 4,431.20  (tracked basis)", card)
        self.assertIn("Limit Entry : 4,444.46  (optional pullback level)", card)
        self.assertIn("Price Move  : +4.40  (IN PROFIT)", card)
        self.assertIn("Limit Move  : +17.66  (from limit level; fill not confirmed)", card)
        self.assertIn("Unit        : XAU/USD price difference (not broker pips)", card)

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