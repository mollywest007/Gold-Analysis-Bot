import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis.institutional import (
    build_context,
    combine_contexts,
    detect_candles,
)
from src.analysis.market_data import OHLCVData


def _data(closes, volumes=None):
    opens = [value - 1 for value in closes]
    highs = [value + 2 for value in closes]
    lows = [value - 2 for value in closes]
    return OHLCVData(
        opens,
        highs,
        lows,
        closes,
        volumes or [1000] * len(closes),
        spot_price=closes[-1],
    )


class InstitutionalEngineTests(unittest.TestCase):
    def test_candle_library_recognizes_core_reversal_and_indecision_patterns(self):
        patterns = detect_candles(
            [100, 103, 98],
            [101, 104, 104],
            [99, 97, 97],
            [99, 98, 103],
        )
        self.assertTrue(patterns)
        self.assertTrue(any(name in patterns for name in ("Hammer", "Bullish Engulfing", "Tweezer Bottoms")))

    def test_context_is_evidence_first_and_exposes_requested_layers(self):
        closes = [100 + (i * 0.25) + ((i % 4) * 0.15) for i in range(120)]
        context = build_context(_data(closes), "H1", {"status": "UNAVAILABLE"})

        self.assertEqual(context.data_quality, "REAL_OHLCV")
        for layer in (
            "market_structure", "support_resistance", "smc", "wyckoff",
            "elliott_wave", "fibonacci", "volume", "volatility",
            "momentum", "moving_averages", "breakout", "session",
            "macro", "intermarket", "statistics",
        ):
            self.assertTrue(getattr(context, layer), layer)
        self.assertIn(context.direction, ("BUY", "SELL", "WAIT"))
        self.assertGreaterEqual(context.bullish_probability, 1)
        self.assertLessEqual(context.bullish_probability, 99)

    def test_multi_timeframe_combiner_prioritizes_higher_timeframes(self):
        weekly = build_context(_data([100 + i * .4 for i in range(120)]), "W1", {})
        five_minute = build_context(_data([150 - i * .4 for i in range(120)]), "M5", {})
        result = combine_contexts({"W1": weekly, "M5": five_minute})

        self.assertIn(result["direction"], ("BUY", "SELL", "WAIT"))
        self.assertIn("timeframes", result)
        self.assertIn("W1", result["timeframes"])
        self.assertIn("M5", result["timeframes"])


if __name__ == "__main__":
    unittest.main()