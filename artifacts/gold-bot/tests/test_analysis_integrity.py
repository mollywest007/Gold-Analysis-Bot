"""Regression tests for market-data alignment and analysis evidence."""
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine
from src.analysis import market_data
from src.analysis.engine import MarketAnalysis
from src.handlers import callbacks
from src.utils import formatting


class AnalysisIntegrityTests(unittest.TestCase):
    def test_ema_and_rsi_alignment_without_swings_cannot_create_a_trade(self):
        closes = [100.0 + index * 0.1 for index in range(24)]
        opens = [close - 0.02 for close in closes]
        highs = [close + 0.05 for close in closes]
        lows = [close - 0.05 for close in closes]

        with patch.object(engine, "_local_swings", return_value=([], [])):
            result = engine._momentum_pullback_decision(
                opens, highs, lows, closes, closes[-1],
                ema20=closes[-1] - 0.1,
                ema50=closes[-1] - 0.2,
                atr=1.0,
                rsi=60.0,
                previous_rsi=58.0,
            )

        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["direction"], "NEUTRAL")
        self.assertEqual(result["tp1"], 0.0)

    @staticmethod
    def _bullish_pullback_candles():
        size = 40
        closes = [109.8] * size
        opens = [109.7] * size
        highs = [110.0] * size
        lows = [109.5] * size
        highs[32], lows[32] = 112.55, 109.0
        opens[32], closes[32] = 110.4, 111.2
        lows[31] = 105.0
        opens[35], closes[35] = 110.7, 110.5
        opens[36], closes[36] = 110.4, 110.2
        highs[36], lows[36] = 110.3, 109.85
        opens[37], closes[37] = 109.95, 110.05
        highs[37], lows[37] = 110.2, 109.68
        opens[38], closes[38] = 110.1, 110.0
        highs[38], lows[38] = 110.25, 109.8
        opens[39], closes[39] = 110.0, 110.4
        highs[39], lows[39] = 110.6, 109.9
        return opens, highs, lows, closes

    def test_momentum_pullback_uses_structure_and_never_invents_targets(self):
        opens, highs, lows, closes = self._bullish_pullback_candles()
        swings = (
            [(20, 109.0), (32, 112.55)],
            [(22, 105.0), (35, 109.7)],
        )

        with patch.object(engine, "_local_swings", return_value=swings):
            result = engine._momentum_pullback_decision(
                opens, highs, lows, closes,
                price=110.4,
                ema20=110.0,
                ema50=111.5,  # EMA50 opposition must not veto local structure.
                atr=2.5,
                rsi=55.0,
                previous_rsi=54.0,
            )

        self.assertEqual(result["status"], "MODERATE ENTRY")
        self.assertEqual(result["direction"], "BUY")
        self.assertLess(result["stop_loss"], 110.4)
        self.assertGreater(result["tp1"], 110.4)
        self.assertEqual(result["tp2"], 0.0)
        self.assertEqual(result["tp3"], 0.0)
        self.assertGreaterEqual(result["rr"], 1.0)

    def test_pullback_without_minor_swing_close_stays_developing(self):
        opens, highs, lows, closes = self._bullish_pullback_candles()
        closes[-1] = 110.25
        swings = (
            [(20, 109.0), (32, 112.55)],
            [(22, 105.0), (35, 109.7)],
        )

        with patch.object(engine, "_local_swings", return_value=swings):
            result = engine._momentum_pullback_decision(
                opens, highs, lows, closes, 110.25, 110.0, 111.5,
                2.5, 55.0, 54.0,
            )

        self.assertEqual(result["status"], "DEVELOPING")
        self.assertEqual(result["direction"], "BUY")
        self.assertEqual(result["tp1"], 0.0)

    def test_strong_rsi_disagreement_vetoes_the_entry(self):
        opens, highs, lows, closes = self._bullish_pullback_candles()
        swings = (
            [(20, 109.0), (32, 112.55)],
            [(22, 105.0), (35, 109.7)],
        )

        with patch.object(engine, "_local_swings", return_value=swings):
            result = engine._momentum_pullback_decision(
                opens, highs, lows, closes, 110.4, 110.0, 111.5,
                2.5, 40.0, 42.0,
            )

        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["direction"], "BUY")
        self.assertIn("disagrees", result["setup"])

    def test_neutral_rsi_does_not_add_a_second_entry_confirmation_gate(self):
        opens, highs, lows, closes = self._bullish_pullback_candles()
        swings = (
            [(20, 109.0), (32, 112.55)],
            [(22, 105.0), (35, 109.7)],
        )

        with patch.object(engine, "_local_swings", return_value=swings):
            result = engine._momentum_pullback_decision(
                opens, highs, lows, closes, 110.4, 110.0, 111.5,
                2.5, 47.0, 46.0,
            )

        self.assertEqual(result["status"], "MODERATE ENTRY")
        self.assertEqual(result["direction"], "BUY")
        self.assertTrue(
            any("no strong disagreement" in item for item in result["quality_checks"])
        )

    def test_refresh_analysis_commands_refresh_live_quote_without_dropping_candles(self):
        with patch.object(market_data, "invalidate_cache") as invalidate, \
             patch.object(market_data, "invalidate_price_cache") as refresh_price:
            callbacks._invalidate_refresh_market_data("analyze")

        invalidate.assert_not_called()
        refresh_price.assert_called_once_with()

    def test_non_market_refresh_commands_keep_market_data_cache(self):
        with patch.object(market_data, "invalidate_cache") as invalidate:
            callbacks._invalidate_refresh_market_data("news")

        invalidate.assert_not_called()

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

    def test_higher_timeframe_aggregation_discards_partial_utc_buckets(self):
        data = market_data.OHLCVData(
            opens=list(range(100, 109)),
            highs=list(range(101, 110)),
            lows=list(range(99, 108)),
            closes=list(range(100, 109)),
            volumes=[1.0] * 9,
            timestamps=[3600 * i for i in range(1, 10)],
        )

        result = market_data._aggregate_bars(data, 4)

        # 01:00–04:00 is a partial UTC bucket, and the final bucket is also
        # incomplete, so neither may be treated as H4.
        self.assertEqual(result.timestamps, [14400.0])
        self.assertEqual(result.opens, [103])
        self.assertEqual(result.closes, [106])

    def test_liquidity_sweep_uses_the_prior_range(self):
        from src.analysis import institutional

        highs = [100.0] * 20 + [105.0]
        lows = [99.0] * 20 + [95.0]
        closes = [99.5] * 20 + [100.5]
        opens = [99.5] * 21

        smc = institutional._smc(
            opens, highs, lows, closes, [100.0] * 21, 1.0,
            {"trend": "RANGING", "choch": "NONE"},
        )

        self.assertEqual(smc["liquidity_sweep"], "BULLISH")

    def test_spot_source_selection_accepts_swissquote_when_goldapi_fails(self):
        self.assertEqual(
            market_data._first_valid_spot([RuntimeError("down"), 4455.25]),
            4455.25,
        )
        self.assertIsNone(market_data._first_valid_spot([None, 0.0, 250.0]))

    def test_spot_source_selection_rejects_materially_inconsistent_quotes(self):
        self.assertIsNone(
            market_data._first_valid_spot([2350.0, 4480.0])
        )
        self.assertIsNone(
            market_data._first_valid_spot([2350.0], reference=4480.0)
        )
        self.assertEqual(
            market_data._first_valid_spot([2350.0, 4480.0], reference=4480.0),
            4480.0,
        )

    def test_executable_gold_quote_requires_a_fresh_spread_consistent_bbo(self):
        snapshot_time = time.time()
        with patch.object(
            market_data,
            "_price_bbo_cache",
            (4455.0, 4455.2, snapshot_time),
        ):
            quote = market_data.get_cached_gold_quote(4455.1)

        self.assertEqual(quote["symbol"], "XAU/USD")
        self.assertEqual(quote["bid"], 4455.0)
        self.assertEqual(quote["ask"], 4455.2)
        self.assertTrue(quote["spread_available"])

        with patch.object(
            market_data,
            "_price_bbo_cache",
            (4455.0, 4455.2, snapshot_time - market_data.PRICE_TTL - 3),
        ):
            stale = market_data.get_cached_gold_quote(4455.1)
        self.assertIsNone(stale["bid"])
        self.assertIsNone(stale["ask"])
        self.assertFalse(stale["spread_available"])

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

    def test_analysis_card_shows_unconfirmed_directional_indication(self):
        analysis = MarketAnalysis(
            price=4350.0,
            timeframe="H1",
            bias="Bearish",
            trend="Bearish",
            strength="Moderate",
            momentum="Bearish",
            confidence=75,
            entry=4350.0,
            stop_loss=4370.0,
            tp1=4310.0,
            tp2=4280.0,
            rr_ratio=2.0,
            action="WAIT",
            wait_reason="<confirmation> required",
            resistance1=4370.0,
            resistance2=4400.0,
            support1=4310.0,
            support2=4280.0,
            breakout=False,
            reversal=False,
            liquidity_zone="4310.0 — 4330.0",
            directional_indication="SELL",
            setup_grade="B",
        )

        with patch.object(
            formatting,
            "market_status",
            return_value={"is_open": True, "note": "test"},
        ):
            card = formatting.analysis_card(analysis)

        self.assertIn("WHAT TO DO", card)
        self.assertIn("Direction : SELL", card)
        self.assertIn("Action    : WAIT", card)
        self.assertIn("Conflict  : None — all clear", card)
        self.assertIn("ENTRY CONFIRMATION", card)
        self.assertIn("Waiting for:", card)
        self.assertNotIn("Awaiting confirmation", card)
        self.assertNotIn("Engine note", card)

    def test_analysis_card_uses_selected_timeframe_only(self):
        analysis = MarketAnalysis(
            price=4350.0,
            timeframe="M15",
            bias="Bullish",
            trend="Bullish",
            strength="Strong",
            momentum="High",
            confidence=82,
            entry=4350.0,
            stop_loss=4335.0,
            tp1=4380.0,
            tp2=4400.0,
            tp3=4420.0,
            rr_ratio=2.0,
            action="BUY",
            wait_reason="",
            resistance1=4360.0,
            resistance2=4380.0,
            support1=4335.0,
            support2=4310.0,
            breakout=False,
            reversal=False,
            liquidity_zone="4335.0 — 4345.0",
            directional_indication="BUY",
            confidence_score=80,
            institutional_report={
                "data_quality": "REAL_OHLCV",
                "direction": "BUY",
                "multi_timeframe": {
                    "directions": {"D1": "SELL", "H4": "SELL", "H1": "WAIT", "M15": "BUY"},
                    "scores": {"D1": 20, "H4": 20, "H1": 20, "M15": 80},
                    "aligned": False,
                },
            },
        )

        with patch.object(
            formatting,
            "market_status",
            return_value={"is_open": True, "note": "test"},
        ):
            card = formatting.analysis_card(analysis)

        self.assertIn("WHAT TO DO", card)
        self.assertIn("ENTRY CONFIRMATION", card)
        self.assertIn("Entry :", card)
        self.assertIn("Alert :", card)
        self.assertNotIn("Timeframe", card)
        self.assertNotIn("Mode scope", card)
        self.assertNotIn("HTF Bias", card)
        self.assertNotIn("M15 analysis", card)
        self.assertNotIn("D1", card)
        self.assertNotIn("H4", card)
        self.assertNotIn("MTF", card)


class CachedPriceTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_refresh_bypasses_recent_cached_spot(self):
        with patch.object(
            market_data,
            "_price_cache",
            (4177.0, time.time()),
        ), patch.object(
            market_data,
            "_fetch_goldapi",
            new=AsyncMock(return_value=4178.0),
        ) as goldapi, patch.object(
            market_data,
            "_fetch_swissquote",
            new=AsyncMock(return_value=4178.2),
        ) as swissquote, patch.object(
            market_data,
            "_first_valid_spot",
            return_value=4178.1,
        ):
            result = await market_data.get_gold_price(force_refresh=True)

        self.assertEqual(result, 4178.1)
        goldapi.assert_awaited_once()
        swissquote.assert_awaited_once()

    async def test_inconsistent_spot_sources_fall_back_without_returning_outlier(self):
        with patch.object(
            market_data,
            "_fetch_goldapi",
            new=AsyncMock(return_value=2350.0),
        ), patch.object(
            market_data,
            "_fetch_swissquote",
            new=AsyncMock(return_value=4480.0),
        ), patch.object(
            market_data,
            "_fetch_yf_last_close",
            new=AsyncMock(return_value=4526.4),
        ), patch.object(market_data, "_price_cache", (0.0, 0.0)):
            result = await market_data.get_gold_price()

        self.assertEqual(result, 4526.4)

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