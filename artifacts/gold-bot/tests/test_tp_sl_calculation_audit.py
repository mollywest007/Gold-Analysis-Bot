"""Formula-level regression tests for mode-specific XAU/USD trade plans."""
import math
import os
import sys
import time
import unittest
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis.engine import MarketAnalysis
from src.analysis.market_data import OHLCVData
from src.analysis.modes import MODES, resolve_momentum_pullback_profile
from src.analysis.weighted.config import resolve_analysis_config
from src.analysis.weighted.market import prepare_completed_candles
from src.analysis.weighted.structure import PriceZone
from src.analysis.weighted.validation import validate_candidate
from src.utils import formatting


MODE_TIMEFRAMES = {
    "scalp": "M5",
    "intraday": "M15",
    "swing": "H4",
    "position": "D1",
}

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M3": 180,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


def _candidate(direction, entry, atr, invalidation_distance_atr=0.10):
    sign = 1 if direction == "BUY" else -1
    return SimpleNamespace(
        direction=direction,
        level=entry,
        invalidation_level=entry - sign * invalidation_distance_atr * atr,
    )


def _structure(direction, entry, atr, *, opposing_distance_atr=20.0):
    sign = 1 if direction == "BUY" else -1
    target = entry + sign * opposing_distance_atr * atr
    return SimpleNamespace(
        swing_highs=[(10, target)] if direction == "BUY" else [(10, entry + atr)],
        swing_lows=[(20, entry - atr)] if direction == "BUY" else [(20, target)],
        supports=[],
        resistances=[],
    )


def _market_data(timeframe, *, count=40, now=None):
    now = float(now if now is not None else time.time())
    seconds = TIMEFRAME_SECONDS[timeframe]
    last_open = now - seconds - 1
    timestamps = [
        last_open - seconds * (count - index - 1)
        for index in range(count)
    ]
    opens = [100.0] * count
    highs = [100.2] * count
    lows = [99.8] * count
    closes = [100.0] * count
    return OHLCVData(
        opens,
        highs,
        lows,
        closes,
        [100.0] * count,
        spot_price=100.0,
        timestamps=timestamps,
        fetched_at=now,
        price_source="spot",
        candle_source="spot_normalized_futures",
        symbol="XAU/USD",
    )


def _cent(value, rounding):
    quantum = Decimal("0.01")
    return float(
        (value / quantum).to_integral_value(rounding=rounding) * quantum
    )


