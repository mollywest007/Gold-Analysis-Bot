import math
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis import engine
from src.analysis.market_data import OHLCVData
from src.analysis.modes import MODES
from src.analysis.weighted.config import resolve_analysis_config
from src.analysis.weighted.engine import _category_for_score, analyze_weighted_market
from src.analysis.weighted.indicators import IndicatorSnapshot, calculate_indicators
from src.analysis.weighted.market import prepare_completed_candles
from src.analysis.weighted.regime import classify_regime
from src.analysis.weighted.scoring import SCORE_WEIGHTS, score_directions
from src.analysis.weighted.setups import SetupCandidate, detect_setups
from src.analysis.weighted.structure import (
    PriceZone,
    StructureSnapshot,
    liquidity_sweeps,
)
from src.analysis.weighted.validation import validate_candidate


TIMEFRAME_SECONDS = {
    "M1": 60,
    "M3": 180,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2_592_000,
}


def _candles(timeframe="M5", *, count=100, now=None, simulated=False):
    now = float(now if now is not None else time.time())
    seconds = TIMEFRAME_SECONDS[timeframe]
    last_open = now - seconds - 1
    timestamps = [
        last_open - seconds * (count - index - 1)
        for index in range(count)
    ]
    opens, highs, lows, closes, volumes = [], [], [], [], []
    previous = 2400.0
    for index in range(count):
        close = 2400 + index * 0.025 + math.sin(index / 3) * 0.3
        opening = previous
        opens.append(opening)
        closes.append(close)
        highs.append(max(opening, close) + 0.28)
        lows.append(min(opening, close) - 0.28)
        volumes.append(100 + index)
        previous = close
    return OHLCVData(
        opens,
        highs,
        lows,
        closes,
        volumes,
        spot_price=closes[-1],
        is_simulated=simulated,
        timestamps=timestamps,
        fetched_at=now,
        price_source="spot",
        candle_source="spot_normalized_futures",
        symbol="GC=F",
    )


def _structure(
    *,
    direction="BUY",
    label="HH_HL",
    swing_highs=((10, 101.0), (20, 102.0)),
    swing_lows=((12, 98.0), (22, 99.0)),
    supports=(),
    resistances=(),
):
    return StructureSnapshot(
        label,
        direction,
        "NONE",
        tuple(swing_highs),
        tuple(swing_lows),
        tuple(supports),
        tuple(resistances),
        swing_highs[-1][1] if swing_highs else None,
        swing_lows[-1][1] if swing_lows else None,
    )


class ModeResolutionTests(unittest.TestCase):
    def test_four_modes_use_the_requested_default_timeframe_ladders(self):
        expected = {
            "scalp": ("M15", "M5", "M1"),
            "intraday": ("H1", "M15", "M5"),
            "swing": ("D1", "H4", "H1"),
            "position": ("W1", "D1", "H4"),
        }
        thresholds = {
            "scalp": 55,
            "intraday": 58,
            "swing": 62,
            "position": 65,
        }
        minimum_rr = {
            "scalp": 1.3,
            "intraday": 1.5,
            "swing": 2.0,
            "position": 2.0,
        }
        for mode, timeframes in expected.items():
            with self.subTest(mode=mode):
                resolved = resolve_analysis_config(MODES[mode])
                self.assertEqual(resolved.timeframes, timeframes)
                self.assertEqual(resolved.moderate_score, thresholds[mode])
                self.assertAlmostEqual(
                    float(resolved.profile.minimum_target_rr), minimum_rr[mode]
                )
                self.assertAlmostEqual(
                    resolved.context_weight
                    + resolved.setup_weight
                    + resolved.refinement_weight,
                    1.0,
                )

    def test_selected_setup_timeframe_resolves_mode_relative_context(self):
        cases = (
            ("scalp", "M15", ("M30", "M15", "M3")),
            ("intraday", "H1", ("D1", "H1", "M30")),
            ("swing", "H1", ("H4", "H1", "M30")),
            ("position", "W1", ("MN1", "W1", "D1")),
        )
        for mode, setup_tf, expected in cases:
            with self.subTest(mode=mode, timeframe=setup_tf):
                config = resolve_analysis_config(MODES[mode], setup_tf)
                self.assertEqual(config.timeframes, expected)

    def test_edge_timeframe_clamps_duplicate_roles_without_double_counting(self):
        resolved = resolve_analysis_config(MODES["scalp"], "M1")
        self.assertEqual(resolved.setup_timeframe, "M1")
        self.assertEqual(resolved.refinement_timeframe, "M1")
        self.assertEqual(resolved.refinement_weight, 0.0)
        self.assertAlmostEqual(
            resolved.context_weight + resolved.setup_weight, 1.0
        )

    def test_combined_coordinator_is_not_a_standalone_analysis_mode(self):
        with self.assertRaisesRegex(ValueError, "no standalone"):
            resolve_analysis_config(MODES["scalp_interval"])


