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

    def test_implausible_live_quote_cannot_close_a_gold_trade(self):
        self.assertTrue(
            trade_tracker.open_trade(
                direction="BUY",
                entry=4477.1,
                sl=4470.51,
                tp1=4486.99,
                tp2=4493.58,
                tp3=4500.17,
                timeframe="M15",
                confidence=85,
                rr_ratio=1.0,
            )
        )

        events = trade_tracker.check_trades(2350.0, tf_extremes={})

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

    def test_terminal_exit_persists_the_evidence_used(self):
        self.assertTrue(self._open_buy())

        events = trade_tracker.check_trades(
            101.0,
            tf_extremes={"H1": (105.0, 89.5)},
        )

        self.assertEqual(events[0]["event"], "SL")
        evidence = trade_tracker.get_all_trades()[0]["exit_evidence"]
        self.assertEqual(evidence["source"], "verified_candle")
        self.assertEqual(evidence["timeframe"], "H1")
        self.assertEqual(evidence["high"], 105.0)
        self.assertEqual(evidence["low"], 89.5)
        self.assertEqual(evidence["spot"], 101.0)

    def test_spot_only_terminal_exit_records_spot_evidence(self):
        self.assertTrue(self._open_buy())

        events = trade_tracker.check_trades(89.0, tf_extremes={})

        self.assertEqual(events[0]["event"], "SL")
        evidence = trade_tracker.get_all_trades()[0]["exit_evidence"]
        self.assertEqual(evidence["source"], "live_spot")
        self.assertEqual(evidence["high"], 89.0)
        self.assertEqual(evidence["low"], 89.0)
        self.assertEqual(evidence["spot"], 89.0)

    def test_single_structural_target_closes_as_final_tp1_using_buy_bid(self):
        self.assertTrue(
            trade_tracker.open_trade(
                direction="BUY",
                entry=4400.0,
                sl=4390.0,
                tp1=4410.0,
                tp2=None,
                tp3=None,
                timeframe="M15",
                confidence=80,
                rr_ratio=1.0,
            )
        )

        # The midpoint is through TP1, but the executable sell-side bid is not.
        self.assertEqual(
            trade_tracker.check_trades(
                4410.0, bid=4409.9, ask=4410.1, tf_extremes={}
            ),
            [],
        )
        events = trade_tracker.check_trades(
            4410.2, bid=4410.1, ask=4410.3, tf_extremes={}
        )

        self.assertEqual([event["event"] for event in events], ["TP1_FINAL"])
        trade = trade_tracker.get_all_trades()[0]
        self.assertEqual(trade["status"], "tp1_final_hit")
        self.assertEqual(trade["close_reason"], "take_profit")
        self.assertIsNone(trade["tp2"])
        self.assertEqual(trade["exit_evidence"]["source"], "live_bid_ask")

    def test_single_structural_target_uses_sell_ask_and_gold_symbol_only(self):
        self.assertTrue(
            trade_tracker.open_trade(
                direction="SELL",
                entry=4400.0,
                sl=4410.0,
                tp1=4390.0,
                tp2=None,
                timeframe="M15",
                confidence=80,
                rr_ratio=1.0,
            )
        )
        trade = trade_tracker.get_all_trades()[0]
        self.assertEqual(trade["symbol"], "XAU/USD")

        self.assertEqual(
            trade_tracker.check_trades(
                4389.8,
                bid=4389.7,
                ask=4389.9,
                symbol="BTC/USD",
                tf_extremes={"M15": (4389.0, 4388.0)},
            ),
            [],
        )
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "open")

        # SELL positions exit at ask; a bid alone crossing TP1 is insufficient.
        self.assertEqual(
            trade_tracker.check_trades(
                4389.8, bid=4389.7, ask=4390.1, tf_extremes={}
            ),
            [],
        )
        events = trade_tracker.check_trades(
            4389.8, bid=4389.6, ask=4389.9, tf_extremes={}
        )
        self.assertEqual([event["event"] for event in events], ["TP1_FINAL"])

    def test_trade_open_rejects_non_gold_symbol(self):
        self.assertFalse(
            trade_tracker.open_trade(
                direction="BUY",
                entry=100.0,
                sl=90.0,
                tp1=110.0,
                tp2=None,
                timeframe="M15",
                confidence=80,
                rr_ratio=1.0,
                symbol="BTC/USD",
            )
        )

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

    def test_post_tp1_retrace_to_entry_does_not_close_before_original_stop(self):
        self.assertTrue(
            trade_tracker.open_trade(
                direction="BUY",
                entry=4185.20,
                sl=4170.29,
                tp1=4207.56,
                tp2=4222.47,
                tp3=4237.38,
                timeframe="M15",
                confidence=85,
                rr_ratio=1.0,
            )
        )
        trades = trade_tracker.get_all_trades()
        trades[0]["status"] = "tp1_hit"
        trades[0]["tp1_hit"] = True
        with open(self.tmp.name, "w") as f:
            json.dump({"trades": trades}, f)

        # This reproduces the previous trade's evidence: the candle dipped
        # below entry (4185.20), remained above the original SL (4170.29), and
        # reached TP2 while the live quote was still profitable.
        events = trade_tracker.check_trades(
            4217.299805,
            tf_extremes={"M15": (4227.3, 4184.0)},
        )

        self.assertEqual([event["event"] for event in events], ["TP2"])
        self.assertEqual(trade_tracker.get_all_trades()[0]["status"], "tp2_hit")
        self.assertTrue(trade_tracker.get_all_trades()[0]["tp2_hit"])
        self.assertTrue(
            trade_tracker.is_active_trade(trade_tracker.get_all_trades()[0])
        )

    def test_original_stop_still_closes_trade_after_tp1(self):
        self.assertTrue(self._open_buy(timeframe="M15"))
        trades = trade_tracker.get_all_trades()
        trades[0]["status"] = "tp1_hit"
        trades[0]["tp1_hit"] = True
        with open(self.tmp.name, "w") as f:
            json.dump({"trades": trades}, f)

        events = trade_tracker.check_trades(
            90.0,
            tf_extremes={"M15": (101.0, 90.0)},
        )

        self.assertEqual([event["event"] for event in events], ["TP1_SL"])
        self.assertEqual(events[0]["exit_price"], 90.0)
        closed_trade = trade_tracker.get_all_trades()[0]
        self.assertEqual(closed_trade["status"], "tp1_sl_hit")
        self.assertEqual(closed_trade["close_reason"], "stop_loss")
        self.assertEqual(closed_trade["exit_evidence"]["low"], 90.0)

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

    def test_final_target_marks_trade_all_tp_hit_and_starts_reanalysis_window(self):
        self.assertTrue(self._open_buy(tp3=130.0))

        with patch.object(trade_tracker.time, "time", return_value=1000.0):
            events = trade_tracker.check_trades(
                100.0,
                tf_extremes={"H1": (131.0, 99.0)},
            )

        self.assertEqual([event["event"] for event in events], ["TP3"])
        trade = trade_tracker.get_all_trades()[0]
        self.assertEqual(trade["status"], "tp3_hit")
        self.assertTrue(trade["all_tp_hit"])
        self.assertEqual(trade["completion_label"], "ALL TP HIT")
        self.assertEqual(trade["tp_reanalysis_until"], 1600.0)

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