"""Regression tests for consistent strict consensus gating."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine
from src.analysis.market_data import OHLCVData


def _ohlcv():
    closes = [100 + (i * 0.2) + ((i % 5) * 0.1) for i in range(120)]
    return OHLCVData(
        opens=[value - 0.2 for value in closes],
        highs=[value + 0.5 for value in closes],
        lows=[value - 0.5 for value in closes],
        closes=closes,
        volumes=[1000] * len(closes),
        spot_price=closes[-1],
    )


class ConsensusGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_timeframe_does_not_request_full_consensus_by_default(self):
        local_analysis = object()

        with patch.object(
            engine,
            "_analyze_single",
            new=AsyncMock(return_value=local_analysis),
        ) as analyze_single:
            result = await engine.analyze("M5", mode="scalp")

        self.assertIs(result, local_analysis)
        analyze_single.assert_awaited_once_with(
            "M5",
            mode="scalp",
            use_higher_timeframe_context=False,
        )

    async def test_explicit_context_flags_cannot_reintroduce_an_entry_gate(self):
        local_analysis = object()

        with patch.object(
            engine,
            "_analyze_single",
            new=AsyncMock(return_value=local_analysis),
        ) as analyze_single, patch.object(
            engine,
            "analyze_multi_timeframe",
            new=AsyncMock(side_effect=AssertionError("MTF must not be fetched")),
        ):
            result = await engine.analyze(
                "H1",
                mode="intraday",
                include_full_context=True,
                use_higher_timeframe_context=True,
            )

        self.assertIs(result, local_analysis)
        analyze_single.assert_awaited_once_with(
            "H1",
            mode="intraday",
            use_higher_timeframe_context=False,
        )

    async def test_single_analysis_fetches_only_the_selected_timeframe(self):
        with patch.object(
            engine,
            "fetch_ohlcv",
            new=AsyncMock(return_value=_ohlcv()),
        ) as fetch_ohlcv, patch.object(
            engine,
            "fetch_intermarket_snapshot",
            new=AsyncMock(return_value={}),
        ), patch.object(
            engine,
            "_get_htf_bias",
            new=AsyncMock(side_effect=AssertionError("HTF must not be fetched")),
        ):
            result = await engine._analyze_single(
                "M15",
                mode="scalp",
                use_higher_timeframe_context=True,
            )

        self.assertEqual(result.timeframe, "M15")
        self.assertEqual(result.htf_bias, "Not used")
        self.assertEqual(result.ltf_trends, {})
        fetch_ohlcv.assert_awaited_once_with("M15")