class TPslFormulaAuditTests(unittest.TestCase):
    def test_every_mode_resolves_the_expected_analysis_timeframe_ladder(self):
        expected = {
            "scalp": ("M15", "M5", "M1"),
            "intraday": ("H1", "M15", "M5"),
            "swing": ("D1", "H4", "H1"),
            "position": ("W1", "D1", "H4"),
        }
        for mode, timeframes in expected.items():
            with self.subTest(mode=mode):
                resolved = resolve_analysis_config(MODES[mode])
                self.assertEqual(resolved.timeframes, timeframes)

        # The saved scalp chart is M15. The resolver shifts the configured
        # context/refinement offsets with that selected setup chart.
        selected_scalp = resolve_analysis_config(MODES["scalp"], "M15")
        self.assertEqual(
            selected_scalp.timeframes, ("M30", "M15", "M3")
        )

    def test_entry_and_all_published_levels_use_gold_cent_precision(self):
        entry, atr = 2345.678, 0.5
        profile = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        plan = validate_candidate(
            _candidate("BUY", entry, atr),
            price=entry,
            atr=atr,
            structure=_structure("BUY", entry, atr),
            profile=profile,
            spread=0.02,
        )
        self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
        self.assertEqual(plan["entry"], 2345.68)
        for key in ("entry", "stop_loss", "tp1", "tp2"):
            value = plan[key]
            if value is not None:
                self.assertAlmostEqual(value * 100, round(value * 100), places=7)

    def test_all_modes_scale_both_directions_with_atr_and_publish_real_rr(self):
        entry = 2400.00
        for mode, timeframe in MODE_TIMEFRAMES.items():
            profile = resolve_momentum_pullback_profile(
                MODES[mode], timeframe
            )
            for atr in (0.05, 5.0):
                spread = atr * 0.05
                for direction in ("BUY", "SELL"):
                    with self.subTest(
                        mode=mode,
                        timeframe=timeframe,
                        atr=atr,
                        direction=direction,
                    ):
                        plan = validate_candidate(
                            _candidate(direction, entry, atr),
                            price=entry,
                            atr=atr,
                            structure=_structure(direction, entry, atr),
                            profile=profile,
                            spread=spread,
                            preferred_rr=MODES[mode].preferred_rr_ratio,
                        )
                        self.assertTrue(plan["valid"], plan.get("rejection_reasons"))

                        minimum_risk = (
                            atr * profile.minimum_stop_distance_atr
                        )
                        expected_unrounded_stop = (
                            entry - minimum_risk
                            if direction == "BUY"
                            else entry + minimum_risk
                        )
                        # Independent cent-grid expectation: a stop rounds
                        # outward from entry, never back inside its ATR floor.
                        expected_stop = (
                            math.floor(expected_unrounded_stop * 100 + 1e-9)
                            / 100
                            if direction == "BUY"
                            else math.ceil(expected_unrounded_stop * 100 - 1e-9)
                            / 100
                        )
                        self.assertEqual(plan["stop_loss"], expected_stop)

                        if direction == "BUY":
                            self.assertLess(plan["stop_loss"], plan["entry"])
                            self.assertGreater(plan["tp1"], plan["entry"])
                        else:
                            self.assertGreater(plan["stop_loss"], plan["entry"])
                            self.assertLess(plan["tp1"], plan["entry"])

                        risk = abs(plan["entry"] - plan["stop_loss"])
                        reward = abs(plan["tp1"] - plan["entry"])
                        independent_rr = round(reward / risk, 2)
                        self.assertEqual(plan["rr"], independent_rr)
                        self.assertEqual(plan["target_rrs"]["TP1"], independent_rr)
                        decimal_entry = Decimal(str(plan["entry"]))
                        decimal_risk = abs(
                            decimal_entry - Decimal(str(plan["stop_loss"]))
                        )
                        decimal_spread = Decimal(str(spread))
                        minimum_rr = Decimal(
                            str(profile.minimum_target_rr)
                        )
                        preferred_rr = Decimal(
                            str(
                                max(
                                    profile.minimum_target_rr,
                                    MODES[mode].preferred_rr_ratio,
                                )
                            )
                        )
                        required_reward = max(
                            decimal_risk * preferred_rr,
                            minimum_rr
                            * (decimal_risk + decimal_spread / Decimal("2"))
                            + decimal_spread,
                        )
                        sign = 1 if direction == "BUY" else -1
                        structural_pivot = Decimal(
                            str(entry + sign * 20 * atr)
                        )
                        structural_buffer = max(
                            Decimal(str(atr))
                            * Decimal(str(profile.target_buffer_atr)),
                            decimal_spread / Decimal("2"),
                            Decimal("0.01"),
                        )
                        structural_cap = (
                            structural_pivot - structural_buffer
                            if direction == "BUY"
                            else structural_pivot + structural_buffer
                        )
                        requested_target = decimal_entry + sign * required_reward
                        constrained_target = (
                            min(requested_target, structural_cap)
                            if direction == "BUY"
                            else max(requested_target, structural_cap)
                        )
                        expected_target = (
                            min(
                                _cent(constrained_target, ROUND_CEILING),
                                _cent(structural_cap, ROUND_FLOOR),
                            )
                            if direction == "BUY"
                            else max(
                                _cent(constrained_target, ROUND_FLOOR),
                                _cent(structural_cap, ROUND_CEILING),
                            )
                        )
                        self.assertEqual(plan["tp1"], expected_target)
                        self.assertGreaterEqual(
                            plan["rr"], MODES[mode].preferred_rr_ratio
                        )
                        self.assertGreaterEqual(
                            plan["reference_spread_target_rrs"]["TP1"],
                            profile.minimum_target_rr,
                        )
                        self.assertGreaterEqual(
                            risk + 1e-9, minimum_risk
                        )
                        self.assertLessEqual(
                            risk,
                            atr * profile.maximum_stop_distance_atr + 1e-9,
                        )

    def test_support_and_resistance_zone_edges_are_used_for_stop_invalidation(self):
        profile = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        entry, atr = 100.0, 1.0
        cases = (
            (
                "BUY",
                99.75,
                PriceZone(99.60, 99.80, 99.70, 2, "support"),
                "supports",
                99.60,
                99.50,
            ),
            (
                "SELL",
                100.25,
                PriceZone(100.20, 100.40, 100.30, 2, "resistance"),
                "resistances",
                100.40,
                100.50,
            ),
        )
        for direction, invalidation, zone, zone_key, expected_level, expected_sl in cases:
            structure = _structure(direction, entry, atr)
            setattr(structure, zone_key, [zone])
            candidate = SimpleNamespace(
                direction=direction, level=entry,
                invalidation_level=invalidation,
            )
            plan = validate_candidate(
                candidate,
                price=entry,
                atr=atr,
                structure=structure,
                profile=profile,
                spread=None,
            )
            with self.subTest(direction=direction):
                self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
                self.assertEqual(plan["invalidation_level"], expected_level)
                self.assertEqual(plan["stop_loss"], expected_sl)

    def test_structural_target_rounding_stays_inside_buffered_level(self):
        profile = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        entry, atr = 100.0, 0.1
        target_buffer = max(atr * profile.target_buffer_atr, 0.01)
        for direction in ("BUY", "SELL"):
            sign = 1 if direction == "BUY" else -1
            structural_cap = entry + sign * 0.085
            level = structural_cap + sign * target_buffer
            structure = SimpleNamespace(
                swing_highs=[(10, level)] if direction == "BUY" else [(10, 101.0)],
                swing_lows=[(20, 99.0)] if direction == "BUY" else [(20, level)],
                supports=[],
                resistances=[],
            )
            plan = validate_candidate(
                _candidate(direction, entry, atr),
                price=entry,
                atr=atr,
                structure=structure,
                profile=profile,
                spread=None,
                preferred_rr=MODES["scalp"].preferred_rr_ratio,
            )
            with self.subTest(direction=direction):
                self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
                if direction == "BUY":
                    self.assertLessEqual(plan["tp1"], structural_cap)
                    self.assertEqual(plan["tp1"], 100.08)
                else:
                    self.assertGreaterEqual(plan["tp1"], structural_cap)
                    self.assertEqual(plan["tp1"], 99.92)
                self.assertGreaterEqual(plan["rr"], profile.minimum_target_rr)

    def test_nearby_opposition_and_costs_reject_insufficient_trade_room(self):
        for mode, timeframe in MODE_TIMEFRAMES.items():
            profile = resolve_momentum_pullback_profile(MODES[mode], timeframe)
            entry, atr = 100.0, 1.0
            plan = validate_candidate(
                _candidate("BUY", entry, atr),
                price=entry,
                atr=atr,
                structure=_structure(
                    "BUY", entry, atr, opposing_distance_atr=0.25
                ),
                profile=profile,
                spread=None,
            )
            with self.subTest(mode=mode, case="nearby structure"):
                self.assertFalse(plan["valid"])
                self.assertTrue(
                    any("R:R" in reason for reason in plan["rejection_reasons"]),
                    plan["rejection_reasons"],
                )

        scalp = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        cost_limited = validate_candidate(
            _candidate("BUY", 100.0, 1.0),
            price=100.0,
            atr=1.0,
            structure=_structure(
                "BUY", 100.0, 1.0, opposing_distance_atr=0.70
            ),
            profile=scalp,
            spread=0.10,
        )
        self.assertFalse(cost_limited["valid"])
        self.assertIn(
            "spread-adjusted r:r",
            " ".join(cost_limited["rejection_reasons"]).lower(),
        )

    def test_no_candidate_or_insufficient_market_history_produces_no_plan(self):
        missing = validate_candidate(
            _candidate("BUY", 100.0, 1.0),
            price=100.0,
            atr=1.0,
            structure=SimpleNamespace(
                swing_highs=[],
                swing_lows=[],
                supports=[],
                resistances=[],
            ),
            profile=resolve_momentum_pullback_profile(MODES["scalp"], "M5"),
            spread=None,
        )
        self.assertFalse(missing["valid"])
        self.assertIn("No confirmed opposing", " ".join(missing["rejection_reasons"]))

        now = 1_700_000_000.0
        insufficient = _market_data("M5", count=29, now=now)
        result = prepare_completed_candles(
            insufficient, "M5", now=now, max_age_bars=2
        )
        self.assertEqual(result.status, "insufficient")
        self.assertIsNone(result.data)

        stale = _market_data("M5", count=40, now=now)
        stale_result = prepare_completed_candles(
            stale,
            "M5",
            now=now + TIMEFRAME_SECONDS["M5"] * 8,
            max_age_bars=2,
        )
        self.assertEqual(stale_result.status, "stale")
        self.assertIsNone(stale_result.data)

    def test_published_plan_prices_are_the_values_rendered_in_telegram_card(self):
        entry, atr = 100.0, 0.4
        profile = resolve_momentum_pullback_profile(MODES["swing"], "H4")
        plan = validate_candidate(
            _candidate("BUY", entry, atr),
            price=entry,
            atr=atr,
            structure=_structure("BUY", entry, atr),
            profile=profile,
            spread=0.04,
            preferred_rr=MODES["swing"].preferred_rr_ratio,
        )
        self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
        analysis = MarketAnalysis(
            price=plan["entry"],
            timeframe="H4",
            bias="Bullish",
            trend="Bullish",
            strength="Strong",
            momentum="Bullish",
            confidence=85,
            entry=plan["entry"],
            stop_loss=plan["stop_loss"],
            tp1=plan["tp1"],
            tp2=plan["tp2"] or 0.0,
            tp3=plan["tp3"] or 0.0,
            rr_ratio=plan["rr"],
            action="BUY",
            wait_reason="",
            resistance1=plan["tp1"],
            resistance2=plan["tp2"] or 0.0,
            support1=plan["stop_loss"],
            support2=0.0,
            breakout=False,
            reversal=False,
            liquidity_zone="",
            atr=atr,
            trade_type="Swing",
            analysis_mode="swing",
            setup_quality="A",
            signal_category="MODERATE",
            signal_label="MODERATE ENTRY",
            setup_status="MODERATE ENTRY",
            institutional_report={
                "analysis_engine": "weighted_multi_timeframe",
                "mode_rules": {
                    "minimum_target_rr": profile.minimum_target_rr,
                },
            },
        )
        with patch.object(
            formatting,
            "market_status",
            return_value={"is_open": True, "note": "test"},
        ):
            card = formatting.early_entry_card(analysis, alert_label="SCALP")

        self.assertIn(f"Entry         : {plan['entry']:,.2f}", card)
        self.assertIn(f"Stop loss     : {plan['stop_loss']:,.2f}", card)
        self.assertIn(f"TP1           : {plan['tp1']:,.2f}", card)
        displayed_rr = round(
            abs(plan["tp1"] - plan["entry"])
            / abs(plan["entry"] - plan["stop_loss"]),
            2,
        )
        self.assertIn(f"TP1 1:{displayed_rr:.2f}", card)
        self.assertEqual(plan["stop_loss"], 99.57)
        self.assertEqual(plan["tp1"], 100.94)

