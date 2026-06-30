import asyncio
import io
import json
import logging
import os
import time
from typing import Set, Dict, Optional

from telegram import InputFile
from telegram.ext import ContextTypes

from src.analysis import analyze
from src.analysis.market_data import get_gold_price, invalidate_cache
from src.utils.formatting import early_entry_card
from src import trade_tracker
from src.image_gen import generate_result_image

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "users.json")

# Tracks the last fired direction per timeframe — persisted to disk.
# Structure: { "H1": "SELL", "M15": "BUY", ... }
_active_signal: Dict[str, str] = {}
# Timestamp of when each TF last fired an alert
_tf_last_fired: Dict[str, float] = {}

SCAN_TIMEFRAMES = ["M5", "M15", "M30", "H1", "H4", "D1"]  # all timeframes

# ── Per-timeframe cooldown — minimum gap between alerts on the same TF ────────
TF_SIGNAL_COOLDOWNS: Dict[str, int] = {
    "M5":  30 * 60,       # 30 min  — M5 candles are only 5 min, must throttle
    "M15": 60 * 60,       # 1 hour
    "M30": 2 * 60 * 60,   # 2 hours
    "H1":  4 * 60 * 60,   # 4 hours
    "H4":  12 * 60 * 60,  # 12 hours
    "D1":  24 * 60 * 60,  # 24 hours
}

# Confluence alert — fires ONE grouped card when this many TFs agree
CONFLUENCE_MIN_TFS = 3

# File that persists signal state across bot restarts
SIGNAL_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "signal_state.json")

# ── Market open/close transition tracking ─────────────────────────────────────
_prev_market_open: Optional[bool] = None
_open_notif_sent_at: float  = 0.0
_close_notif_sent_at: float = 0.0
NOTIF_COOLDOWN = 30 * 60


