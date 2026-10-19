"""Part 2 risk, event-window, structural-target, and lifecycle tests."""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import trade_tracker
from src.analysis.event_risk import assess_event_risk
from src.analysis.modes import MODES, resolve_momentum_pullback_profile
from src.analysis.risk_policy import (
    RISK_POLICY,
    RiskPolicy,
    calculate_position_size,
)
from src.analysis.weighted.validation import validate_candidate


class RiskPolicyTests(unittest.TestCase):
    def test_defaults_and_invalid_policy_are_checked(self):
        self.assertEqual(RISK_POLICY.risk_per_trade_fraction, 0.005)
        self.assertEqual(RISK_POLICY.max_suggested_risk_fraction, 0.01)
        with self.assertRaises(ValueError):
            RiskPolicy(
                risk_per_trade_fraction=0.02,
                max_suggested_risk_fraction=0.01,
            ).validate()
        with self.assertRaises(ValueError):
            RiskPolicy(max_simultaneous_setups=True).validate()
        with self.assertRaises(ValueError):
            RiskPolicy(event_filter_before_minutes=-1).validate()

    def test_mode_profiles_reject_invalid_threshold_and_stop_configuration(self):
        with self.assertRaises(ValueError):
            replace(MODES["intraday"], analysis_strong_score=58)
        with self.assertRaises(ValueError):
            replace(
                MODES["swing"].momentum_pullback,
                maximum_stop_distance_atr=0.2,
            )

    def test_size_requires_real_account_and_contract_inputs(self):
        unavailable = calculate_position_size(
            equity=None,
            entry=2000,
            stop=1990,
            contract_size_oz_per_lot=None,
            lot_step=None,
            minimum_lot=None,
        )
        self.assertFalse(unavailable["available"])

        size = calculate_position_size(
            equity=10_000,
            entry=2000,
            stop=1990,
            contract_size_oz_per_lot=100,
            lot_step=0.01,
            minimum_lot=0.01,
        )
        self.assertTrue(size["available"])
        self.assertEqual(size["lots"], 0.05)
        self.assertEqual(size["cash_risk"], 50.0)

    def test_all_four_modes_use_the_requested_minimum_target_rr(self):
        expected = {
            "scalp": 1.3,
            "intraday": 1.5,
            "swing": 2.0,
            "position": 2.0,
        }
        for mode, minimum_rr in expected.items():
            with self.subTest(mode=mode):
                self.assertEqual(
                    MODES[mode].momentum_pullback.minimum_target_rr,
                    minimum_rr,
                )

    def test_manual_event_schedule_is_never_presented_as_a_live_feed(self):
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        unavailable = assess_event_risk(now=now, schedule="")
        self.assertEqual(unavailable["status"], "UNAVAILABLE_NO_CALENDAR_FEED")
        self.assertFalse(unavailable["blocks_signal"])

        blocked = assess_event_risk(
            now=now,
            schedule="2026-10-16T12:05:00Z|US CPI",
        )
        self.assertEqual(blocked["status"], "BLOCKED_CONFIGURED_EVENT")
        self.assertTrue(blocked["blocks_signal"])
        self.assertEqual(blocked["source"], "operator_supplied_schedule")

        malformed = assess_event_risk(now=now, schedule="next Friday|FOMC")
        self.assertEqual(malformed["status"], "INVALID_OPERATOR_SCHEDULE")
        self.assertTrue(malformed["blocks_signal"])


