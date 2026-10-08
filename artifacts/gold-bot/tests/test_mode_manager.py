"""Regression tests for persisted mode and timeframe settings."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import mode_manager


class ModeManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path_patch = patch.object(mode_manager, "_MODE_PATH", self.tmp.name)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        os.unlink(self.tmp.name)

    def test_scalp_defaults_to_m15(self):
        with open(self.tmp.name, "w") as f:
            json.dump({"mode": "scalp"}, f)
        self.assertEqual(mode_manager.get_timeframe(), "M15")

    def test_selected_timeframe_survives_reload(self):
        mode_manager.set_mode("scalp")
        mode_manager.set_timeframe("M15")
        self.assertEqual(mode_manager.get_timeframe(), "M15")
        self.assertEqual(mode_manager._load_state()["timeframe"], "M15")

    def test_scalp_can_select_timeframes_above_m15(self):
        mode_manager.set_mode("scalp")
        self.assertEqual(mode_manager.set_timeframe("H1"), "H1")
        self.assertEqual(mode_manager.get_timeframe(), "H1")

    def test_swing_can_select_h1_h4_and_d1(self):
        mode_manager.set_mode("swing")
        self.assertEqual(
            mode_manager.get_mode_config().scan_timeframes,
            ["H1", "H4", "D1"],
        )
        self.assertEqual(mode_manager.set_timeframe("H1"), "H1")

    def test_switching_mode_keeps_compatible_timeframe(self):
        mode_manager.set_mode("scalp")
        mode_manager.set_timeframe("M15")
        mode_manager.set_mode("intraday")
        self.assertEqual(mode_manager.get_timeframe(), "M15")

    def test_invalid_timeframe_is_rejected(self):
        mode_manager.set_mode("scalp")
        with self.assertRaises(ValueError):
            mode_manager.set_timeframe("W1")

    def test_unknown_explicit_mode_does_not_fall_back_to_saved_mode(self):
        mode_manager.set_mode("intraday")

        with self.assertRaisesRegex(ValueError, "Unknown analysis mode"):
            mode_manager.get_mode_config("scapl")

        self.assertEqual(mode_manager.get_mode(), "intraday")

    def test_mode_menu_includes_combined_scalp_interval_option(self):
        labels = [cfg.label for cfg in mode_manager.list_modes()]
        self.assertIn("Scalp / Intra-hour", labels)


if __name__ == "__main__":
    unittest.main()