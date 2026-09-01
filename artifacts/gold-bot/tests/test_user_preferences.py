import json
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.analysis import cache
from src import mode_manager, user_preferences


class UserPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.preferences_file = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", delete=False
        )
        self.preferences_file.write("{}")
        self.preferences_file.flush()
        self.preferences_file.close()

        self.mode_file = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", delete=False
        )
        json.dump({"mode": "intraday", "timeframe": "H1"}, self.mode_file)
        self.mode_file.flush()
        self.mode_file.close()

        self.preferences_patch = patch.object(
            user_preferences, "PREFERENCES_PATH", self.preferences_file.name
        )
        self.mode_patch = patch.object(
            mode_manager, "_MODE_PATH", self.mode_file.name
        )
        self.preferences_patch.start()
        self.mode_patch.start()

    def tearDown(self):
        self.mode_patch.stop()
        self.preferences_patch.stop()

    def test_accounts_keep_independent_modes_and_timeframes(self):
        first = 101
        second = 202

        user_preferences.set_mode(first, "scalp")
        user_preferences.set_timeframe(first, "M15")

        self.assertEqual(user_preferences.get_mode(first), "scalp")
        self.assertEqual(user_preferences.get_timeframe(first), "M15")
        self.assertEqual(user_preferences.get_mode(second), "intraday")
        self.assertEqual(user_preferences.get_timeframe(second), "H1")

    def test_mode_change_only_adjusts_that_accounts_compatible_timeframe(self):
        first = 303
        second = 404

        user_preferences.set_timeframe(first, "M30")
        user_preferences.set_mode(first, "scalp")

        self.assertEqual(user_preferences.get_mode(first), "scalp")
        self.assertEqual(user_preferences.get_timeframe(first), "M15")
        self.assertEqual(user_preferences.get_mode(second), "intraday")
        self.assertEqual(user_preferences.get_timeframe(second), "H1")

    def test_analysis_cache_isolated_by_mode(self):
        cache.invalidate()
        results = [
            SimpleNamespace(analysis_mode="intraday"),
            SimpleNamespace(analysis_mode="scalp"),
        ]

        async def exercise():
            with patch(
                "src.analysis.engine.analyze",
                new=AsyncMock(side_effect=results),
            ) as analyze_mock:
                intraday = await cache.get_analysis("H1", mode="intraday")
                scalp = await cache.get_analysis("H1", mode="scalp")
                intraday_again = await cache.get_analysis("H1", mode="intraday")
                return analyze_mock, intraday, scalp, intraday_again

        analyze_mock, intraday, scalp, intraday_again = asyncio.run(exercise())
        self.assertEqual(analyze_mock.await_count, 2)
        self.assertIs(intraday, intraday_again)
        self.assertIsNot(intraday, scalp)
        cache.invalidate()


if __name__ == "__main__":
    unittest.main()