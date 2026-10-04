import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import backtest, market_data
from src.analysis.market_data import OHLCVData
from src.analysis.engine import compute_atr, compute_rsi


class HistoricalCandleFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_historical_fetch_keeps_only_completed_real_candles(self):
        data = OHLCVData(
            [100.0, 101.0, 102.0],
            [101.0, 102.0, 103.0],
            [99.0, 100.0, 101.0],
            [100.5, 101.5, 102.5],
            [10.0, 10.0, 10.0],
            timestamps=[0.0, 900.0, 1800.0],
            fetched_at=1800.0,
        )
        with patch.object(
            market_data, "_fetch_ohlcv_raw", new=AsyncMock(return_value=data)
        ) as fetch:
            result = await market_data.fetch_historical_ohlcv(
                "M15", now=1800.0
            )

        fetch.assert_awaited_once_with(
            "M15", data_range="60d", include_live_spot=False
        )
        self.assertEqual(result.closes, [100.5, 101.5])
        self.assertEqual(result.timestamps, [0.0, 900.0])
        self.assertFalse(result.is_simulated)

    async def test_historical_fetch_rejects_simulated_data(self):
        simulated = OHLCVData(
            [100.0] * 100,
            [101.0] * 100,
            [99.0] * 100,
            [100.0] * 100,
            [1.0] * 100,
            is_simulated=True,
            timestamps=[float(i * 900) for i in range(100)],
        )
        with patch.object(
            market_data, "_fetch_ohlcv_raw", new=AsyncMock(return_value=simulated)
        ):
            result = await market_data.fetch_historical_ohlcv(
                "M15", now=200_000.0
            )
        self.assertIsNone(result)


class ReplayTests(unittest.TestCase):
    @staticmethod
    def _data():
        count = 105
        opens = [100.0] * count
        highs = [100.2] * count
        lows = [99.8] * count
        closes = [100.0] * count
        volumes = [10.0] * count
        timestamps = [1_700_000_000.0 + 60 * i for i in range(count)]
        return OHLCVData(
            opens,
            highs,
            lows,
            closes,
            volumes,
            timestamps=timestamps,
        )

    def _run_one_signal(self, *, stop_and_target_same_candle=False):
        data = self._data()
        data.highs[80] = 102.1
        data.lows[80] = 98.5 if stop_and_target_same_candle else 99.8
        calls = 0

        def one_signal(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "MODERATE ENTRY",
                    "direction": "BUY",
                    "stop_loss": 99.0,
                    "tp1": 102.0,
                    "rr": 2.0,
                }
            return {"status": "WAIT"}

        with patch.object(
            backtest.engine,
            "_momentum_pullback_decision",
            side_effect=one_signal,
        ):
            return backtest.replay_momentum_pullback(data, "scalp", "M1")

    def test_replay_enters_at_next_open_and_records_tp1_first(self):
        report = self._run_one_signal()

        self.assertEqual(report["generated_signals"], 1)
        self.assertEqual(report["closed_trades"], 1)
        self.assertEqual(report["tp1_first"], 1)
        self.assertEqual(report["sl_first"], 0)
        self.assertEqual(report["average_planned_rr"], 2.0)
        self.assertEqual(report["average_entry_delay_minutes"], 1.0)
        self.assertFalse(report["sample_sufficient"])
        formatted = backtest.format_backtest_report(report)
        self.assertIn("performance metrics are withheld", formatted)
        self.assertNotIn("TP1 first:", formatted)

    def test_replay_counts_stop_first_when_both_levels_touch_same_candle(self):
        report = self._run_one_signal(stop_and_target_same_candle=True)

        self.assertEqual(report["sl_first"], 1)
        self.assertEqual(report["tp1_first"], 0)
        self.assertEqual(report["longest_losing_streak"], 1)
        self.assertEqual(
            report["loss_setup_types"], {"Fast momentum pullback": 1}
        )

    def test_replay_rejects_simulated_candles_and_wrong_mode_timeframe(self):
        data = self._data()
        data.is_simulated = True
        with self.assertRaisesRegex(ValueError, "real historical candles"):
            backtest.replay_momentum_pullback(data, "scalp", "M1")

        data.is_simulated = False
        with self.assertRaisesRegex(ValueError, "not scanned by Scalp"):
            backtest.replay_momentum_pullback(data, "scalp", "H1")

    def test_fast_indicator_series_match_live_engine_values(self):
        data = self._data()
        closes = [
            100.0 + (index % 11) * 0.2 + index * 0.01
            for index in range(len(data.closes))
        ]
        highs = [value + 0.4 for value in closes]
        lows = [value - 0.3 for value in closes]
        ema = backtest._seeded_ema_series(closes, 20)
        rsi = backtest._rsi_series(closes, 14)
        atr = backtest._atr_series(highs, lows, closes, 14)

        for index in range(20, len(closes)):
            self.assertAlmostEqual(
                ema[index], backtest.engine._ema(closes[:index + 1], 20), places=8
            )
            self.assertAlmostEqual(
                rsi[index], compute_rsi(closes[:index + 1], 14), places=8
            )
            self.assertAlmostEqual(
                atr[index],
                compute_atr(highs[:index + 1], lows[:index + 1], closes[:index + 1], 14),
                places=4,
            )


if __name__ == "__main__":
    unittest.main()