class StructuralTargetTests(unittest.TestCase):
    def test_all_targets_are_real_structural_levels_with_individual_rr(self):
        profile = resolve_momentum_pullback_profile(MODES["intraday"], "M15")
        structure = SimpleNamespace(
            swing_highs=[(10, 106.0), (20, 110.0), (30, 115.0)],
            swing_lows=[(12, 98.0)],
            resistances=[],
            supports=[],
        )
        candidate = SimpleNamespace(
            direction="BUY",
            invalidation_level=99.5,
            level=100.0,
        )
        plan = validate_candidate(
            candidate,
            price=100.0,
            atr=1.0,
            structure=structure,
            profile=profile,
            spread=0.05,
            preferred_rr=1.8,
        )

        self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
        self.assertGreater(plan["stop_loss"], 0)
        self.assertLess(plan["stop_loss"], plan["entry"])
        self.assertGreater(plan["take_profit"], plan["entry"])
        self.assertEqual(plan["tp1"], plan["take_profit"])
        self.assertGreater(plan["tp2"], plan["tp1"])
        self.assertGreater(plan["tp3"], plan["tp2"])
        self.assertEqual(set(plan["target_rrs"]), {"TP1", "TP2", "TP3"})
        self.assertEqual(
            plan["target_rrs"]["TP1"],
            round(
                abs(plan["tp1"] - plan["entry"])
                / abs(plan["entry"] - plan["stop_loss"]),
                2,
            ),
        )
        self.assertGreaterEqual(
            plan["reference_spread_target_rrs"]["TP1"],
            profile.minimum_target_rr,
        )

    def test_sell_plan_uses_real_lower_structure_and_keeps_stop_above_invalidation(self):
        profile = resolve_momentum_pullback_profile(MODES["intraday"], "M15")
        structure = SimpleNamespace(
            swing_highs=[(10, 102.0)],
            swing_lows=[(12, 96.5), (20, 93.0), (30, 89.0)],
            resistances=[],
            supports=[],
        )
        candidate = SimpleNamespace(
            direction="SELL",
            invalidation_level=100.5,
            level=100.0,
        )

        plan = validate_candidate(
            candidate,
            price=100.0,
            atr=1.0,
            structure=structure,
            profile=profile,
            spread=0.05,
        )

        self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
        self.assertGreater(plan["stop_loss"], plan["invalidation_level"])
        self.assertGreater(plan["stop_loss"], plan["entry"])
        self.assertLess(plan["tp1"], plan["entry"])
        self.assertLess(plan["tp2"], plan["tp1"])
        self.assertLess(plan["tp3"], plan["tp2"])
        self.assertEqual(set(plan["target_rrs"]), {"TP1", "TP2", "TP3"})
        self.assertGreaterEqual(plan["target_rrs"]["TP1"], profile.minimum_target_rr)
        self.assertEqual(
            plan["target_rrs"]["TP3"],
            round(
                abs(plan["tp3"] - plan["entry"])
                / abs(plan["entry"] - plan["stop_loss"]),
                2,
            ),
        )

    def test_buy_and_sell_stops_expand_to_the_mode_atr_floor(self):
        profile = resolve_momentum_pullback_profile(MODES["intraday"], "M15")
        cases = (
            (
                "BUY",
                99.90,
                SimpleNamespace(
                    swing_highs=[(10, 102.0), (20, 105.0), (30, 110.0)],
                    swing_lows=[(12, 99.90)],
                    resistances=[],
                    supports=[],
                ),
            ),
            (
                "SELL",
                100.10,
                SimpleNamespace(
                    swing_highs=[(10, 100.10)],
                    swing_lows=[(12, 98.0), (20, 95.0), (30, 90.0)],
                    resistances=[],
                    supports=[],
                ),
            ),
        )

        for direction, invalidation, structure in cases:
            with self.subTest(direction=direction):
                plan = validate_candidate(
                    SimpleNamespace(
                        direction=direction,
                        invalidation_level=invalidation,
                        level=100.0,
                    ),
                    price=100.0,
                    atr=1.0,
                    structure=structure,
                    profile=profile,
                    spread=None,
                    preferred_rr=1.8,
                )
                self.assertTrue(plan["valid"], plan.get("rejection_reasons"))
                self.assertTrue(plan["stop_widened_to_atr_floor"])
                self.assertGreaterEqual(
                    abs(plan["entry"] - plan["stop_loss"]),
                    profile.minimum_stop_distance_atr - 0.01,
                )
                self.assertGreaterEqual(
                    plan["rr"],
                    profile.minimum_target_rr,
                )
                if direction == "BUY":
                    self.assertLess(plan["stop_loss"], plan["invalidation_level"])
                    self.assertLess(plan["tp1"], 102.0)
                else:
                    self.assertGreater(plan["stop_loss"], plan["invalidation_level"])
                    self.assertGreater(plan["tp1"], 98.0)

    def test_wider_stop_returns_wait_when_structure_cannot_support_target(self):
        profile = resolve_momentum_pullback_profile(MODES["intraday"], "M15")
        plan = validate_candidate(
            SimpleNamespace(
                direction="BUY",
                invalidation_level=99.90,
                level=100.0,
            ),
            price=100.0,
            atr=1.0,
            structure=SimpleNamespace(
                swing_highs=[(10, 100.50)],
                swing_lows=[(12, 99.90)],
                resistances=[],
                supports=[],
            ),
            profile=profile,
            spread=None,
            preferred_rr=1.8,
        )
        self.assertFalse(plan["valid"])
        self.assertTrue(
            any("R:R" in reason for reason in plan["rejection_reasons"]),
            plan["rejection_reasons"],
        )


class TradeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        with open(self.tmp.name, "w", encoding="utf-8") as handle:
            json.dump({"trades": []}, handle)
        self.path_patch = patch.object(trade_tracker, "TRADES_PATH", self.tmp.name)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        os.unlink(self.tmp.name)

    def _open_pending_buy(self, **kwargs):
        return trade_tracker.open_trade(
            direction="BUY",
            entry=100.0,
            limit_entry=100.0,
            sl=90.0,
            tp1=120.0,
            tp2=130.0,
            timeframe="M15",
            confidence=70,
            rr_ratio=2.0,
            account_id=7788,
            waiting_for_entry=True,
            **kwargs,
        )

    def test_pending_entry_needs_a_real_spot_touch_before_becoming_active(self):
        self.assertTrue(self._open_pending_buy())
        self.assertEqual(
            trade_tracker.get_active_trades(7788)[0]["lifecycle_status"],
            "WAITING FOR ENTRY",
        )

        no_touch = trade_tracker.check_trades(
            101.0, account_id=7788, symbol="XAU/USD", price_source="spot"
        )
        self.assertEqual(no_touch, [])
        self.assertEqual(
            trade_tracker.get_all_trades(7788)[0]["status"],
            "waiting_for_entry",
        )

        touched = trade_tracker.check_trades(
            99.9,
            account_id=7788,
            symbol="XAU/USD",
            price_source="spot",
            bid=99.8,
            ask=100.0,
        )
        self.assertEqual([event["event"] for event in touched], ["ENTRY"])
        trade = trade_tracker.get_all_trades(7788)[0]
        self.assertEqual(trade["lifecycle_status"], "ACTIVE")
        self.assertFalse(trade["entry_fill_confirmed"])

    def test_pre_entry_invalidation_expires_the_pending_plan_without_a_fill(self):
        self.assertTrue(self._open_pending_buy(invalidation_level=101.0))
        events = trade_tracker.check_trades(
            100.5, account_id=7788, symbol="XAU/USD", price_source="spot"
        )
        self.assertEqual([event["event"] for event in events], ["INVALIDATED"])
        trade = trade_tracker.get_all_trades(7788)[0]
        self.assertEqual(trade["status"], "invalidated")
        self.assertEqual(trade["lifecycle_status"], "INVALIDATED")
        self.assertFalse(trade.get("entry_fill_confirmed", False))

    def test_expiry_and_account_setup_cap(self):
        self.assertTrue(
            trade_tracker.open_trade(
                "BUY", 100, 90, 120, 130, "M15", 75, 2,
                account_id=7788,
            )
        )
        trades = trade_tracker.get_all_trades(7788)
        trades[0]["opened_at"] = 0
        with open(self.tmp.name, "w", encoding="utf-8") as handle:
            json.dump({"trades": trades}, handle)
        with patch.object(trade_tracker.time, "time", return_value=10**10):
            events = trade_tracker.check_trades(
                100, account_id=7788, symbol="XAU/USD", price_source="spot"
            )
        self.assertEqual(events[0]["event"], "EXPIRED")
        self.assertEqual(
            trade_tracker.get_all_trades(7788)[0]["lifecycle_status"], "EXPIRED"
        )

        with patch.object(
            trade_tracker,
            "RISK_POLICY",
            replace(RISK_POLICY, max_simultaneous_setups=2),
        ):
            for timeframe in ("M1", "M3"):
                self.assertTrue(
                    trade_tracker.open_trade(
                        "BUY", 100, 90, 120, 130, timeframe, 75, 2,
                        account_id=9999,
                    )
                )
            self.assertFalse(
                trade_tracker.open_trade(
                    "BUY", 100, 90, 120, 130, "M5", 75, 2,
                    account_id=9999,
                )
            )

    def test_account_aggregate_risk_cap_applies_before_another_setup_is_saved(self):
        policy = replace(
            RISK_POLICY,
            max_total_risk_fraction=0.01,
            max_simultaneous_setups=4,
        )
        with patch.object(trade_tracker, "RISK_POLICY", policy):
            self.assertTrue(
                trade_tracker.open_trade(
                    "BUY", 100, 90, 120, 130, "M15", 75, 2,
                    account_id=4411, risk_fraction=0.007,
                )
            )
            self.assertFalse(
                trade_tracker.open_trade(
                    "SELL", 100, 110, 80, 70, "H1", 75, 2,
                    account_id=4411, risk_fraction=0.004,
                )
            )
        self.assertEqual(len(trade_tracker.get_all_trades(4411)), 1)

    def test_persisted_trade_cannot_bypass_its_actual_rr_floor(self):
        opened = trade_tracker.open_trade(
            "BUY", 100, 90, 110, 120, "M15", 75, 5,
            account_id=8822, minimum_rr=1.5,
        )
        self.assertFalse(opened)

    def test_persistence_rejects_clustered_targets_and_records_spread_screen(self):
        rejected = trade_tracker.open_trade(
            "BUY", 100, 90, 120, 120.25, "M15", 75, 2,
            tp3=130,
            account_id=8833,
            minimum_target_gap=0.6,
        )
        self.assertFalse(rejected)

        opened = trade_tracker.open_trade(
            "BUY", 100, 90, 120, 130, "M15", 75, 2,
            tp3=140,
            atr=10,
            account_id=8834,
            minimum_target_gap=0.6,
            reference_spread=0.05,
            reference_spread_status="passed",
            reference_spread_rr=1.98,
            stop_widened_to_atr_floor=True,
        )
        self.assertTrue(opened)
        persisted = trade_tracker.get_all_trades(8834)[0]
        self.assertEqual(persisted["reference_spread"], 0.05)
        self.assertEqual(persisted["reference_spread_status"], "passed")
        self.assertEqual(persisted["reference_spread_rr"], 1.98)
        self.assertEqual(persisted["minimum_target_gap"], 0.6)
        self.assertEqual(persisted["atr"], 10)
        self.assertEqual(persisted["risk_distance"], 10)
        self.assertEqual(persisted["tp1_distance"], 20)
        self.assertEqual(persisted["stop_distance_atr"], 1.0)
        self.assertTrue(persisted["stop_widened_to_atr_floor"])


if __name__ == "__main__":
    unittest.main()
