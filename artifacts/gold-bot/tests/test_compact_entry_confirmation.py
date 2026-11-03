import unittest
from types import SimpleNamespace

from src.utils.formatting import _entry_confirmation_level_rows


class CompactConfirmationTests(unittest.TestCase):
    def render(self, direction="SELL", price=4552, level=4442, **changes):
        levels = dict(
            available=True, direction=direction, current_price=price,
            confirmation_level=level, retest_level=4480,
            setup_type="BREAKOUT_RETEST", entry_status="WAIT",
        )
        levels.update(changes)
        return dict(_entry_confirmation_level_rows(SimpleNamespace(
            action="WAIT", entry_confirmation_levels=levels,
        )))

    def test_only_selected_setup_and_next_move(self):
        rows = self.render()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows["Current Price"], "4,552")
        self.assertIn("Drop below 4,442", rows["Waiting for"])
        self.assertNotIn("BUY", str(rows))
        self.assertNotIn("4,480", rows["Waiting for"])

    def test_refresh_changes_next_move(self):
        rows = self.render(price=4430)
        self.assertEqual(rows["Current Price"], "4,430")
        self.assertIn("Hold below 4,442", rows["Waiting for"])
        self.assertIn("Waiting for Confirmation", rows["Setup"])

    def test_buy_and_pattern_only_confirmation(self):
        self.assertIn("Rise above 4,520", self.render(
            direction="BUY", price=4450, level=4520,
        )["Waiting for"])
        rows = self.render(direction="BUY", level=None)
        self.assertEqual(rows["Waiting for"], "Bullish rejection at 4,480")

    def test_neutral_and_unavailable_do_not_invent_levels(self):
        self.assertEqual(self.render(direction="WAIT")["Signal"], "None")
        rows = self.render(available=False)
        self.assertNotIn("Current Price", rows)
        self.assertEqual(rows["Signal"], "None")
