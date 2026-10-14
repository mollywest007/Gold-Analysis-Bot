import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine
from src.analysis.adaptive.config import PROFILES, resolve_timeframes
from src.analysis.adaptive.indicators import rsi
from src.analysis.adaptive.scoring import classify_signal, score_directions
from src.analysis.adaptive.structure import liquidity_sweep
from src.analysis.adaptive.validation import validate_plan
from src.analysis.market_data import OHLCVData


def candles(count=80, price=2000.0, timestamps=None):
    closes = [price + i * 0.1 for i in range(count)]
    return OHLCVData(
        opens=[value - 0.05 for value in closes],
        highs=[value + 0.2 for value in closes],
        lows=[value - 0.2 for value in closes],
        closes=closes,
        volumes=[100.0] * count,
        spot_price=closes[-1],
        timestamps=timestamps,
        fetched_at=1_700_000_000,
        price_source="spot",
        candle_source="spot_normalized_futures",
    )


class AdaptiveEngineTests(unittest.TestCase):
    def test_mode_roles_and_thresholds_match_requested_profiles(self):
        self.assertEqual(resolve_timeframes("scalp"), {
            "context": "M15", "setup": "M5", "refinement": "M1",
        })
        self.assertEqual(resolve_timeframes("intraday"), {
            "context": "H1", "setup": "M15", "refinement": "M5",
        })
        self.assertEqual(resolve_timeframes("swing"), {
            "context": "D1", "setup": "H4", "refinement": "H1",
        })
        self.assertEqual(resolve_timeframes("position"), {
            "context": "W1", "setup": "D1", "refinement": "H4",
        })
        self.assertEqual(
            [PROFILES[key].score_threshold for key in ("scalp", "intraday", "swing", "position")],
            [55, 58, 62, 65],
        )
        self.assertEqual(
            [PROFILES[key].minimum_rr for key in ("scalp", "intraday", "swing", "position")],
            [1.3, 1.5, 2.0, 2.0],
        )

    def test_unfinished_candle_is_removed_and_missing_timestamps_rejected(self):
        now = 1_700_000_000.0
        size = 36
        timestamps = [now - 20 - (size - i - 1) * 60 for i in range(size)]
        data = candles(size, timestamps=timestamps)
        completed, reason = engine._completed_strategy_candles(data, "M1", now)
        self.assertEqual(reason, "")
        self.assertEqual(len(completed), size - 1)
        self.assertEqual(completed.timestamps[-1], timestamps[-2])

        data.timestamps = []
        incomplete, reason = engine._completed_strategy_candles(data, "M1", now)
        self.assertIsNone(incomplete)
        self.assertIn("timestamps", reason)

    def test_early_signal_does_not_require_every_indicator_or_category(self):
        context = {"trend":"BULLISH", "bos":"NONE"}
        setup = {"trend":"UNCLEAR", "bos":"NONE", "resistance":None, "support":None}
        candidates = [{
            "direction":"BUY", "type":"support_resistance_reaction", "level":2000.0,
        }]
        features = {
            "last":2000.0, "ema20":None, "ema50":None, "macd":None,
            "rsi":None, "atr":1.0,
        }
        scores, breakdown, counts = score_directions(
            context, setup, None, candidates, features, "RANGE-BOUND", None,
            PROFILES["scalp"],
        )
        self.assertGreaterEqual(scores["BUY"], PROFILES["scalp"].early_threshold)
        self.assertLessEqual(scores["SELL"], 4)
        self.assertEqual(breakdown["SELL"]["entry_location"], 0)
        self.assertEqual(breakdown["BUY"]["momentum_trend"], 0)
        self.assertEqual(classify_signal(scores["BUY"], counts["BUY"], PROFILES["scalp"], True), "EARLY")

    def test_signal_categories_have_distinct_and_consistent_thresholds(self):
        profile = PROFILES["intraday"]
        self.assertEqual(classify_signal(49, 2, profile, True), "EARLY")
        self.assertEqual(classify_signal(60, 2, profile, True), "MODERATE")
        self.assertEqual(classify_signal(85, 4, profile, True), "STRONG")
        self.assertEqual(classify_signal(99, 4, profile, False), "WAIT")

    def test_liquidity_sweep_requires_a_range_pierce_and_close_back_inside(self):
        data = candles(count=40)
        prior_high = max(data.highs[-13:-1])
        data.highs[-1] = prior_high + .5
        data.opens[-1] = prior_high + .1
        data.closes[-1] = prior_high + .2  # remains outside; not a failed breakout
        data.lows[-1] = data.opens[-1] - .2
        self.assertIsNone(liquidity_sweep(data, lookback=12, atr_value=1.0))

        data = candles(count=40)
        prior_low = min(data.lows[-13:-1])
        data.lows[-1] = prior_low - .25
        data.opens[-1] = prior_low + .1
        data.closes[-1] = prior_low + .2
        data.highs[-1] = prior_low + .25
        self.assertEqual(liquidity_sweep(data, lookback=12, atr_value=1.0), "BUY")

    def test_missing_context_is_not_filled_or_scored_as_positive(self):
        setup = candles()
        result = __import__(
            "src.analysis.adaptive.engine", fromlist=["evaluate"]
        ).evaluate("scalp", "M5", {"M5": setup, "M1": candles()})
        self.assertEqual(result["action"], "NO TRADE")
        self.assertFalse(result["data_valid"])
        self.assertIn("Missing", result["rejection_reasons"][0])

    def test_plan_validation_requires_structural_stop_room_and_real_target(self):
        profile = PROFILES["scalp"]
        structure = {"lows":[(10, 99.0)], "highs":[(20, 102.0)]}
        valid = validate_plan("BUY", 100.0, 1.0, structure, None, profile, .05)
        self.assertTrue(valid["valid"])
        self.assertLess(valid["stop"], 99.0)
        self.assertGreater(valid["target"], 100.0)
        self.assertGreaterEqual(valid["rr"], profile.minimum_rr)

        too_tight = validate_plan(
            "BUY", 100.0, 1.0,
            {"lows":[(10, 99.95)], "highs":[(20, 102.0)]},
            None, profile,
        )
        self.assertFalse(too_tight["valid"])
        self.assertIn("stop", too_tight["rejection_reasons"][0].lower())

        obstructed = validate_plan(
            "BUY", 100.0, 1.0,
            {"lows":[(10, 99.0)], "highs":[(20, 101.0)]},
            None, PROFILES["position"],
        )
        self.assertFalse(obstructed["valid"])
        self.assertIn("obstructed", obstructed["rejection_reasons"][0].lower())

    def test_short_indicator_input_stays_unavailable(self):
        self.assertIsNone(rsi([1.0, 2.0, 3.0], 14))


if __name__ == "__main__":
    unittest.main()
