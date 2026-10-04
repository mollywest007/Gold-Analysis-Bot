import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import alerts
from src.analysis import engine
from src.analysis.engine import _momentum_pullback_decision, _structure_targets
from src.analysis.modes import MODES
from src.chart_analysis import _mode_prompt_instructions, _resolve_chart_scope
from src.handlers import callbacks, commands


class ModeSpecificProfileTests(unittest.TestCase):
    def test_range_trap_sensitivity_is_mode_specific(self):
        trap_settings = {
            mode: (
                config.momentum_pullback.range_trap_lookback_candles,
                config.momentum_pullback.range_trap_max_width_atr,
            )
            for mode, config in MODES.items()
            if config.momentum_pullback is not None
        }

        self.assertEqual(
            trap_settings,
            {
                "scalp": (16, 2.25),
                "intraday": (20, 2.50),
                "swing": (24, 2.75),
                "position": (30, 3.00),
            },
        )

    def test_structural_target_filter_uses_each_modes_rr_floor(self):
        scalp = MODES["scalp"]
        position = MODES["position"]
        opposing_highs = [(10, 101.15)]

        scalp_plan = _structure_targets(
            "BUY",
            100.0,
            99.5,
            1.0,
            opposing_highs,
            [],
            target_buffer_atr=scalp.momentum_pullback.target_buffer_atr,
            minimum_rr=scalp.min_rr_ratio,
            minimum_room_atr=scalp.momentum_pullback.minimum_target_room_atr,
        )
        position_plan = _structure_targets(
            "BUY",
            100.0,
            99.5,
            1.0,
            opposing_highs,
            [],
            target_buffer_atr=position.momentum_pullback.target_buffer_atr,
            minimum_rr=position.min_rr_ratio,
            minimum_room_atr=position.momentum_pullback.minimum_target_room_atr,
        )

        self.assertGreater(scalp_plan[0], 100.0)
        self.assertIn("too little room", position_plan[3])
        self.assertEqual(position_plan[:3], (0.0, 0.0, 0.0))

    def test_combined_coordinator_cannot_fall_back_to_a_trade_profile(self):
        with self.assertRaisesRegex(ValueError, "resolve a combined mode"):
            _momentum_pullback_decision(
                [], [], [], [], 0.0, 0.0, 0.0, 1.0, 50.0, 50.0,
                mode_cfg=MODES["scalp_interval"],
            )

    def test_chart_scope_routes_combined_analysis_by_displayed_timeframe(self):
        mode, issue = _resolve_chart_scope(
            "scalp_interval",
            "H1",
            None,
            [("scalp", "M15"), ("intraday", "H1")],
        )

        self.assertEqual(mode, "intraday")
        self.assertIsNone(issue)

    def test_chart_scope_rejects_a_timeframe_mismatch(self):
        mode, issue = _resolve_chart_scope("swing", "H1", "H4", None)

        self.assertEqual(mode, "swing")
        self.assertIn("No trade setup is issued", issue)

    def test_chart_prompt_receives_the_selected_mode_thresholds(self):
        prompt_rules = _mode_prompt_instructions("swing", "H4", None)

        self.assertIn("Selected mode: Swing", prompt_rules)
        self.assertIn("at least 1:2.5 R:R", prompt_rules)
        self.assertIn("Do not wait for a complete trend", prompt_rules)

    def test_combined_manual_routes_keep_each_timeframe_in_its_own_mode(self):
        selected_timeframes = {"scalp": "M15", "interval": "H1"}
        for module in (callbacks, commands):
            with (
                patch.object(
                    module, "get_user_mode", return_value="scalp_interval"
                ),
                patch.object(
                    module,
                    "get_combined_timeframes",
                    return_value=selected_timeframes,
                ),
            ):
                self.assertEqual(
                    module._analysis_mode_for_timeframe(101, "M15"), "scalp"
                )
                self.assertEqual(
                    module._analysis_mode_for_timeframe(101, "H1"), "intraday"
                )
                with self.assertRaises(ValueError):
                    module._analysis_mode_for_timeframe(101, "M30")


class ModeScopedBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_market_cards_are_built_from_each_accounts_selected_streams(self):
        streams_by_account = {
            101: [("SCALP", "M15", "scalp"), ("INTRA-HOUR", "H1", "intraday")],
            102: [("SCALP", "M15", "scalp"), ("INTRA-HOUR", "H1", "intraday")],
            103: [("SWING", "H4", "swing")],
        }
        analysis = AsyncMock(
            side_effect=lambda timeframe, mode: SimpleNamespace(
                timeframe=timeframe, mode=mode
            )
        )
        broadcast = AsyncMock(side_effect=lambda bot, recipients, text, **kwargs: (set(), len(recipients)))

        with (
            patch.object(
                alerts,
                "get_monitoring_streams",
                side_effect=lambda account_id: streams_by_account[account_id],
            ),
            patch.object(alerts, "_safe_analyze", analysis),
            patch.object(alerts, "_broadcast_text", broadcast),
        ):
            dead, delivered = await alerts._broadcast_mode_scoped_market_cards(
                object(),
                {101, 102, 103},
                lambda item: f"{item.mode}:{item.timeframe}",
                "test market card",
            )

        self.assertEqual(dead, set())
        self.assertEqual(delivered, 3)
        self.assertCountEqual(
            analysis.await_args_list,
            [
                unittest.mock.call("M15", mode="scalp"),
                unittest.mock.call("H1", mode="intraday"),
                unittest.mock.call("H4", mode="swing"),
            ],
        )
        combined_texts = [
            call.args[2]
            for call in broadcast.await_args_list
            if call.args[1] == {101, 102}
        ]
        self.assertEqual(len(combined_texts), 1)
        self.assertIn("scalp:M15", combined_texts[0])
        self.assertIn("intraday:H1", combined_texts[0])


class ModeSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_loads_the_requested_mode_profile_before_scanning(self):
        class CandleData:
            def __len__(self):
                return 40

        data = CandleData()
        seen = []

        def analyze_with_profile(candles, timeframe, mode_cfg):
            seen.append((timeframe, mode_cfg.name, mode_cfg.momentum_pullback))
            return mode_cfg

        with (
            patch.object(engine, "fetch_ohlcv", new=AsyncMock(return_value=data)),
            patch.object(
                engine, "_analyze_simple_data", side_effect=analyze_with_profile
            ),
        ):
            for mode, timeframe in (
                ("scalp", "M15"),
                ("intraday", "H1"),
                ("swing", "H4"),
                ("position", "D1"),
            ):
                result = await engine._analyze_single(timeframe, mode=mode)
                self.assertEqual(result.name, mode)

        self.assertEqual(
            seen,
            [
                (timeframe, mode, MODES[mode].momentum_pullback)
                for mode, timeframe in (
                    ("scalp", "M15"),
                    ("intraday", "H1"),
                    ("swing", "H4"),
                    ("position", "D1"),
                )
            ],
        )

    async def test_unknown_or_combined_mode_stops_before_market_fetch(self):
        with patch.object(engine, "fetch_ohlcv", new=AsyncMock()) as fetch:
            with self.assertRaisesRegex(ValueError, "Unknown analysis mode"):
                await engine._analyze_single("H1", mode="intraday_typo")
            fetch.assert_not_awaited()

            with self.assertRaisesRegex(ValueError, "no standalone"):
                await engine._analyze_single("M15", mode="scalp_interval")
            fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()