class MarketDataValidationTests(unittest.TestCase):
    def test_unfinished_bar_is_excluded_without_marking_it_complete(self):
        now = 1_700_000_000.0
        data = _candles("M1", count=40, now=now + 60.5)
        data.timestamps[-1] = now + 30
        result = prepare_completed_candles(data, "M1", now=now)
        self.assertEqual(result.status, "ready")
        self.assertEqual(len(result.data.closes), 39)
        self.assertNotIn(data.timestamps[-1], result.data.timestamps)

    def test_stale_data_and_simulated_or_unverified_data_never_signal(self):
        now = 1_700_000_000.0
        stale_data = _candles("M5", count=40, now=now)
        stale = prepare_completed_candles(
            stale_data,
            "M5",
            now=stale_data.timestamps[-1] + TIMEFRAME_SECONDS["M5"] * 8,
            max_age_bars=2,
        )
        self.assertEqual(stale.status, "stale")

        config = resolve_analysis_config(MODES["scalp"], "M5")
        future = now + 10_000
        sim_setup = _candles("M5", count=80, now=future, simulated=True)
        context = _candles("M15", count=80, now=future)
        refine = _candles("M1", count=80, now=future)
        result = analyze_weighted_market(
            config=config,
            raw_data={"M5": sim_setup, "M15": context, "M1": refine},
            spread=0.1,
            now=future,
        )
        self.assertEqual(result["action"], "WAIT")
        self.assertEqual(result["data_quality"], "SIMULATED")
        self.assertFalse(result["required_data_valid"])

    def test_missing_timestamps_fail_closed(self):
        data = _candles("M15", count=50)
        data.timestamps = []
        result = prepare_completed_candles(data, "M15")
        self.assertEqual(result.status, "timestamps_unavailable")
        self.assertIsNone(result.data)


