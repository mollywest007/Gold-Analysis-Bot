"""Regression tests for the Gemini chart-analysis request construction."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.chart_analysis import _GEMINI_MODEL, _GEMINI_URL, _PROMPT


class ChartAnalysisPromptTests(unittest.TestCase):
    def test_gemini_vision_endpoint_uses_current_model(self):
        self.assertEqual(_GEMINI_MODEL, "gemini-3.6-flash")
        self.assertIn("models/gemini-3.6-flash:generateContent", _GEMINI_URL)

    def test_json_schema_braces_survive_prompt_formatting(self):
        prompt = _PROMPT.format(open_trade_section="")

        self.assertIn('"bias"', prompt)
        self.assertIn('"open_trade_notes"', prompt)
        self.assertIn("Return ONLY a single valid JSON object", prompt)
        self.assertNotIn("{open_trade_section}", prompt)


if __name__ == "__main__":
    unittest.main()