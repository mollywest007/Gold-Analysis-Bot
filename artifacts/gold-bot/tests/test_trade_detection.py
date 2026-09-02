"""Regression tests for entry de-duplication and candle-extreme exits.

The tests redirect the tracker to a temporary JSON file, so they never mutate
the bot's persisted trade history.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import trade_tracker


class TradeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        with open(self.tmp.name, "w") as f:
            json.dump({"trades": []}, f)
        self.path_patch = patch.object(trade_tracker, "TRADES_PATH", self.tmp.name)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        os.unlink(self.tmp.name)

    def _open_buy(self, timeframe="H1", tp3=130.0):
        return trade_tracker.open_trade(
            direction="BUY",
            entry=100.0,
            sl=90.0,
            tp1=110.0,
            tp2=120.0,
            tp3=tp3,
            timeframe=timeframe,
            confidence=85,
            rr_ratio=1.0,
        )

    def test_same_timeframe_second_entry_is_rejected_even_when_far_away(self):
        self.assertTrue(self._open_buy())
        self.assertFalse(
            trade_tracker.open_trade(
                direction="BUY",
                entry=150.0,
                sl=140.0,
                tp1=160.0,
                tp2=170.0,
                tp3=180.0,
                timeframe="H1",
                confidence=90,
                rr_ratio=1.0,
                atr=50.0,
            )
        )
        self.assertEqual(len(trade_tracker.get_all_trades()), 1)

    def test_active_trade_query_matches_timeframe_ownership(self):
        self.assertTrue(self._open_buy(timeframe="M15", tp3=130.0))
        self.assertTrue(self._open_buy(timeframe="M30", tp3=None))

        trades = trade_tracker.get_all_trades()
        trades[0]["status"] = "tp2_hit"
        trades[0]["tp2_hit"] = True
        with open(self.tmp.name, "w") as f:
            json.dump({"trades": trades}, f)

        active = trade_tracker.get_active_trades()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["timeframe"], "M15")

    def test_no_post_entry_candle_does_not_trigger_old_wick(self):
        self.assertTrue(self._open_buy())
        events = trade_tracker.check_trades(
            100.0,
            # No timeframe extreme means there is no verified post-entry wick.
            tf_extremes={},
        )
        self.assertEqual(events, [])
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "open")

    def test_unverified_fallback_extremes_do_not_close_trade(self):
        self.assertTrue(self._open_buy())
        events = trade_tracker.check_trades(
            100.0,
            recent_high=101.0,
            recent_low=89.0,
            tf_extremes={},
        )
        self.assertEqual(events, [])
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "open")

    def test_post_entry_wick_triggers_target(self):
        self.assertTrue(self._open_buy())
        events = trade_tracker.check_trades(
            100.0,
            tf_extremes={"H1": (111.0, 99.0)},
        )
        self.assertEqual([event["event"] for event in events], ["TP1"])
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "tp1_hit")

    def test_stop_wick_triggers_stop_for_sell(self):
        self.assertTrue(
            trade_tracker.open_trade(
                direction="SELL",
                entry=100.0,
                sl=110.0,
                tp1=90.0,
                tp2=80.0,
                tp3=70.0,
                timeframe="M15",
                confidence=85,
                rr_ratio=1.0,
            )
        )
        events = trade_tracker.check_trades(
            100.0,
            tf_extremes={"M15": (111.0, 99.0)},
        )
        self.assertEqual([event["event"] for event in events], ["SL"])
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "sl_hit")

    def test_stop_event_closes_once_and_marks_notification_pending(self):
        self.assertTrue(self._open_buy(timeframe="M15"))

        events = trade_tracker.check_trades(
            100.0,
            tf_extremes={"M15": (101.0, 89.0)},
        )
        self.assertEqual([event["event"] for event in events], ["SL"])

        trade = trade_tracker.get_trade_by_id(events[0]["trade"]["id"])
        self.assertFalse(trade_tracker.is_active_trade(trade))
        self.assertEqual(trade["status"], "sl_hit")
        self.assertTrue(trade["result_notification_pending"])

        # A terminal record cannot emit the same SL event again.
        self.assertEqual(
            trade_tracker.check_trades(
                100.0,
                tf_extremes={"M15": (101.0, 89.0)},
            ),
            [],
        )
        self.assertTrue(
            trade_tracker.mark_result_notification_sent(trade["id"])
        )
        self.assertFalse(
            trade_tracker.mark_result_notification_sent(trade["id"])
        )
        self.assertEqual(trade_tracker.get_pending_result_notifications(), [])

    def test_invalid_sl_geometry_cannot_close_a_trade(self):
        self.assertTrue(self._open_buy(timeframe="M15"))
        trades = trade_tracker.get_all_trades()
        trades[0]["sl"] = 110.0
        with open(self.tmp.name, "w") as f:
            json.dump({"trades": trades}, f)

        events = trade_tracker.check_trades(
            100.0,
            tf_extremes={"M15": (120.0, 80.0)},
        )
        self.assertEqual(events, [])
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "open")

    def test_reentry_is_allowed_after_genuine_terminal_tp2(self):
        self.assertTrue(self._open_buy(tp3=None))
        events = trade_tracker.check_trades(
            100.0,
            tf_extremes={"H1": (121.0, 99.0)},
        )
        self.assertEqual([event["event"] for event in events], ["TP2"])
        self.assertFalse(trade_tracker.is_active_trade(trade_tracker.get_all_trades()[0]))
        self.assertTrue(
            trade_tracker.open_trade(
                direction="SELL",
                entry=100.0,
                sl=110.0,
                tp1=90.0,
                tp2=80.0,
                tp3=None,
                timeframe="H1",
                confidence=80,
                rr_ratio=1.0,
            )
        )

    def test_tp2_trade_with_tp3_still_owns_timeframe(self):
        self.assertTrue(self._open_buy(tp3=130.0))
        trade = trade_tracker.get_all_trades()[0]
        trade_tracker.check_trades(100.0, tf_extremes={"H1": (121.0, 99.0)})
        self.assertTrue(trade_tracker.is_active_trade(trade_tracker.get_all_trades()[0]))
        self.assertFalse(
            trade_tracker.open_trade(
                direction="SELL",
                entry=100.0,
                sl=110.0,
                tp1=90.0,
                tp2=80.0,
                tp3=None,
                timeframe="H1",
                confidence=80,
                rr_ratio=1.0,
            )
        )

    def test_tp1_is_partial_and_does_not_release_trade_ownership(self):
        self.assertTrue(self._open_buy(tp3=130.0))
        events = trade_tracker.check_trades(
            100.0,
            tf_extremes={"H1": (111.0, 99.0)},
        )
        self.assertEqual([event["event"] for event in events], ["TP1"])
        trade = trade_tracker.get_all_trades()[0]
        self.assertEqual(trade["status"], "tp1_hit")
        self.assertTrue(trade_tracker.is_active_trade(trade))

    def test_invalid_target_ladder_is_rejected(self):
        self.assertFalse(
            trade_tracker.open_trade(
                direction="BUY",
                entry=100.0,
                sl=90.0,
                tp1=110.0,
                tp2=110.0,
                timeframe="M30",
                confidence=80,
                rr_ratio=1.0,
            )
        )

    def test_cancel_trade_rolls_back_only_untouched_open_record(self):
        self.assertTrue(self._open_buy(timeframe="M15"))
        trade = trade_tracker.get_all_trades()[0]

        self.assertTrue(trade_tracker.cancel_trade(trade["id"]))
        self.assertEqual(trade_tracker.get_all_trades(), [])

        self.assertTrue(self._open_buy(timeframe="M15"))
        trade = trade_tracker.get_all_trades()[0]
        trade_tracker.check_trades(
            100.0,
            tf_extremes={"M15": (111.0, 99.0)},
        )
        self.assertFalse(trade_tracker.cancel_trade(trade["id"]))
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "tp1_hit")

    def test_limit_entry_is_preserved_separately_from_tracked_market_entry(self):
        self.assertTrue(
            trade_tracker.open_trade(
                direction="SELL",
                entry=4431.20,
                limit_entry=4444.46,
                sl=4450.00,
                tp1=4418.00,
                tp2=4405.00,
                tp3=4392.00,
                timeframe="M15",
                confidence=85,
                rr_ratio=1.0,
            )
        )
        trade = trade_tracker.get_all_trades()[0]
        self.assertEqual(trade["entry"], 4431.20)
        self.assertEqual(trade["limit_entry"], 4444.46)


if __name__ == "__main__":
    unittest.main()