class IndicatorAndSetupTests(unittest.TestCase):
    def test_indicator_snapshot_reports_ema_rsi_macd_and_atr(self):
        data = _candles("M5", count=100)
        result = calculate_indicators(data.highs, data.lows, data.closes)
        self.assertIsNotNone(result.ema20)
        self.assertIsNotNone(result.ema50)
        self.assertIsNotNone(result.rsi14)
        self.assertIsNotNone(result.macd_hist)
        self.assertGreater(result.atr14, 0)
        self.assertEqual(sum(SCORE_WEIGHTS.values()), 100)

    def test_nearby_opposing_structure_reduces_the_directional_score(self):
        config = resolve_analysis_config(MODES["scalp"])
        candidate = SetupCandidate(
            "BUY", "TREND_PULLBACK", 100.0, 99.5, 0.8, 0.9, 0.8, 0.2,
            ("pullback holding support",),
        )
        common = {
            "indicators": {"context": None, "setup": None, "refinement": None},
            "candidates": [candidate],
            "regime": "BULLISH_TREND",
            "price": 100.0,
            "atr": 1.0,
            "spread": None,
            "profile": config.profile,
            "config": config,
        }
        near = _structure(
            direction="BUY",
            swing_highs=((20, 100.8),),
            swing_lows=((22, 99.0),),
        )
        far = _structure(
            direction="BUY",
            swing_highs=((20, 103.0),),
            swing_lows=((22, 99.0),),
        )
        near_score = score_directions(
            structures={"context": None, "setup": near, "refinement": None},
            **common,
        )["BUY"]
        far_score = score_directions(
            structures={"context": None, "setup": far, "refinement": None},
            **common,
        )["BUY"]
        self.assertLess(near_score["score"], far_score["score"])
        self.assertIn(
            "nearby_opposing_structure",
            {item["reason"] for item in near_score["penalties"]},
        )

    def test_regime_requires_directional_ema_stack_for_trend_label(self):
        structure = _structure(direction="BUY")
        aligned = IndicatorSnapshot(101.0, 100.0, 55.0, 54.0, 0.2, 0.1, 1.0, 1.0, 1.0)
        crossed = IndicatorSnapshot(99.0, 100.0, 55.0, 54.0, 0.2, 0.1, 1.0, 1.0, 1.0)
        self.assertEqual(
            classify_regime(
                structure, aligned, price=102.0, candle_timestamp=None,
                volatility_limit=2.0,
            ).label,
            "BULLISH_TREND",
        )
        self.assertEqual(
            classify_regime(
                structure, crossed, price=102.0, candle_timestamp=None,
                volatility_limit=2.0,
            ).label,
            "TRANSITIONING_OR_UNCLEAR",
        )

    def test_missing_data_cannot_earn_positive_confluence_points(self):
        config = resolve_analysis_config(MODES["intraday"])
        scored = score_directions(
            structures={"context": None, "setup": None, "refinement": None},
            indicators={"context": None, "setup": None, "refinement": None},
            candidates=[],
            regime="TRANSITIONING_OR_UNCLEAR",
            price=2400.0,
            atr=None,
            spread=None,
            profile=config.profile,
            config=config,
        )
        self.assertEqual(scored["BUY"]["score"], 0)
        self.assertEqual(scored["SELL"]["score"], 0)
        self.assertTrue(
            all(value == 0 for value in scored["BUY"]["breakdown"].values())
        )

    def test_liquidity_sweep_requires_prior_level_reentry(self):
        highs = [101.0] * 10
        lows = [99.0] * 10
        opens = [100.0] * 10
        closes = [100.2] * 10
        lows[-1] = 98.8
        closes[-1] = 100.1
        sweeps = liquidity_sweeps(
            highs, lows, opens, closes, atr=1.0, lookback=8
        )
        self.assertEqual([item["direction"] for item in sweeps], ["BUY"])
        closes[-1] = 98.7
        self.assertEqual(
            liquidity_sweeps(highs, lows, opens, closes, atr=1.0, lookback=8),
            [],
        )

    def test_all_five_early_setup_patterns_are_detectable(self):
        profile = MODES["scalp"].momentum_pullback
        atr = 1.0
        count = 50
        opens = [100.0] * count
        highs = [100.5] * count
        lows = [99.5] * count
        closes = [100.0] * count
        indicators = IndicatorSnapshot(
            100.0, 99.8, 52.0, 50.0, 0.2, 0.1, 1.0, 1.0, 1.0
        )

        # Sweep: push through the prior low, then close back inside.
        sweep = (opens[:], highs[:], lows[:], closes[:])
        sweep[1][-1] = 100.3
        sweep[2][-1] = 98.8
        sweep[0][-1] = 99.9
        sweep[3][-1] = 100.15
        sweep_result = detect_setups(
            *sweep,
            price=100.15,
            atr=atr,
            indicators=indicators,
            structure=_structure(direction="WAIT", label="RANGE"),
            context_direction="WAIT",
            refinement_direction="BUY",
            regime="RANGE_BOUND",
            profile=profile,
            breakout_lookback=20,
            sweep_lookback=15,
        )
        self.assertIn("LIQUIDITY_SWEEP", {item.setup_type for item in sweep_result})

        # Breakout: close decisively above the recent boundary.
        breakout = (opens[:], highs[:], lows[:], closes[:])
        breakout[0][-1] = 100.6
        breakout[1][-1] = 101.6
        breakout[2][-1] = 100.5
        breakout[3][-1] = 101.5
        breakout_result = detect_setups(
            *breakout,
            price=101.5,
            atr=atr,
            indicators=indicators,
            structure=_structure(direction="WAIT", label="RANGE"),
            context_direction="WAIT",
            refinement_direction="BUY",
            regime="TRANSITIONING_OR_UNCLEAR",
            profile=profile,
            breakout_lookback=20,
            sweep_lookback=15,
        )
        self.assertIn("BREAKOUT", {item.setup_type for item in breakout_result})

        # A structurally close breakout must not be discarded only because
        # price has not yet caught up with EMA20.
        lagging_ema_indicators = IndicatorSnapshot(
            97.0, 96.0, 52.0, 50.0, 0.2, 0.1, 1.0, 1.0, 1.0
        )
        early_breakout_result = detect_setups(
            *breakout,
            price=101.5,
            atr=atr,
            indicators=lagging_ema_indicators,
            structure=_structure(direction="WAIT", label="RANGE"),
            context_direction="WAIT",
            refinement_direction="BUY",
            regime="TRANSITIONING_OR_UNCLEAR",
            profile=profile,
            breakout_lookback=20,
            sweep_lookback=15,
        )
        self.assertIn("BREAKOUT", {item.setup_type for item in early_breakout_result})

        # Retest: the previous completed break is retested and held by the last bar.
        retest = (opens[:], highs[:], lows[:], closes[:])
        retest[1][-3] = 100.5
        retest[2][-3] = 99.8
        retest[0][-3] = 100.0
        retest[3][-3] = 101.1
        retest[0][-1] = 100.4
        retest[1][-1] = 100.8
        retest[2][-1] = 100.2
        retest[3][-1] = 100.7
        retest_result = detect_setups(
            *retest,
            price=100.7,
            atr=atr,
            indicators=indicators,
            structure=_structure(direction="WAIT", label="RANGE"),
            context_direction="WAIT",
            refinement_direction="BUY",
            regime="TRANSITIONING_OR_UNCLEAR",
            profile=profile,
            breakout_lookback=20,
            sweep_lookback=15,
        )
        self.assertIn(
            "BREAKOUT_RETEST", {item.setup_type for item in retest_result}
        )

        zone = PriceZone(99.8, 100.1, 100.0, 3, "support")
        reaction_structure = _structure(
            direction="WAIT",
            label="RANGE",
            supports=(zone,),
        )
        reaction = (opens[:], highs[:], lows[:], closes[:])
        reaction[0][-1] = 100.0
        reaction[1][-1] = 100.2
        reaction[2][-1] = 99.4
        reaction[3][-1] = 100.1
        reaction_result = detect_setups(
            *reaction,
            price=100.1,
            atr=atr,
            indicators=indicators,
            structure=reaction_structure,
            context_direction="WAIT",
            refinement_direction="BUY",
            regime="RANGE_BOUND",
            profile=profile,
            breakout_lookback=20,
            sweep_lookback=15,
        )
        self.assertIn(
            "SUPPORT_RESISTANCE_REACTION",
            {item.setup_type for item in reaction_result},
        )

        pullback_result = detect_setups(
            *reaction,
            price=100.1,
            atr=atr,
            indicators=indicators,
            structure=_structure(
                direction="BUY",
                supports=(PriceZone(99.8, 100.1, 100.0, 3, "support"),),
            ),
            context_direction="BUY",
            refinement_direction="BUY",
            regime="BULLISH_TREND",
            profile=profile,
            breakout_lookback=20,
            sweep_lookback=15,
        )
        self.assertIn(
            "TREND_PULLBACK", {item.setup_type for item in pullback_result}
        )


class ValidationAndPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_early_moderate_strong_and_conflict_categories_obey_thresholds(self):
        config = resolve_analysis_config(MODES["swing"])
        candidate = SetupCandidate(
            "BUY", "TREND_PULLBACK", 100.0, 99.0, 0.8, 0.8, 0.75, 0.2,
            ("trend pullback is holding support",),
        )
        early_breakdown = {
            "market_structure": 8,
            "entry_location": 15,
            "price_action": 12,
            "momentum_trend": 4,
            "liquidity_breakout_retest": 10,
            "volatility_execution": 4,
        }
        self.assertEqual(
            _category_for_score(
                config.early_score,
                early_breakdown,
                candidate,
                config=config,
                major_conflict=False,
            ),
            "EARLY",
        )
        self.assertEqual(
            _category_for_score(
                config.moderate_score,
                early_breakdown,
                candidate,
                config=config,
                major_conflict=False,
            ),
            "MODERATE",
        )
        self.assertEqual(
            _category_for_score(
                config.strong_score,
                early_breakdown,
                candidate,
                config=config,
                major_conflict=False,
            ),
            "STRONG",
        )
        self.assertEqual(
            _category_for_score(
                100,
                early_breakdown,
                candidate,
                config=config,
                major_conflict=True,
            ),
            "WAIT",
        )
        developing_candidate = SetupCandidate(
            "BUY", "SUPPORT_RESISTANCE_REACTION", 100.0, 99.0,
            0.8, 0.8, 0.7, 0.2, ("rejection from support",),
        )
        self.assertEqual(
            _category_for_score(
                config.early_score,
                {"market_structure": 0, "entry_location": 0,
                 "price_action": 0, "momentum_trend": 1,
                 "liquidity_breakout_retest": 0, "volatility_execution": 0},
                developing_candidate,
                config=config,
                major_conflict=False,
            ),
            "EARLY",
        )

    def test_structural_stop_and_target_must_meet_mode_rr(self):
        candidate = SetupCandidate(
            "BUY", "TREND_PULLBACK", 100.0, 99.5, 0.8, 0.9, 0.8, 0.2,
            ("pullback to support with rejection",),
        )
        profile = MODES["scalp"].momentum_pullback
        structure = _structure(
            direction="BUY",
            swing_highs=((20, 100.4),),
            swing_lows=((22, 98.0),),
        )
        rejected = validate_candidate(
            candidate,
            price=100.0,
            atr=1.0,
            structure=structure,
            profile=profile,
            spread=0.01,
            preferred_rr=1.5,
        )
        self.assertFalse(rejected["valid"])
        self.assertIn("R:R", " ".join(rejected["rejection_reasons"]))

        structure = _structure(
            direction="BUY",
            swing_highs=((20, 102.5),),
            swing_lows=((22, 98.0),),
        )
        accepted = validate_candidate(
            candidate,
            price=100.0,
            atr=1.0,
            structure=structure,
            profile=profile,
            spread=0.01,
        )
        self.assertTrue(accepted["valid"])
        self.assertGreaterEqual(accepted["rr"], 1.3)
        self.assertGreaterEqual(
            accepted["reference_spread_rr"],
            profile.minimum_target_rr,
        )
        self.assertLess(accepted["stop_loss"], candidate.invalidation_level)

    def test_non_finite_candidate_invalidation_fails_closed(self):
        candidate = SetupCandidate(
            "BUY", "TREND_PULLBACK", 100.0, float("nan"),
            0.8, 0.9, 0.8, 0.2, ("support pullback",),
        )
        result = validate_candidate(
            candidate,
            price=100.0,
            atr=1.0,
            structure=_structure(),
            profile=MODES["scalp"].momentum_pullback,
            spread=None,
        )
        self.assertFalse(result["valid"])
        self.assertIn("non-finite", " ".join(result["rejection_reasons"]))

    def test_buy_invalidation_must_be_below_entry(self):
        candidate = SetupCandidate(
            "BUY", "TREND_PULLBACK", 100.0, 100.05,
            0.8, 0.9, 0.8, 0.2, ("support pullback",),
        )
        result = validate_candidate(
            candidate,
            price=100.0,
            atr=1.0,
            structure=_structure(),
            profile=MODES["scalp"].momentum_pullback,
            spread=None,
        )
        self.assertFalse(result["valid"])
        self.assertIn("invalidation", " ".join(result["rejection_reasons"]).lower())

    async def test_selected_mode_fetches_the_resolved_context_setup_and_refinement(self):
        now = time.time()
        data = {
            timeframe: _candles(timeframe, now=now)
            for timeframe in ("M1", "M5", "M15")
        }
        with (
            patch.object(
                engine,
                "fetch_ohlcv",
                new=AsyncMock(side_effect=lambda tf: data[tf]),
            ) as fetch,
            patch.object(
                engine,
                "get_cached_gold_quote",
                return_value={"spread_available": False},
            ),
        ):
            result = await engine._analyze_single("M5", mode="scalp")

        from src.utils.formatting import transparent_analysis_cards

        card = "\n".join(transparent_analysis_cards(result))
        card_text = " ".join(card.split())
        self.assertEqual(
            fetch.await_args_list,
            [
                unittest.mock.call("M15"),
                unittest.mock.call("M5"),
                unittest.mock.call("M1"),
            ],
        )
        self.assertEqual(result.timeframe, "M5")
        self.assertEqual(result.analysis_mode, "scalp")
        self.assertEqual(result.win_probability, 0)
        self.assertIn("XAU/USD ANALYSIS CARD", card)
        self.assertIn("Mode          : Scalping", card)
        self.assertIn("Timeframes : Context M15 WAIT", card_text)
        self.assertIn("WHAT TO DO", card)
        self.assertIn("HOW IT IS ANALYZING", card)
        self.assertIn("CURRENT CONFIRMATION", card)
        self.assertNotIn("Not backtested", card)
        self.assertEqual(
            {role: report["timeframe"] for role, report in result.analysis_timeframes.items()},
            {"context": "M15", "setup": "M5", "refinement": "M1"},
        )

    async def test_missing_context_data_returns_wait_and_rejection_instead_of_fabricating(self):
        now = time.time()
        setup = _candles("M5", now=now)
        refine = _candles("M1", now=now)
        with (
            patch.object(
                engine,
                "fetch_ohlcv",
                new=AsyncMock(
                    side_effect=lambda tf: (
                        None if tf == "M15" else setup if tf == "M5" else refine
                    )
                ),
            ),
            patch.object(
                engine,
                "get_cached_gold_quote",
                return_value={"spread_available": False},
            ),
        ):
            result = await engine._analyze_single("M5", mode="scalp")

        self.assertEqual(result.action, "WAIT")
        self.assertIn("Context M15", " ".join(result.rejection_reasons))


if __name__ == "__main__":
    unittest.main()
