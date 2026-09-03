"""Regression tests for automatic notification delivery and retry behavior."""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import alerts


class NotificationPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.users_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.users_file.close()
        with open(self.users_file.name, "w") as f:
            json.dump({"subscribers": [123], "disabled": []}, f)

        self.data_patch = patch.object(alerts, "DATA_PATH", self.users_file.name)
        self.data_patch.start()
        self.forming_patch = patch.object(alerts, "_forming_alert_sent", {})
        self.forming_patch.start()
        self.shift_patch = patch.object(alerts, "_momentum_shift_warned", {})
        self.shift_patch.start()

    def tearDown(self):
        self.shift_patch.stop()
        self.forming_patch.stop()
        self.data_patch.stop()
        os.unlink(self.users_file.name)

    def test_simulated_ohlcv_is_never_used_for_exit_detection(self):
        simulated = SimpleNamespace(
            highs=[4405.0],
            lows=[4300.0],
            timestamps=[1786946400.0],
            is_simulated=True,
        )

        self.assertIsNone(
            alerts._post_entry_tf_extremes(
                simulated,
                current_price=4395.0,
                opened_at=1786946400.0,
            )
        )

    def test_exit_extremes_ignore_old_post_entry_wicks(self):
        data = SimpleNamespace(
            highs=[500.0, 111.0, 112.0, 113.0, 114.0],
            lows=[1.0, 99.0, 98.0, 97.0, 96.0],
            timestamps=[100.0, 200.0, 300.0, 400.0, 500.0],
            is_simulated=False,
        )

        self.assertEqual(
            alerts._post_entry_tf_extremes(
                data,
                current_price=110.0,
                opened_at=150.0,
            ),
            (114.0, 96.0),
        )

    def test_old_post_entry_wick_is_not_reused_for_exit(self):
        data = SimpleNamespace(
            highs=[120.0, 111.0],
            lows=[80.0, 99.0],
            timestamps=[200.0, 300.0],
            is_simulated=False,
        )

        self.assertEqual(
            alerts._post_entry_tf_extremes(
                data,
                current_price=100.0,
                opened_at=150.0,
            ),
            (111.0, 99.0),
        )

    def test_stale_ohlcv_is_reduced_to_spot_only_for_exit_detection(self):
        data = SimpleNamespace(
            highs=[120.0],
            lows=[80.0],
            timestamps=[900.0],
            fetched_at=100.0,
            is_simulated=False,
        )

        self.assertEqual(
            alerts._post_entry_tf_extremes(
                data,
                current_price=100.0,
                opened_at=0.0,
                timeframe="M15",
                now=2000.0,
            ),
            (100.0, 100.0),
        )

    def test_signal_lock_is_removed_without_an_active_persisted_trade(self):
        active_signal = {"M15": "BUY", "H1": "SELL"}
        last_fired = {"M15": 100.0, "H1": 200.0}
        pending = {"H1": "SELL"}

        alerts._reconcile_signal_locks(
            active_signal,
            last_fired,
            pending,
            [{"timeframe": "H1", "status": "open"}],
        )

        self.assertEqual(active_signal, {"H1": "SELL"})
        self.assertEqual(last_fired, {"H1": 200.0})
        self.assertEqual(pending, {"H1": "SELL"})

    def test_tp3_starts_ten_minute_reanalysis_cooldown(self):
        state = alerts.AccountAlertState(active_signal={"H1": "SELL"})

        with patch.object(alerts.time, "time", return_value=1000.0):
            deadline = alerts.clear_signal_lock(
                "H1",
                after_tp=True,
                state=state,
            )

        self.assertEqual(deadline, 1600.0)
        self.assertEqual(state.tp_cooldown_until["H1"], 1600.0)
        self.assertEqual(state.closed_signal["H1"], "SELL")

        with patch.object(alerts.time, "time", return_value=1001.0):
            self.assertFalse(
                alerts._should_send("H1", "SELL", state=state)
            )

        # The same direction is re-armed only after the full ten-minute
        # analysis window has elapsed.
        with patch.object(alerts.time, "time", return_value=1601.0):
            self.assertTrue(
                alerts._should_send("H1", "SELL", state=state)
            )
        self.assertNotIn("H1", state.tp_cooldown_until)

    def test_legacy_tp3_lock_rearms_after_cooldown(self):
        state = alerts.AccountAlertState(
            closed_signal={"M15": "BUY"},
        )
        legacy_tp3 = {
            "direction": "BUY",
            "timeframe": "M15",
            "status": "tp3_hit",
            "closed_at": 1000.0,
        }

        with patch.object(
            alerts.trade_tracker,
            "get_all_trades",
            return_value=[legacy_tp3],
        ), patch.object(alerts.time, "time", return_value=1700.0):
            self.assertTrue(
                alerts._should_send("M15", "BUY", state=state)
            )

        self.assertNotIn("M15", state.closed_signal)

    def test_persisted_sl_cooldown_is_restored_before_lock_reconciliation(self):
        state = alerts.AccountAlertState(
            active_signal={"H1": "BUY"},
        )
        stopped_trade = {
            "direction": "BUY",
            "timeframe": "H1",
            "status": "sl_hit",
            "cooldown_until": 2000.0,
        }

        with patch.object(alerts.time, "time", return_value=1500.0):
            alerts._reconcile_terminal_cooldowns(
                state.sl_cooldown_until,
                [stopped_trade],
            )

        self.assertEqual(state.sl_cooldown_until["H1"], 2000.0)
        with patch.object(alerts.time, "time", return_value=1500.0):
            self.assertFalse(
                alerts._should_send("H1", "BUY", state=state)
            )

    async def test_sl_result_is_blocked_while_persisted_trade_is_active(self):
        bot = AsyncMock()
        active_trade = {
            "id": "active-sl",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "timeframe": "M15",
            "status": "open",
        }

        with patch.object(
            alerts.trade_tracker,
            "get_trade_by_id",
            return_value=active_trade,
        ), patch.object(
            alerts,
            "_send_result_image",
            new=AsyncMock(),
        ) as send_result:
            delivered = await alerts._send_verified_result_event(
                bot,
                {123},
                active_trade,
                "SL",
                90.0,
            )

        self.assertFalse(delivered)
        send_result.assert_not_awaited()
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    async def test_confirmed_sl_sends_cooldown_status_with_remaining_time(self):
        bot = AsyncMock()
        closed_trade = {
            "id": "closed-sl-cooldown",
            "direction": "SELL",
            "entry": 4441.53,
            "sl": 4458.10,
            "tp1": 4416.68,
            "tp2": 4400.11,
            "timeframe": "M15",
            "status": "sl_hit",
            "closed_at": 900.0,
            "close_reason": "stop_loss",
            "cooldown_notification_pending": True,
            "cooldown_until": 2800.0,
            "cooldown_duration_seconds": 1800.0,
        }

        with patch.object(
            alerts.trade_tracker,
            "get_trade_by_id",
            return_value=closed_trade,
        ), patch.object(
            alerts,
            "_broadcast_text",
            new=AsyncMock(return_value=(set(), True)),
        ) as broadcast, patch.object(
            alerts.trade_tracker,
            "mark_cooldown_notification_sent",
            return_value=True,
        ) as mark_sent, patch.object(
            alerts.time,
            "time",
            return_value=1000.0,
        ):
            delivered = await alerts._send_sl_cooldown_notification(
                bot, {123}, closed_trade, "SL"
            )

        self.assertTrue(delivered)
        text = broadcast.await_args.args[2]
        self.assertIn("STOP LOSS TRIGGERED", text)
        self.assertIn("Cooldown    : ACTIVE for 30 min (2 M15 candles)", text)
        self.assertIn("Remaining   : about 30 min remaining", text)
        self.assertIn("No new M15 entries will open during cooldown.", text)
        mark_sent.assert_called_once_with("closed-sl-cooldown")

    async def test_cooldown_status_is_blocked_while_trade_is_active(self):
        bot = AsyncMock()
        active_trade = {
            "id": "active-cooldown",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "timeframe": "M15",
            "status": "open",
            "cooldown_notification_pending": True,
            "cooldown_until": 2800.0,
            "cooldown_duration_seconds": 1800.0,
        }

        with patch.object(
            alerts.trade_tracker,
            "get_trade_by_id",
            return_value=active_trade,
        ), patch.object(
            alerts,
            "_broadcast_text",
            new=AsyncMock(),
        ) as broadcast:
            delivered = await alerts._send_sl_cooldown_notification(
                bot, {123}, active_trade, "SL"
            )

        self.assertFalse(delivered)
        broadcast.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        bot.send_photo.assert_not_awaited()

    async def test_terminal_sl_result_is_sent_once_only(self):
        bot = AsyncMock()
        closed_trade = {
            "id": "closed-sl",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "timeframe": "M15",
            "status": "sl_hit",
            "result_notification_pending": True,
            "closed_at": 200.0,
            "close_reason": "stop_loss",
        }
        already_sent = {
            **closed_trade,
            "result_notification_pending": False,
            "result_notification_sent_at": 123.0,
        }

        with patch.object(
            alerts.trade_tracker,
            "get_trade_by_id",
            side_effect=[closed_trade, already_sent],
        ), patch.object(
            alerts,
            "_send_result_image",
            new=AsyncMock(return_value=True),
        ) as send_result, patch.object(
            alerts.trade_tracker,
            "mark_result_notification_sent",
            return_value=True,
        ) as mark_sent:
            first = await alerts._send_verified_result_event(
                bot, {123}, closed_trade, "SL", 90.0
            )
            second = await alerts._send_verified_result_event(
                bot, {123}, closed_trade, "SL", 90.0
            )

        self.assertTrue(first)
        self.assertFalse(second)
        send_result.assert_awaited_once()
        mark_sent.assert_called_once_with("closed-sl")

    async def test_status_only_sl_record_is_not_notified(self):
        bot = AsyncMock()
        stale_trade = {
            "id": "stale-sl",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "timeframe": "M15",
            "status": "sl_hit",
            "result_notification_pending": True,
        }

        with patch.object(
            alerts.trade_tracker,
            "get_trade_by_id",
            return_value=stale_trade,
        ), patch.object(
            alerts,
            "_send_result_image",
            new=AsyncMock(),
        ) as send_result:
            delivered = await alerts._send_verified_result_event(
                bot, {123}, stale_trade, "SL", 90.0
            )

        self.assertFalse(delivered)
        send_result.assert_not_awaited()
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    async def test_low_level_sl_sender_also_rejects_active_trade(self):
        bot = AsyncMock()
        active_trade = {
            "id": "active-low-level-sl",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "timeframe": "M15",
            "status": "open",
        }

        delivered = await alerts._send_result_image(
            bot, {123}, active_trade, "SL", 90.0
        )

        self.assertFalse(delivered)
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    def _analysis(self):
        return SimpleNamespace(
            kill_zone="",
            is_kill_zone=False,
            buy_votes=4,
            sell_votes=2,
            price=2350.0,
            adx=25.0,
            confidence=78,
            htf_bias="Bullish",
            early_entry=2348.5,
            limit_entry=0.0,
            ote_high=2352.0,
            ote_low=2344.0,
        )

    async def test_setup_forming_alert_is_deduplicated_after_delivery(self):
        bot = AsyncMock()

        await alerts._send_setup_forming_alert(
            bot, {123}, self._analysis(), "M15", "BUY"
        )
        await alerts._send_setup_forming_alert(
            bot, {123}, self._analysis(), "M15", "BUY"
        )

        self.assertEqual(bot.send_message.await_count, 1)
        self.assertIn("SETUP FORMING", bot.send_message.await_args.kwargs["text"])
        self.assertIn("4/8 core indicators", bot.send_message.await_args.kwargs["text"])
        self.assertIn("Watch limit : 2,348.50", bot.send_message.await_args.kwargs["text"])
        self.assertEqual(alerts._forming_alert_sent["M15"], "BUY")

    async def test_setup_forming_alert_retries_after_delivery_failure(self):
        bot = AsyncMock()
        bot.send_message.side_effect = [RuntimeError("temporary Telegram error"), None]

        await alerts._send_setup_forming_alert(
            bot, {123}, self._analysis(), "M15", "BUY"
        )
        self.assertNotIn("M15", alerts._forming_alert_sent)

        await alerts._send_setup_forming_alert(
            bot, {123}, self._analysis(), "M15", "BUY"
        )

        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(alerts._forming_alert_sent["M15"], "BUY")

    async def test_momentum_shift_warns_once_after_delivery(self):
        bot = AsyncMock()
        trade = {"direction": "BUY", "entry": 2350.0, "sl": 2335.0}

        await alerts._send_momentum_shift_warning(
            bot, {123}, trade, "M15", "SELL"
        )
        await alerts._send_momentum_shift_warning(
            bot, {123}, trade, "M15", "SELL"
        )

        self.assertEqual(bot.send_message.await_count, 1)
        self.assertIn("MOMENTUM SHIFT", bot.send_message.await_args.kwargs["text"])
        self.assertIn("New bias    : SELL forming", bot.send_message.await_args.kwargs["text"])
        self.assertEqual(alerts._momentum_shift_warned["M15"], "SELL")

    async def test_momentum_shift_retries_after_delivery_failure(self):
        bot = AsyncMock()
        bot.send_message.side_effect = [RuntimeError("temporary Telegram error"), None]
        trade = {"direction": "BUY", "entry": 2350.0, "sl": 2335.0}

        await alerts._send_momentum_shift_warning(
            bot, {123}, trade, "M15", "SELL"
        )
        self.assertNotIn("M15", alerts._momentum_shift_warned)

        await alerts._send_momentum_shift_warning(
            bot, {123}, trade, "M15", "SELL"
        )

        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(alerts._momentum_shift_warned["M15"], "SELL")

    async def test_setup_forming_alert_is_reached_by_live_scan(self):
        from src import market_hours

        forming = SimpleNamespace(
            action="WAIT",
            setup_quality="FORMING",
            confidence=78,
            win_probability=0,
            is_simulated=False,
            buy_votes=4,
            sell_votes=2,
            adx=25.0,
            price=2350.0,
            htf_bias="Bullish",
            kill_zone="",
            is_kill_zone=False,
            early_entry=2348.5,
            limit_entry=0.0,
            ote_high=2352.0,
            ote_low=2344.0,
        )
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))

        with patch.object(alerts, "_sync_mode_state"), \
             patch.object(alerts, "get_mode_config", return_value=SimpleNamespace(
                 confluence_min_tfs=3,
             )), \
             patch.object(alerts, "get_scan_timeframes", return_value=["M15"]), \
             patch.object(alerts, "_load", return_value={123}), \
             patch.object(alerts, "_safe_analyze", new=AsyncMock(return_value=forming)), \
             patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=2350.0)), \
             patch.object(alerts.trade_tracker, "get_active_trades", return_value=[]), \
             patch.object(alerts.trade_tracker, "check_trades", return_value=[]), \
             patch.object(
                 market_hours,
                 "market_status",
                 return_value={
                     "is_open": True,
                     "status_text": "MARKET OPEN",
                     "note": "Test session",
                 },
             ):
            await alerts._check_and_alert_once(context)

        self.assertEqual(context.application.bot.send_message.await_count, 1)
        text = context.application.bot.send_message.await_args.kwargs["text"]
        self.assertIn("SETUP FORMING", text)
        self.assertIn("Watch limit : 2,348.50", text)

    async def test_confirmed_momentum_shift_is_reached_by_live_scan(self):
        from src import market_hours

        reversal = SimpleNamespace(
            action="SELL",
            setup_quality="A",
            confidence=85,
            win_probability=70,
            buy_votes=1,
            sell_votes=7,
            adx=25.0,
            is_simulated=False,
            htf_bias="Neutral",
            choch="NONE",
        )
        open_trade = {
            "id": "open-buy",
            "timeframe": "M15",
            "direction": "BUY",
            "entry": 2350.0,
            "sl": 2335.0,
        }
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))

        with patch.object(alerts, "_sync_mode_state"), \
             patch.object(alerts, "get_mode_config", return_value=SimpleNamespace(
                 confluence_min_tfs=3,
                 alert_min_win_probability=60,
                 alert_min_grades=("A+", "A"),
             )), \
             patch.object(alerts, "get_scan_timeframes", return_value=["M15"]), \
             patch.object(alerts, "_load", return_value={123}), \
             patch.object(alerts, "_safe_analyze", new=AsyncMock(return_value=reversal)), \
             patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=2350.0)), \
             patch.object(alerts, "fetch_ohlcv", new=AsyncMock(return_value=None)), \
             patch.object(alerts.trade_tracker, "get_active_trades", return_value=[open_trade]), \
             patch.object(alerts.trade_tracker, "get_all_trades", return_value=[open_trade]), \
             patch.object(alerts.trade_tracker, "check_trades", return_value=[]), \
             patch.object(
                 market_hours,
                 "market_status",
                 return_value={
                     "is_open": True,
                     "status_text": "MARKET OPEN",
                     "note": "Test session",
                 },
             ):
            await alerts._check_and_alert_once(context)

        self.assertEqual(context.application.bot.send_message.await_count, 1)
        text = context.application.bot.send_message.await_args.kwargs["text"]
        self.assertIn("MOMENTUM SHIFT", text)
        self.assertIn("New bias    : SELL confirmed", text)
        self.assertNotIn("XAU/USD  SELL", text.split("MOMENTUM SHIFT", 1)[-1])

    async def test_entry_alert_delivers_card_even_when_chart_is_unavailable(self):
        bot = AsyncMock()
        analysis = SimpleNamespace(
            early_entry=0.0,
            entry=2350.0,
            stop_loss=2335.0,
            tp1=2380.0,
            tp2=2400.0,
            tp3=2420.0,
            action="BUY",
            setup_quality="A",
            win_probability=78,
        )

        with patch.object(alerts, "early_entry_card", return_value="ENTRY ALERT"), \
             patch(
                 "src.chart_generator.generate_chart_image",
                 new=AsyncMock(return_value=None),
             ):
            delivered = await alerts._fire_signal(bot, {123}, analysis, "M15")

        self.assertTrue(delivered)
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertEqual(
            bot.send_message.await_args.kwargs["text"],
            "ENTRY ALERT",
        )

    async def test_entry_alert_is_withheld_when_trade_cannot_be_persisted(self):
        from src import market_hours

        full_signal = SimpleNamespace(
            action="BUY",
            setup_quality="A",
            confidence=85,
            win_probability=75,
            buy_votes=7,
            sell_votes=1,
            adx=25.0,
            is_simulated=False,
            htf_bias="Bullish",
            choch="NONE",
            entry=2350.0,
            stop_loss=2335.0,
            tp1=2380.0,
            tp2=2400.0,
            tp3=2420.0,
            rr_ratio=2.0,
            early_entry=0.0,
            limit_entry=0.0,
            atr=10.0,
        )
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))

        with patch.object(alerts, "_sync_mode_state"), \
             patch.object(alerts, "get_mode_config", return_value=SimpleNamespace(
                 name="intraday",
                 confluence_min_tfs=3,
                 alert_min_win_probability=60,
                 alert_min_grades=("A+", "A"),
             )), \
             patch.object(alerts, "get_scan_timeframes", return_value=["M15"]), \
             patch.object(alerts, "_load", return_value={123}), \
             patch.object(alerts, "_safe_analyze", new=AsyncMock(return_value=full_signal)), \
             patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=2350.0)), \
             patch.object(alerts, "fetch_ohlcv", new=AsyncMock(return_value=None)), \
             patch.object(alerts.trade_tracker, "get_active_trades", return_value=[]), \
             patch.object(alerts.trade_tracker, "get_all_trades", return_value=[]), \
             patch.object(alerts.trade_tracker, "check_trades", return_value=[]), \
             patch.object(alerts.trade_tracker, "open_trade", return_value=False) as open_trade, \
             patch.object(alerts, "_fire_signal", new=AsyncMock()) as fire_signal, \
             patch.object(
                 market_hours,
                 "market_status",
                 return_value={
                     "is_open": True,
                     "status_text": "MARKET OPEN",
                     "note": "Test session",
                 },
             ):
            await alerts._check_and_alert_once(context)

        open_trade.assert_called_once()
        fire_signal.assert_not_awaited()
        context.application.bot.send_message.assert_not_awaited()

    async def test_simulated_setup_does_not_send_any_notification(self):
        from src import market_hours

        simulated = SimpleNamespace(
            action="WAIT",
            setup_quality="FORMING",
            confidence=0,
            win_probability=0,
            is_simulated=True,
            buy_votes=4,
            sell_votes=1,
            adx=25.0,
        )
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))

        with patch.object(alerts, "_sync_mode_state"), \
             patch.object(alerts, "get_mode_config", return_value=SimpleNamespace(
                 confluence_min_tfs=3,
             )), \
             patch.object(alerts, "get_scan_timeframes", return_value=["M15"]), \
             patch.object(alerts, "_load", return_value={123}), \
             patch.object(alerts, "_safe_analyze", new=AsyncMock(return_value=simulated)), \
             patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=2350.0)), \
             patch.object(alerts.trade_tracker, "get_active_trades", return_value=[]), \
             patch.object(
                 market_hours,
                 "market_status",
                 return_value={
                     "is_open": True,
                     "status_text": "MARKET OPEN",
                     "note": "Test session",
                 },
             ), \
             patch.object(alerts, "_send_setup_forming_alert", new=AsyncMock()) as setup_alert:
            await alerts._check_and_alert_once(context)

        setup_alert.assert_not_awaited()

    async def test_simulated_full_signal_does_not_send_momentum_warning(self):
        from src import market_hours

        simulated = SimpleNamespace(
            action="SELL",
            setup_quality="A",
            confidence=85,
            win_probability=70,
            buy_votes=0,
            sell_votes=8,
            adx=25.0,
            is_simulated=True,
        )
        open_trade = {
            "timeframe": "M15",
            "direction": "BUY",
            "entry": 2350.0,
            "sl": 2335.0,
        }
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))

        with patch.object(alerts, "_sync_mode_state"), \
             patch.object(alerts, "get_mode_config", return_value=SimpleNamespace(
                 confluence_min_tfs=3,
             )), \
             patch.object(alerts, "get_scan_timeframes", return_value=["M15"]), \
             patch.object(alerts, "_load", return_value={123}), \
             patch.object(alerts, "_safe_analyze", new=AsyncMock(return_value=simulated)), \
             patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=2350.0)), \
             patch.object(alerts, "fetch_ohlcv", new=AsyncMock(return_value=None)), \
             patch.object(
                 alerts.trade_tracker,
                 "get_active_trades",
                 return_value=[open_trade],
             ), \
             patch.object(alerts.trade_tracker, "check_trades", return_value=[]), \
             patch.object(
                 market_hours,
                 "market_status",
                 return_value={
                     "is_open": True,
                     "status_text": "MARKET OPEN",
                     "note": "Test session",
                 },
             ), \
             patch.object(alerts, "_should_send") as should_send, \
             patch.object(
                 alerts,
                 "_send_momentum_shift_warning",
                 new=AsyncMock(),
             ) as shift_warning:
            await alerts._check_and_alert_once(context)

        should_send.assert_not_called()
        shift_warning.assert_not_awaited()