def _load() -> Set[int]:
    try:
        with open(DATA_PATH, "r") as f:
            return set(json.load(f).get("subscribers", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save(subs: Set[int]) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump({"subscribers": list(subs)}, f)


def register_user(chat_id: int) -> None:
    """Auto-register a user when they start the bot. Alerts are free for everyone."""
    users = _load()
    if chat_id not in users:
        users.add(chat_id)
        _save(users)
        logger.info(f"New user registered: {chat_id} (total: {len(users)})")


def user_count() -> int:
    return len(_load())


def _load_signal_state() -> None:
    """Load persisted signal state from disk — survives bot restarts."""
    global _active_signal, _tf_last_fired
    try:
        with open(SIGNAL_STATE_PATH) as f:
            s = json.load(f)
            _active_signal = s.get("active_signal", {})
            _tf_last_fired = s.get("last_fired", {})
            logger.info(f"Signal state loaded: {_active_signal}")
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_signal_state() -> None:
    """Write signal state to disk so restarts don't re-fire stale signals."""
    os.makedirs(os.path.dirname(SIGNAL_STATE_PATH), exist_ok=True)
    with open(SIGNAL_STATE_PATH, "w") as f:
        json.dump({"active_signal": _active_signal, "last_fired": _tf_last_fired}, f)


def _should_send(tf: str, action: str) -> bool:
    """
    Fire alert only when:
      (a) direction is new for this TF, OR
      (b) same direction but the per-TF cooldown has expired.
    This prevents spam on short TFs (M5 fires every 5 min) and on restarts.
    """
    prev       = _active_signal.get(tf)
    last_fired = _tf_last_fired.get(tf, 0.0)
    cooldown   = TF_SIGNAL_COOLDOWNS.get(tf, 4 * 60 * 60)
    elapsed    = time.time() - last_fired

    if prev == action and elapsed < cooldown:
        logger.info(
            f"[{tf}] Suppressed — same direction, "
            f"{int(elapsed // 60)}m / {int(cooldown // 60)}m cooldown."
        )
        return False
    if prev == action:
        logger.info(f"[{tf}] Cooldown expired ({int(elapsed // 60)}m) — re-firing {action}.")
    return True


def clear_signal_lock(tf: str) -> None:
    """Call after a trade closes so the next signal on this timeframe fires freely."""
    _active_signal.pop(tf, None)
    _tf_last_fired.pop(tf, None)
    _save_signal_state()
    logger.info(f"[{tf}] Signal lock cleared — ready for next entry.")


async def _broadcast_text(bot, subs: Set[int], text: str) -> Set[int]:
    dead: Set[int] = set()
    for chat_id in list(subs):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
            else:
                logger.warning(f"Text send failed for {chat_id}: {e}")
    return dead


async def _broadcast_photo(bot, subs: Set[int], img_bytes: bytes, caption: str) -> Set[int]:
    dead: Set[int] = set()
    for chat_id in list(subs):
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(io.BytesIO(img_bytes), filename="xauusd_alert.jpg"),
                caption=caption,
            )
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
            else:
                logger.warning(f"Photo send failed for {chat_id}: {e}")
    return dead


async def _send_result_image(
    bot, subs: Set[int], trade: dict, event: str, exit_price: float,
) -> None:
    direction  = trade["direction"]
    entry      = trade["entry"]
    sl         = trade["sl"]
    tp1        = trade["tp1"]
    tp2        = trade["tp2"]
    confidence = trade.get("confidence", 80)
    timeframe  = trade.get("timeframe", "H1")
    rr_ratio   = trade.get("rr_ratio", 2.0)

    if event == "SL":
        result  = "LOSS"
        caption = (f"STOP LOSS HIT  |  XAU/USD\n"
                   f"{direction}  Entry: {entry:,.2f}  Exit: {exit_price:,.2f}\n"
                   f"Loss: {abs(entry - exit_price):,.2f} pts")
    elif event == "TP2":
        result  = "WIN_TP2"
        caption = (f"ALL TARGETS HIT  |  XAU/USD\n"
                   f"{direction}  Entry: {entry:,.2f}  TP2: {tp2:,.2f}\n"
                   f"Full profit: +{abs(entry - exit_price):,.2f} pts")
    elif event == "TP1_SL":
        result  = "LOSS"
        caption = (f"SL HIT (TP1 was captured)  |  XAU/USD\n"
                   f"{direction}  Entry: {entry:,.2f}  SL: {sl:,.2f}\n"
                   f"TP1 {tp1:,.2f} was hit — SL then triggered at {exit_price:,.2f}")
    else:
        result  = "WIN_TP1"
        caption = (f"TP1 HIT  |  XAU/USD\n"
                   f"{direction}  Entry: {entry:,.2f}  TP1: {tp1:,.2f}\n"
                   f"Partial profit: +{abs(entry - exit_price):,.2f} pts  |  Watching for TP2")

    try:
        img_bytes = generate_result_image(
            direction=direction, entry=entry, sl=sl, tp1=tp1, tp2=tp2,
            exit_price=exit_price, result=result,
            confidence=confidence, timeframe=timeframe, rr_ratio=rr_ratio,
        )
    except Exception as e:
        logger.error(f"Result image generation failed: {e}")
        img_bytes = None

    dead: Set[int] = set()
    for chat_id in list(subs):
        try:
            if img_bytes:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=io.BytesIO(img_bytes),
                    caption=caption,
                )
            else:
                await bot.send_message(chat_id=chat_id,
                                       text=f"<pre>{caption}</pre>", parse_mode="HTML")
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
            else:
                logger.warning(f"Result send failed for {chat_id}: {e}")

    if dead:
        subs -= dead
        _save(subs)
    logger.info(f"Result image sent: {result} @ {exit_price:.2f} to {len(subs)} sub(s)")


