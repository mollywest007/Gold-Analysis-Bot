"""Risk-aware sizing and contract-based P&L tests."""
import os
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.analysis.risk_policy import (
    calculate_position_size,
    calculate_trade_economics,
    risk_fraction_for_mode,
)
from src.analysis.modes import MODES, resolve_momentum_pullback_profile
from src.analysis.weighted.indicators import atr_series
from src.analysis.weighted.validation import validate_candidate
from types import SimpleNamespace


class RiskPolicyEconomicsTests(unittest.TestCase):
    def test_long_dollar_pnl_separates_price_distance_value_margin_and_costs(self):
        result = calculate_trade_economics(
            direction="BUY",
            entry=2000.0,
            stop=1990.0,
            target=2020.0,
            lots=0.1,
            contract_size_oz_per_lot=100.0,
            leverage=20.0,
            commission_rate=0.0001,
            slippage_per_oz=0.05,
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["price_distance"], {"stop": 10.0, "target": 20.0})
        self.assertEqual(result["position_value"], 20000.0)
        self.assertEqual(result["margin"], 1000.0)
        self.assertEqual(result["at_stop"]["gross_pnl"], -100.0)
        self.assertEqual(result["at_target"]["gross_pnl"], 200.0)
        self.assertEqual(result["at_stop"]["estimated_net_pnl"], -104.99)
        self.assertEqual(result["at_target"]["estimated_net_pnl"], 194.98)

    def test_short_pnl_direction_and_leverage_do_not_multiply_profit(self):
        shared = {
            "direction": "SELL",
            "entry": 2000.0,
            "stop": 2010.0,
            "target": 1980.0,
            "lots": 0.1,
            "contract_size_oz_per_lot": 100.0,
        }
        low_leverage = calculate_trade_economics(**shared, leverage=10)
        high_leverage = calculate_trade_economics(**shared, leverage=50)

        self.assertEqual(low_leverage["at_stop"]["gross_pnl"], -100.0)
        self.assertEqual(low_leverage["at_target"]["gross_pnl"], 200.0)
        self.assertEqual(
            low_leverage["at_target"]["gross_pnl"],
            high_leverage["at_target"]["gross_pnl"],
        )
        self.assertEqual(low_leverage["margin"], 2000.0)
        self.assertEqual(high_leverage["margin"], 400.0)

    def test_position_sizing_budgets_fees_and_slippage_inside_account_risk(self):
        result = calculate_position_size(
            equity=10000,
            entry=2000,
            stop=1990,
            contract_size_oz_per_lot=100,
            lot_step=0.01,
            minimum_lot=0.01,
            risk_fraction=0.005,
            commission_rate=0.0001,
            slippage_per_oz=0.05,
            leverage=20,
        )

        self.assertTrue(result["available"])
        self.assertLessEqual(result["actual_risk_fraction"], 0.005)
        self.assertGreater(result["estimated_execution_cost"], 0)
        self.assertIsNotNone(result["estimated_margin"])

    def test_position_size_uses_only_remaining_account_wide_risk_budget(self):
        result = calculate_position_size(
            equity=10000,
            entry=2000,
            stop=1990,
            contract_size_oz_per_lot=100,
            lot_step=0.01,
            minimum_lot=0.01,
            risk_fraction=0.005,
            existing_open_risk=170,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["cash_risk"], 30.0)
        self.assertEqual(result["existing_open_risk"], 170.0)
        self.assertEqual(result["total_risk_limit"], 200.0)
        self.assertLessEqual(result["aggregate_risk_fraction"], 0.02)

        exhausted = calculate_position_size(
            equity=10000,
            entry=2000,
            stop=1990,
            contract_size_oz_per_lot=100,
            lot_step=0.01,
            minimum_lot=0.01,
            existing_open_risk=200,
        )
        self.assertFalse(exhausted["available"])
        self.assertIn("No remaining risk budget", exhausted["reason"])

    def test_invalid_trade_economics_are_unavailable_not_guessed(self):
        result = calculate_trade_economics(
            direction="BUY",
            entry=2000,
            stop=2010,
            target=2020,
            lots=0.1,
            contract_size_oz_per_lot=100,
        )
        self.assertFalse(result["available"])

    def test_mode_risk_overrides_are_bounded_by_global_policy(self):
        with patch.dict(os.environ, {"XAU_SCALP_RISK_PER_TRADE_FRACTION": "0.008"}):
            self.assertEqual(risk_fraction_for_mode("scalp"), 0.008)
        with patch.dict(os.environ, {"XAU_SCALP_RISK_PER_TRADE_FRACTION": "0.02"}):
            with self.assertRaisesRegex(ValueError, "must be in"):
                risk_fraction_for_mode("scalp")

    def test_position_sizing_uses_the_selected_mode_risk_override(self):
        with patch.dict(os.environ, {"XAU_SCALP_RISK_PER_TRADE_FRACTION": "0.008"}):
            result = calculate_position_size(
                equity=10000,
                entry=2000,
                stop=1990,
                contract_size_oz_per_lot=100,
                lot_step=0.01,
                minimum_lot=0.01,
                mode="scalp",
            )
        self.assertTrue(result["available"])
        self.assertEqual(result["cash_risk"], 80.0)
        self.assertEqual(result["lots"], 0.08)

    def test_mode_tp_sl_profiles_have_minimum_rr_and_validated_overrides(self):
        for mode, cfg in MODES.items():
            if cfg.momentum_pullback is None:
                continue
            profile = resolve_momentum_pullback_profile(
                cfg, cfg.analysis_setup_timeframe
            )
            with self.subTest(mode=mode):
                self.assertGreaterEqual(profile.minimum_target_rr, 1.5)
                self.assertGreaterEqual(
                    profile.preferred_target_rr, profile.minimum_target_rr
                )
                self.assertGreaterEqual(profile.minimum_stop_distance_atr, 0.7)

        with patch.dict(
            os.environ,
            {
                "XAU_SCALP_ATR_PERIOD": "20",
                "XAU_SCALP_MINIMUM_STOP_ATR": "0.8",
                "XAU_SCALP_MINIMUM_TARGET_RR": "1.7",
                "XAU_SCALP_PREFERRED_TARGET_RR": "2.4",
            },
        ):
            overridden = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        self.assertEqual(overridden.atr_period, 20)
        self.assertEqual(overridden.minimum_stop_distance_atr, 0.8)
        self.assertEqual(overridden.minimum_target_rr, 1.7)
        self.assertEqual(overridden.preferred_target_rr, 2.4)

    def test_minimum_rr_override_cannot_lower_the_mode_floor(self):
        for mode in ("scalp", "intraday", "swing", "position"):
            cfg = MODES[mode]
            base = resolve_momentum_pullback_profile(
                cfg, cfg.analysis_setup_timeframe
            )
            floor = max(1.5, cfg.min_rr_ratio, base.minimum_target_rr)
            with self.subTest(mode=mode):
                with patch.dict(
                    os.environ,
                    {f"XAU_{mode.upper()}_MINIMUM_TARGET_RR": str(floor - 0.1)},
                ):
                    with self.assertRaisesRegex(
                        ValueError, "at least this mode's minimum target R:R"
                    ):
                        resolve_momentum_pullback_profile(
                            cfg, cfg.analysis_setup_timeframe
                        )

                with patch.dict(
                    os.environ,
                    {f"XAU_{mode.upper()}_MINIMUM_TARGET_RR": str(floor)},
                ):
                    configured = resolve_momentum_pullback_profile(
                        cfg, cfg.analysis_setup_timeframe
                    )
                self.assertEqual(configured.minimum_target_rr, floor)

    def test_mode_execution_cost_overrides_are_optional_and_validated(self):
        base = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        self.assertIsNone(base.commission_rate)
        self.assertIsNone(base.slippage_per_oz)

        with patch.dict(
            os.environ,
            {
                "XAU_SCALP_COMMISSION_RATE": "0.0002",
                "XAU_SCALP_SLIPPAGE_PER_OZ": "0.08",
            },
        ):
            configured = resolve_momentum_pullback_profile(MODES["scalp"], "M5")
        self.assertEqual(configured.commission_rate, 0.0002)
        self.assertEqual(configured.slippage_per_oz, 0.08)

        with patch.dict(
            os.environ, {"XAU_SCALP_COMMISSION_RATE": "1.0"}
        ), self.assertRaisesRegex(ValueError, "less than 1"):
            resolve_momentum_pullback_profile(MODES["scalp"], "M5")

    def test_commission_and_slippage_are_included_in_both_direction_rr_checks(self):
        for direction in ("BUY", "SELL"):
            sign = 1 if direction == "BUY" else -1
            profile = replace(
                resolve_momentum_pullback_profile(MODES["scalp"], "M5"),
                commission_rate=0.001,
                slippage_per_oz=0.05,
            )
            structure = SimpleNamespace(
                swing_highs=[(10, 103.0)] if direction == "BUY" else [(10, 101.0)],
                swing_lows=[(20, 99.0)] if direction == "BUY" else [(20, 97.0)],
                supports=[],
                resistances=[],
            )
            plan = validate_candidate(
                SimpleNamespace(
                    direction=direction,
                    level=100.0,
                    invalidation_level=100.0 - sign * 0.1,
                ),
                price=100.0,
                atr=1.0,
                structure=structure,
                profile=profile,
                spread=0.02,
            )
            with self.subTest(direction=direction):
                self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
                self.assertEqual(plan["execution_cost_status"], "configured")
                self.assertIn("TP1", plan["execution_cost_target_rrs"])
                self.assertGreaterEqual(
                    plan["execution_cost_target_rrs"]["TP1"],
                    profile.minimum_target_rr,
                )

        profile = replace(
            resolve_momentum_pullback_profile(MODES["scalp"], "M5"),
            commission_rate=0.001,
            slippage_per_oz=0.05,
        )
        constrained = validate_candidate(
            SimpleNamespace(
                direction="BUY",
                level=100.0,
                invalidation_level=99.9,
            ),
            price=100.0,
            atr=1.0,
            structure=SimpleNamespace(
                swing_highs=[(10, 101.8)],
                swing_lows=[(20, 99.0)],
                supports=[],
                resistances=[],
            ),
            profile=profile,
            spread=0.02,
        )
        self.assertFalse(constrained["valid"])
        self.assertIn(
            "fee/slippage-adjusted",
            " ".join(constrained["rejection_reasons"]).lower(),
        )

    def test_atr_period_requires_a_full_window_of_completed_candles(self):
        highs = [101.0] * 40
        lows = [99.0] * 40
        closes = [100.0] * 40

        self.assertIsNone(atr_series(highs, lows, closes, period=40)[-1])
        self.assertEqual(atr_series(highs, lows, closes, period=14)[-1], 2.0)


if __name__ == "__main__":
    unittest.main()
