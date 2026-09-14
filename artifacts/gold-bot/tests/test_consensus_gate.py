"""Regression tests for consistent strict consensus gating."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine


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