async def send_market_conditions_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a market conditions update every 4 hours to all subscribers."""
    from src.market_hours import market_status
    from src.utils.formatting import market_conditions_card

    ms  = market_status()
    bot = context.application.bot
    subs = _load()

    if not subs:
        logger.info("Market conditions summary: no subscribers.")
        return

    if not ms["is_open"]:
        logger.info("Market conditions summary skipped — market closed.")
        return

    # Skip if a market-open notification was sent very recently (avoids double-up)
    if (time.time() - _open_notif_sent_at) < NOTIF_COOLDOWN:
        logger.info("Market conditions summary skipped — market-open notification sent recently.")
        return

    try:
        a    = await analyze("H1")
        text = market_conditions_card(a)
        dead = await _broadcast_text(bot, subs, text)
        if dead:
            subs -= dead
            _save(subs)
        logger.info(f"Market conditions summary sent to {len(subs)} subscriber(s).")
    except Exception as e:
        logger.error(f"Market conditions summary failed: {e}")


async def _send_market_open_notification(bot, subs: Set[int]) -> None:
    from src.utils.formatting import market_open_card
    logger.info("Sending market-open notification...")
    try:
        a    = await analyze("H1")
        text = market_open_card(a)
        dead = await _broadcast_text(bot, subs, text)
        if dead:
            subs -= dead
            _save(subs)
        logger.info(f"Market-open sent to {len(subs)} subscriber(s).")
    except Exception as e:
        logger.error(f"Market-open notification failed: {e}")


async def _send_market_close_notification(bot, subs: Set[int]) -> None:
    from src.utils.formatting import weekly_closed_recap_text
    logger.info("Sending market-close notification...")
    try:
        text = weekly_closed_recap_text()
        dead = await _broadcast_text(bot, subs, text)
        if dead:
            subs -= dead
            _save(subs)
        logger.info(f"Market-close sent to {len(subs)} subscriber(s).")
    except Exception as e:
        logger.error(f"Market-close notification failed: {e}")


async def _fire_signal(bot, subs: Set[int], a, tf: str) -> None:
    """Broadcast a single-TF entry signal: entry card + live chart."""
    # 1. Send the entry card (same format as /recommend Part 2)
    text = early_entry_card(a)
    dead = await _broadcast_text(bot, subs, text)
    if dead:
        subs -= dead
        _save(subs)

    # 2. Generate and broadcast the chart with trade levels drawn on it
    try:
        from src.chart_generator import generate_chart_image
        entry_display = a.early_entry if a.early_entry and a.early_entry != a.entry else a.entry
        img_bytes = await generate_chart_image(
            timeframe=tf,
            entry=entry_display,
            sl=a.stop_loss,
            tp1=a.tp1,
            tp2=a.tp2,
            tp3=a.tp3,
            direction=a.action,
        )
        if img_bytes:
            sl_dist = abs(entry_display - a.stop_loss)
            rr1 = round(abs(a.tp1 - entry_display) / sl_dist, 1) if sl_dist > 0 and a.tp1 else 0
            tp3_val = getattr(a, "tp3", None)
            rr3 = round(abs(tp3_val - entry_display) / sl_dist, 1) if sl_dist > 0 and tp3_val else 0
            tp3_str = f"   TP3: {tp3_val:,.2f} (1:{rr3})" if tp3_val else ""
            caption = (
                f"XAU/USD {tf}  |  {a.action}  |  Grade {a.setup_quality}\n"
                f"Entry: {entry_display:,.2f}   SL: {a.stop_loss:,.2f}\n"
                f"TP1: {a.tp1:,.2f} (1:{rr1}){tp3_str}"
            )
            dead2 = await _broadcast_photo(bot, subs, img_bytes, caption)
            if dead2:
                subs -= dead2
                _save(subs)
    except Exception as e:
        logger.warning(f"Alert chart failed ({tf}): {e}")

    logger.info(
        f"[{tf}] Alert fired: {a.action} @ {a.entry:.2f} "
        f"grade={a.setup_quality} win={a.win_probability}% "
        f"to {len(subs)} sub(s)"
    )


async def _fire_confluence(bot, subs: Set[int], signal_list: list, direction: str) -> None:
    """
    Broadcast ONE grouped alert when 3+ timeframes align on the same direction.
    signal_list: list of (tf, MarketAnalysis) tuples — all same direction.
    """
    from src.utils.formatting import confluence_alert_card

    # Reference TF priority for the trade plan (most reliable intraday TF first)
    tf_priority = ["H4", "H1", "M30", "D1", "M15", "M5"]
    tfs_present = {tf for tf, _ in signal_list}
    ref_tf = next((tf for tf in tf_priority if tf in tfs_present), signal_list[0][0])
    ref_a  = next(a for tf, a in signal_list if tf == ref_tf)

    text = confluence_alert_card(signal_list, direction, ref_tf)
    dead = await _broadcast_text(bot, subs, text)
    if dead:
        subs -= dead
        _save(subs)

    # One chart using the reference TF
    try:
        from src.chart_generator import generate_chart_image
        img_bytes = await generate_chart_image(
            timeframe=ref_tf,
            entry=ref_a.entry,
            sl=ref_a.stop_loss,
            tp1=ref_a.tp1,
            tp2=ref_a.tp2,
            tp3=getattr(ref_a, "tp3", None),
            direction=direction,
        )
        if img_bytes:
            sl_dist = abs(ref_a.entry - ref_a.stop_loss)
            rr1 = round(abs(ref_a.tp1 - ref_a.entry) / sl_dist, 1) if sl_dist else 0
            tfs_str = " + ".join(tf for tf, _ in signal_list)
            caption = (
                f"CONFLUENCE {direction}  |  XAU/USD  |  {len(signal_list)} TFs\n"
                f"{tfs_str}\n"
                f"Ref {ref_tf}  Entry: {ref_a.entry:,.2f}   SL: {ref_a.stop_loss:,.2f}   TP1: {ref_a.tp1:,.2f} (1:{rr1})"
            )
            dead2 = await _broadcast_photo(bot, subs, img_bytes, caption)
            if dead2:
                subs -= dead2
                _save(subs)
    except Exception as e:
        logger.warning(f"Confluence chart failed: {e}")

    logger.info(
        f"Confluence {direction} alert fired — TFs: {[tf for tf, _ in signal_list]} "
        f"ref={ref_tf}  to {len(subs)} sub(s)"
    )


async def send_startup_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a reconnect card to all subscribers when the bot starts up."""
    from src.utils.formatting import restart_summary_card

    bot  = context.application.bot
    subs = _load()

    if not subs:
        logger.info("Startup summary: no subscribers.")
        return

    try:
        all_trades   = trade_tracker.get_all_trades()
        open_trades  = [t for t in all_trades if t.get("status") == "open"]
        # last 5 closed/expired signals (skip open ones for the history section)
        recent       = [t for t in all_trades if t.get("status") != "open"][:5]
        stats        = trade_tracker.get_stats()
        text         = restart_summary_card(open_trades, recent, stats)
        dead         = await _broadcast_text(bot, subs, text)
        if dead:
            subs -= dead
            _save(subs)
        logger.info(f"Startup summary sent to {len(subs)} subscriber(s).")
    except Exception as e:
        logger.error(f"Startup summary failed: {e}")


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _prev_market_open, _open_notif_sent_at, _close_notif_sent_at

    from src.market_hours import market_status
    ms       = market_status()
    now_open = ms["is_open"]
    bot      = context.application.bot
    subs     = _load()
    now_ts   = time.time()

    # ── Market open/close transitions ─────────────────────────────────────────
    if _prev_market_open is not None and subs:
        if not _prev_market_open and now_open:
            if (now_ts - _open_notif_sent_at) > NOTIF_COOLDOWN:
                _open_notif_sent_at = now_ts
                await _send_market_open_notification(bot, subs)
        elif _prev_market_open and not now_open:
            if (now_ts - _close_notif_sent_at) > NOTIF_COOLDOWN:
                _close_notif_sent_at = now_ts
                await _send_market_close_notification(bot, subs)

    _prev_market_open = now_open

    if not now_open:
        logger.info(f"Alert scan skipped — {ms['status_text']}")
        return

    if not subs:
        logger.info("Alert scan: no subscribers.")
        return

    # ── Check open trades for TP/SL hits ──────────────────────────────────────
    try:
        current_price = await get_gold_price()
        if current_price > 0:
            events = trade_tracker.check_trades(current_price)
            for ev in events:
                await _send_result_image(bot, subs, ev["trade"], ev["event"], ev["exit_price"])
                # Trade closed — unlock this timeframe so the next entry signal fires fresh
                closed_tf = ev["trade"].get("timeframe")
                if closed_tf:
                    clear_signal_lock(closed_tf)
    except Exception as e:
        logger.error(f"Trade check failed: {e}")

    # ── Scan timeframes for entry signals ─────────────────────────────────────
    # Each timeframe alerts independently. One card per timeframe per direction
    # change — lock releases only when the signal flips or the trade closes.
    analyses = await asyncio.gather(
        *[_safe_analyze(tf) for tf in SCAN_TIMEFRAMES],
        return_exceptions=True,
    )

    # Pass 1 — log all results, collect newly-triggered signals
    new_signals: list = []   # (tf, MarketAnalysis) pairs that should fire this cycle
    state_changed = False

    for tf, a in zip(SCAN_TIMEFRAMES, analyses):
        if a is None or isinstance(a, Exception):
            if isinstance(a, Exception):
                logger.error(f"[{tf}] Analysis raised: {a}")
            continue

        logger.info(
            f"[{tf}] scan: action={a.action} grade={a.setup_quality} "
            f"conf={a.confidence}% win={a.win_probability}% adx={a.adx:.1f}"
        )

        if a.action not in ("BUY", "SELL"):
            if tf in _active_signal:
                logger.info(f"[{tf}] Signal cleared (now {a.action}) — lock released.")
                _active_signal.pop(tf)
                state_changed = True
            else:
                logger.info(f"[{tf}] No signal.")
            continue

        if _should_send(tf, a.action):
            new_signals.append((tf, a))

    if state_changed:
        _save_signal_state()

    if not new_signals:
        return

    # Pass 2 — confluence check: group same-direction signals into one alert
    buy_new  = [(tf, a) for tf, a in new_signals if a.action == "BUY"]
    sell_new = [(tf, a) for tf, a in new_signals if a.action == "SELL"]

    async def _process(sig_list: list, direction: str) -> None:
        """Fire alert (confluence or individual) and record state + open trades."""
        if len(sig_list) >= CONFLUENCE_MIN_TFS:
            await _fire_confluence(bot, subs, sig_list, direction)
        else:
            for tf, a in sig_list:
                await _fire_signal(bot, subs, a, tf)

        now_ts = time.time()
        for tf, a in sig_list:
            _active_signal[tf] = direction
            _tf_last_fired[tf] = now_ts
            try:
                invalidate_cache(tf)
                trade_tracker.open_trade(
                    direction=a.action, entry=a.entry, sl=a.stop_loss,
                    tp1=a.tp1, tp2=a.tp2, timeframe=tf,
                    confidence=a.confidence, rr_ratio=a.rr_ratio,
                )
            except Exception as e:
                logger.error(f"Trade open failed ({tf}): {e}")
        _save_signal_state()

    if buy_new:
        await _process(buy_new, "BUY")
    if sell_new:
        await _process(sell_new, "SELL")


async def _safe_analyze(tf: str):
    try:
        return await analyze(tf)
    except Exception as e:
        logger.error(f"Alert scan — analysis failed for {tf}: {e}")
        return None


# Load persisted signal state on module import
_load_signal_state()
