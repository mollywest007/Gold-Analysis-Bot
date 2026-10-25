"""Regression tests for the Gemini chart-analysis request construction."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.chart_analysis import (
    _GEMINI_MODEL,
    _GEMINI_URL,
    _PROMPT,
    _validate_chart_trade_levels,
)


class ChartAnalysisPromptTests(unittest.TestCase):
    def test_gemini_vision_endpoint_uses_current_model(self):
        self.assertEqual(_GEMINI_MODEL, "gemini-3.6-flash")
        self.assertIn("models/gemini-3.6-flash:generateContent", _GEMINI_URL)

    def test_json_schema_braces_survive_prompt_formatting(self):
        prompt = _PROMPT.format(
            mode_instructions="Selected mode: Intraday",
            open_trade_section="",
        )

        self.assertIn('"bias"', prompt)
        self.assertIn('"open_trade_notes"', prompt)
        self.assertIn("Selected mode: Intraday", prompt)
        self.assertIn("Return ONLY a single valid JSON object", prompt)
        self.assertNotIn("{mode_instructions}", prompt)
        self.assertNotIn("{open_trade_section}", prompt)
        self.assertIn('"atr14"', prompt)
        self.assertIn("deterministic mode-specific level", prompt.lower())

    def test_chart_trade_levels_use_deterministic_mode_validation_for_both_sides(self):
        cases = (
            (
                "BUY",
                {
                    "entry": 100.0,
                    "invalidation": 99.0,
                    "atr14": 1.0,
                    "key_support": [98.5],
                    "key_resistance": [103.0],
                },
            ),
            (
                "SELL",
                {
                    "entry": 100.0,
                    "invalidation": 101.0,
                    "atr14": 1.0,
                    "key_support": [97.0],
                    "key_resistance": [101.5],
                },
            ),
        )
        for direction, values in cases:
            with self.subTest(direction=direction):
                plan, error = _validate_chart_trade_levels(
                    {"direction": direction, **values}, "scalp", "M5"
                )
                self.assertIsNone(error)
                self.assertTrue(plan)
                self.assertGreaterEqual(plan["rr"], 1.5)
                if direction == "BUY":
                    self.assertLess(plan["stop_loss"], plan["invalidation_level"])
                    self.assertLess(plan["invalidation_level"], plan["entry"])
                    self.assertGreater(plan["tp1"], plan["entry"])
                else:
                    self.assertGreater(plan["stop_loss"], plan["invalidation_level"])
                    self.assertGreater(plan["invalidation_level"], plan["entry"])
                    self.assertLess(plan["tp1"], plan["entry"])

    def test_chart_trade_levels_require_visible_atr_and_enough_structural_room(self):
        missing_atr, atr_error = _validate_chart_trade_levels(
            {
                "direction": "BUY",
                "entry": 100.0,
                "invalidation": 99.0,
                "key_resistance": [103.0],
            },
            "scalp",
            "M5",
        )
        self.assertIsNone(missing_atr)
        self.assertIn("ATR14", atr_error)

        insufficient, room_error = _validate_chart_trade_levels(
            {
                "direction": "BUY",
                "entry": 100.0,
                "invalidation": 99.0,
                "atr14": 1.0,
                "key_support": [98.5],
                "key_resistance": [101.5],
            },
            "scalp",
            "M5",
        )
        self.assertIsNone(insufficient)
        self.assertIn("reward-to-risk", room_error.lower())


if __name__ == "__main__":
    unittest.main()