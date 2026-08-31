"""Regression tests for market-data alignment and analysis evidence."""
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine
from src.analysis import market_data


class AnalysisIntegrityTests(unittest.TestCase):
    def test_ohlcv_cleanup_keeps_columns_and_timestamps_aligned(self):
        quote = {
            "open": [100.0, 101.0, None, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [10.0, 20.0, 30.0, 40.0],
        }

        opens, highs, lows, closes, volumes, timestamps = (
            market_data._aligned_ohlcv_rows(quote, [1, 2, 3, 4])
        )

        self.assertEqual(opens, [100.0, 101.0, 103.0])
        self.assertEqual(highs, [101.0, 102.0, 104.0])
        self.assertEqual(lows, [99.0, 100.0, 102.0])
        self.assertEqual(closes, [100.5, 101.5, 103.5])
        self.assertEqual(volumes, [10.0, 20.0, 40.0])
        self.assertEqual(timestamps, [1.0, 2.0, 4.0])

    def test_spot_source_selection_accepts_swissquote_when_goldapi_fails(self):
        self.assertEqual(
            market_data._first_valid_spot([RuntimeError("down"), 4455.25]),
            4455.25,
        )
        self.assertIsNone(market_data._first_valid_spot([None, 0.0, 250.0]))

    def test_support_one_is_nearest_support(self):
        highs = [101.0] * 25
        lows = [100.0] * 25
        closes = [100.0] * 25
        lows[5] = 95.0
        lows[15] = 90.0

        _, _, support1, support2 = engine.find_sr_levels(
            highs, lows, closes, price=100.0, atr=1.0, timeframe="M1"
        )

        self.assertEqual(support1, 95.0)
        self.assertEqual(support2, 90.0)

    def test_new_directional_candlestick_patterns_are_scored(self):
        bullish = (
            "Three Inside Up",
            "Bullish Kicker",
            "Bullish Abandoned Baby",
            "Bullish Belt Hold",
            "Bullish Counterattack",
        )
        bearish = (
            "Three Inside Down",
            "Bearish Kicker",
            "Bearish Abandoned Baby",
            "Bearish Belt Hold",
            "Bearish Counterattack",
        )

        for pattern in bullish:
            self.assertEqual(engine.candle_signal(pattern), "BUY", pattern)
        for pattern in bearish:
            self.assertEqual(engine.candle_signal(pattern), "SELL", pattern)

    def test_exact_vote_tie_cannot_become_a_directional_signal(self):
        self.assertEqual(
            engine._select_direction(
                buy_score=0.70,
                sell_score=0.60,
                buy_votes=5,
                sell_votes=5,
                min_votes=2,
            ),
            "NEUTRAL",
        )
        self.assertEqual(
            engine._select_direction(
                buy_score=0.70,
                sell_score=0.60,
                buy_votes=6,
                sell_votes=5,
                min_votes=2,
            ),
            "BUY",
        )


class CachedPriceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_candles_receive_a_fresh_spot_snapshot(self):
        data = market_data.OHLCVData(
            [100.0] * 30,
            [101.0] * 30,
            [99.0] * 30,
            [100.0] * 30,
            [1.0] * 30,
            spot_price=100.0,
        )
        cache = {"M15": (data, time.time())}

        with patch.object(market_data, "_ohlcv_cache", cache), \
             patch.object(
                 market_data,
                 "get_gold_price",
                 new=AsyncMock(return_value=101.25),
             ):
            result = await market_data.fetch_ohlcv("M15")

        self.assertIs(result, data)
        self.assertEqual(result.price, 101.25)


if __name__ == "__main__":
    unittest.main()