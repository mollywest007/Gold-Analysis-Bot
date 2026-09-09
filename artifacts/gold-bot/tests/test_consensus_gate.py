"""Regression tests for consistent strict consensus gating."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine


class ConsensusGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_lower_scalp_timeframe_keeps_local_report_under_strict_gate(self):
        consensus = {
            "analyses": {},
            "final_direction": "WAIT",
        }
        local_analysis = object()

        with patch.object(
            engine,
            "analyze_multi_timeframe",
            new=AsyncMock(return_value=consensus),
        ), patch.object(
            engine,
            "_analyze_single",
            new=AsyncMock(return_value=local_analysis),
        ), patch.object(
            engine,
            "_apply_multi_timeframe_consensus",
            side_effect=lambda analysis, report: analysis,
        ) as apply_gate:
            result = await engine.analyze("M5", mode="scalp")

        self.assertIs(result, local_analysis)
        apply_gate.assert_called_once_with(local_analysis, consensus)
