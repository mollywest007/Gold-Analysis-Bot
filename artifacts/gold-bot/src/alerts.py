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
from src.analysis.market_data import get_gold_price, invalidate_cache, fetch_ohlcv
from src.mode_manager import get_mode, get_mode_config, get_timeframe
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
# A scan can take longer than the 15-second scheduler interval while charts
# are being generated.  Claims are made before Telegram I/O so an overlapping
# scan cannot send the same entry before the persistent lock is written.
_pending_signal: Dict[str, str] = {}
_scan_lock = asyncio.Lock()
# Post-SL cooldown — after a loss, block re-entry on that TF for 2 candle periods.
# Prevents the bot spamming re-entries every 15s in volatile/choppy conditions.
# Structure: { "M15": <timestamp when cooldown expires> }
_sl_cooldown_until: Dict[str, float] = {}
# Cooldown = 2 candle periods per timeframe
_SL_COOLDOWN_CANDLES = 2
# Track which reminder milestones have been sent per trade.
# Structure: { "trade_id": {"entry", "update_2x", "update_6x"} }
_reminded_trade_ids: Dict[str, Set[str]] = {}

# Reminder milestones are calculated from the trade's own timeframe below.
# The scheduler runs every 10 minutes, so each milestone has a window wide
# enough to be caught without making a faster timeframe dictate reminders for
# slower trades.
_TF_PERIOD_SECONDS = {
    "M1":  60,
    "M3":  3 * 60,
    "M5":  5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1":  60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
    "W1": 7 * 24 * 60 * 60,
    "MN1": 30 * 24 * 60 * 60,
}
_DEFAULT_TF_PERIOD_SECONDS = 60 * 60


def _reminder_milestones(tf: str) -> list[tuple[str, int, int, bool]]:
    """Return reminder windows scaled to the trade's candle timeframe.

    The first reminder is a missed-entry nudge after roughly one candle. The
    later reminders are status updates after roughly 2.5 and 6 candle
    periods. Windows overlap neither each other nor the 10-minute scheduler
    cadence, so an H1 trade cannot receive the M15-style early nudge.
    """
    period = _TF_PERIOD_SECONDS.get(tf, _DEFAULT_TF_PERIOD_SECONDS)
    first_min = period
    first_max = int(period * 1.5)
    second_min = int(period * 2.5)
    second_max = int(period * 3.5)
    third_min = int(period * 6)
    third_max = int(period * 7)
    # The reminder job runs every 10 minutes, so bounded windows can expire
    # between runs (especially for M1/M3/M5).  max_age is retained in the
    # tuple shape for callers, but reminders are now due-based after min_age.
    return [
        ("entry", first_min, 0, True),
        ("update_2x", second_min, 0, False),
        ("update_6x", third_min, 0, False),
    ]

def get_scan_timeframes() -> list[str]:
    """Return the user's selected timeframe for automatic alert scanning.

    Reports can still request every timeframe in a mode, but background alerts
    must honor the timeframe selected in Settings instead of silently scanning
    M5 whenever Scalp mode is active.
    """
    return [get_timeframe()]

# Time-based cooldowns removed — alerts fire on every genuine direction change.
# A "new entry" is defined as: the timeframe's signal flipped away (e.g. SELL→WAIT)
# and then came back (WAIT→SELL), or is firing for the first time.
# This means no missed entries due to arbitrary timers.
TF_SIGNAL_COOLDOWNS: Dict[str, int] = {}

# Maximum age a signal lock is held before auto-expiring.
# Prevents a missed TP/SL detection from permanently blocking future signals.
SIGNAL_LOCK_MAX_AGE = 12 * 3600  # 12 hours

# Tracks the last "setup forming" pre-alert sent per TF — avoids repeat spam
# Structure: { "M15": "BUY", "H1": "SELL", ... }
_forming_alert_sent: Dict[str, str] = {}

# Tracks the last momentum-shift direction warned per TF — avoids re-sending
# the same warning every 15s while the opposing trade is still open.
# Cleared when the open trade closes so the next shift warns fresh.
# Structure: { "M15": "SELL", "H1": "BUY", ... }
_momentum_shift_warned: Dict[str, str] = {}

# Confluence alert — fires ONE grouped card when this many TFs agree.
# Below this threshold each TF fires its own individual card.
CONFLUENCE_MIN_TFS = 3

# Higher timeframes used to determine the master trend bias.
# Lower-TF signals that disagree with this bias are suppressed.
HTF_ANCHOR = ["H4"]   # legacy export; mode-aware scans use the engine's HTF map

# File that persists signal state across bot restarts
SIGNAL_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "signal_state.json")

# ── Market open/close transition tracking ─────────────────────────────────────
_prev_market_open: Optional[bool] = None
_open_notif_sent_at: float  = 0.0
_close_notif_sent_at: float = 0.0
NOTIF_COOLDOWN = 30 * 60
_signal_state_mode: str = ""
_signal_state_timeframe: str = ""


def _is_authorized(chat_id: int) -> bool:
    """All users are authorized — no access restrictions."""
    return True


def _load() -> Set[int]:
    try:
        with open(DATA_PATH, "r") as f:
            return set(json.load(f).get("subscribers", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _load_disabled() -> Set[int]:
    """Load chats that explicitly opted out of automatic alerts."""
    try:
        with open(DATA_PATH, "r") as f:
            return set(json.load(f).get("disabled", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save(subs: Set[int], disabled: Optional[Set[int]] = None) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    if disabled is None:
        disabled = _load_disabled()
    with open(DATA_PATH, "w") as f:
        json.dump(
            {
                "subscribers": sorted(subs),
                "disabled": sorted(disabled),
            },
            f,
        )


def register_user(chat_id: int) -> None:
    """Register a user for alert broadcasts."""
    users = _load()
    disabled = _load_disabled()
    disabled.discard(chat_id)
    if chat_id not in users:
        users.add(chat_id)
        _save(users, disabled)
        logger.info(f"User registered for alerts: {chat_id}")
    elif chat_id in disabled:
        _save(users, disabled)


def unregister_user(chat_id: int) -> None:
    """Disable alert broadcasts for a chat without deleting any trade history."""
    users = _load()
    disabled = _load_disabled()
    disabled.add(chat_id)
    if chat_id in users:
        users.remove(chat_id)
    _save(users, disabled)
    logger.info(f"User unsubscribed from alerts: {chat_id}")


def is_registered(chat_id: int) -> bool:
    """Return whether a chat is currently subscribed to automatic alerts."""
    return chat_id in _load()


def is_alerts_disabled(chat_id: int) -> bool:
    """Return whether a chat explicitly opted out of automatic alerts."""
    return chat_id in _load_disabled()


def user_count() -> int:
    return len(_load())


def _load_signal_state() -> None:
    """Load persisted signal state from disk — survives bot restarts."""
    global _active_signal, _tf_last_fired, _signal_state_mode, _signal_state_timeframe
    try:
        with open(SIGNAL_STATE_PATH) as f:
            s = json.load(f)
            _active_signal = s.get("active_signal", {})
            _tf_last_fired = s.get("last_fired", {})
            _signal_state_mode = s.get("mode", "")
            _signal_state_timeframe = s.get("timeframe", "")
            logger.info(f"Signal state loaded: {_active_signal}")
    except FileNotFoundError:
        pass  # Normal on first run
    except json.JSONDecodeError as e:
        logger.warning(f"Signal state file corrupted ({e}) — starting fresh.")


def _save_signal_state() -> None:
    """Write signal state to disk so restarts don't re-fire stale signals."""
    os.makedirs(os.path.dirname(SIGNAL_STATE_PATH), exist_ok=True)
    with open(SIGNAL_STATE_PATH, "w") as f:
        json.dump({
            "mode": get_mode(),
            "timeframe": get_timeframe(),
            "active_signal": _active_signal,
            "last_fired": _tf_last_fired,
        }, f)


def _sync_mode_state() -> None:
    """Clear signal locks when the strategy mode or timeframe changes.

    Open trades remain in the tracker and continue to receive TP/SL checks;
    only entry locks and forming alerts are mode/timeframe-specific.
    """
    global _signal_state_mode, _signal_state_timeframe
    active_mode = get_mode()
    active_timeframe = get_timeframe()
    settings_changed = (
        _signal_state_mode
        and (
            _signal_state_mode != active_mode
            or _signal_state_timeframe != active_timeframe
        )
    )
    if settings_changed:
        logger.info(
            f"Analysis settings changed "
            f"{_signal_state_mode}/{_signal_state_timeframe} → "
            f"{active_mode}/{active_timeframe}; clearing stale entry locks."
        )
        _active_signal.clear()
        _tf_last_fired.clear()
        _forming_alert_sent.clear()
        _momentum_shift_warned.clear()
        _sl_cooldown_until.clear()
    if (
        _signal_state_mode != active_mode
        or _signal_state_timeframe != active_timeframe
    ):
        _signal_state_mode = active_mode
        _signal_state_timeframe = active_timeframe
        _save_signal_state()


def _should_send(tf: str, action: str) -> bool:
    """
    Fire alert whenever the direction is new for this TF.
    Same direction = same trade still open, no re-alert until it resets.
    Resets happen when: signal flips to WAIT/opposite, or trade closes.
    Also auto-clears locks older than SIGNAL_LOCK_MAX_AGE to prevent
    a missed TP/SL detection from permanently suppressing future signals.
    Post-SL cooldown: after a loss, block re-entry for 2 candle periods.
    """
    # ── Post-SL cooldown check ─────────────────────────────────────────────────
    cooldown_until = _sl_cooldown_until.get(tf, 0.0)
    if time.time() < cooldown_until:
        remaining = int((cooldown_until - time.time()) // 60)
        logger.info(f"[{tf}] Post-SL cooldown active — {remaining}m remaining. Skipping {action}.")
        return False

    if tf in _pending_signal:
        logger.info(
            f"[{tf}] Suppressed — {action} is already being delivered "
            f"({_pending_signal[tf]} claim in progress)."
        )
        return False

    # A persisted signal lock is not enough to prove that the old plan closed.
    # A same-direction signal is suppressed while its plan is active.  An
    # opposite-direction signal is intentionally allowed through to _process:
    # that path sends the user a momentum-shift warning instead of silently
    # dropping the change in bias.  It must not open a second trade.
    active_trade = next(
        (
            t for t in trade_tracker.get_all_trades()
            if trade_tracker.is_active_trade(t) and t.get("timeframe") == tf
        ),
        None,
    )
    if active_trade:
        if active_trade.get("direction") == action:
            logger.info(
                f"[{tf}] Suppressed — active {action} trade "
                f"{active_trade.get('id')} still owns this timeframe."
            )
            return False
        logger.info(
            f"[{tf}] Opposite signal {action} detected while "
            f"{active_trade.get('direction')} trade is active — warning path."
        )
        return True

    prev = _active_signal.get(tf)
    if prev == action:
        last_fired = _tf_last_fired.get(tf, 0.0)
        age = time.time() - last_fired
        if age > SIGNAL_LOCK_MAX_AGE:
            logger.warning(
                f"[{tf}] Signal lock expired after {age / 3600:.1f}h — "
                f"auto-clearing stale {prev} lock so next entry fires freely."
            )
            _active_signal.pop(tf, None)
            _tf_last_fired.pop(tf, None)
            return True
        logger.info(f"[{tf}] Suppressed — {action} already active on this TF (same trade).")
        return False
    return True


def clear_signal_lock(tf: str, after_sl: bool = False) -> None:
    """Call after a trade closes so the next signal on this timeframe fires freely.

    after_sl=True: apply a 2-candle cooldown before allowing re-entry.
    This prevents the bot spamming re-entries every 15s after a quick SL hit
    in volatile/choppy conditions.
    """
    _active_signal.pop(tf, None)
    _pending_signal.pop(tf, None)
    _tf_last_fired.pop(tf, None)
    # Clear momentum-shift warning state so the next shift warns fresh
    _momentum_shift_warned.pop(tf, None)
    if after_sl:
        period = _TF_PERIOD_SECONDS.get(tf, _DEFAULT_TF_PERIOD_SECONDS)
        cooldown = _SL_COOLDOWN_CANDLES * period
        _sl_cooldown_until[tf] = time.time() + cooldown
        logger.info(
            f"[{tf}] Signal lock cleared after SL — "
            f"post-SL cooldown {cooldown // 60:.0f}m before next entry."
        )
    else:
        _sl_cooldown_until.pop(tf, None)
        logger.info(f"[{tf}] Signal lock cleared — ready for next entry.")
    _save_signal_state()


def get_signal_lock_info(tf: str) -> str:
    """Return a human-readable lock status for a timeframe, or '' if no lock."""
    direction = _active_signal.get(tf)
    if not direction:
        return ""
    last_fired = _tf_last_fired.get(tf, 0.0)
    elapsed    = int((time.time() - last_fired) // 60)
    return f"Alert sent {elapsed}m ago ({direction}) — waiting for signal to reset"


async def _send_setup_forming_alert(
    bot, subs: Set[int], a, tf: str, forming_dir: str
) -> None:
    """
    Lightweight pre-signal notice — fires when 3 indicators agree but the full
    signal hasn't triggered yet. Gives the trader a heads-up to watch the chart
    and prepare a limit order, without committing to an entry.
    Only fires once per direction per TF; resets when direction changes.
    """
    global _forming_alert_sent
    if _forming_alert_sent.get(tf) == forming_dir:
        return  # already warned this direction on this TF

    _forming_alert_sent[tf] = forming_dir
    arrow = "📈" if forming_dir == "BUY" else "📉"
    kz_tag = f"  🔔 {a.kill_zone}" if getattr(a, "is_kill_zone", False) else ""
    votes  = a.buy_votes if forming_dir == "BUY" else a.sell_votes
    text = (
        f"<pre>⚠️  SETUP FORMING  —  XAU/USD  {tf}\n"
        f"{'─' * 34}\n"
        f"{arrow}  Direction : {forming_dir}\n"
        f"   Price    : {a.price:,.2f}\n"
        f"   Votes    : {votes}/5 indicators agree\n"
        f"   ADX      : {a.adx:.1f}   Conf: {a.confidence}%\n"
        f"   HTF      : {a.htf_bias}{kz_tag}\n"
        f"{'─' * 34}\n"
        f"  Not a signal yet. Watch for entry.\n"
        f"  Early limit @ OTE zone if available.\n"
        f"</pre>"
    )
    await _broadcast_text(bot, subs, text)
    logger.info(f"[{tf}] Setup-forming pre-alert sent — {forming_dir} ({votes}/5 votes)")


async def _send_momentum_shift_warning(
    bot, subs: Set[int], trade: dict, tf: str, new_direction: str
) -> None:
    """Notify once when a new direction conflicts with an active trade."""
    global _momentum_shift_warned
    if _momentum_shift_warned.get(tf) == new_direction:
        return

    old_direction = trade.get("direction", "UNKNOWN")
    entry = float(trade.get("entry", 0.0))
    sl = float(trade.get("sl", 0.0))
    text = (
        f"<pre>⚠️  MOMENTUM SHIFT  —  XAU/USD  {tf}\n"
        f"{'─' * 36}\n"
        f"  Open trade  : {old_direction} from {entry:,.2f}\n"
        f"  Stop loss   : {sl:,.2f}\n"
        f"  New bias    : {new_direction} forming\n"
        f"{'─' * 36}\n"
        f"  No new entry fired — the current\n"
        f"  {old_direction} trade still owns {tf}.\n"
        f"  Higher-timeframe filters may block\n"
        f"  the reversal until this trade closes.\n"
        f"{'─' * 36}</pre>"
    )
    await _broadcast_text(bot, subs, text)
    _momentum_shift_warned[tf] = new_direction
    logger.info(
        f"[{tf}] Momentum shift warning sent — open {old_direction} "
        f"vs new {new_direction} bias."
    )


async def _broadcast_text(
    bot, subs: Set[int], text: str, *, return_result: bool = False
):
    """Send text to subscribers.

    The historical callers only need the set of dead chats. Alert delivery
    also needs to know whether at least one send succeeded so a transient
    Telegram error does not consume the signal lock.
    """
    dead: Set[int] = set()
    delivered = 0
    for chat_id in list(subs):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            delivered += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
            else:
                logger.warning(f"Text send failed for {chat_id}: {e}")
    return (dead, delivered > 0) if return_result else dead


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
        caption = (f"🔴 STOP LOSS HIT  |  XAU/USD  |  {timeframe}\n"
                   f"{direction}  Entry: {entry:,.2f}  Exit: {exit_price:,.2f}\n"
                   f"Loss: {abs(entry - exit_price):,.2f} pts")
    elif event == "TP3":
        result  = "WIN_TP2"
        tp3_val = trade.get("tp3", exit_price)
        caption = (f"🎯 TP3 HIT — MAXIMUM TARGET  |  XAU/USD  |  {timeframe}\n"
                   f"{direction}  Entry: {entry:,.2f}  TP3: {tp3_val:,.2f}\n"
                   f"Full run profit: +{abs(entry - exit_price):,.2f} pts")
    elif event == "TP2":
        result  = "WIN_TP2"
        tp3_val = trade.get("tp3")
        watching = f"  |  Watching for TP3 @ {tp3_val:,.2f}" if tp3_val else ""
        caption = (f"✅ TP2 HIT  |  XAU/USD  |  {timeframe}\n"
                   f"{direction}  Entry: {entry:,.2f}  TP2: {tp2:,.2f}\n"
                   f"Profit: +{abs(entry - exit_price):,.2f} pts{watching}")
    elif event == "TP1_SL":
        result  = "LOSS"
        caption = (f"🟠 BREAK-EVEN EXIT (after TP1)  |  XAU/USD  |  {timeframe}\n"
                   f"{direction}  Entry: {entry:,.2f}  Exit: {exit_price:,.2f}\n"
                   f"TP1 {tp1:,.2f} was hit — protection moved to entry; "
                   f"the original SL was {sl:,.2f}")
    else:
        result  = "WIN_TP1"
        tp3_val = trade.get("tp3")
        watching = f"  |  Watching for TP2 → TP3 @ {tp3_val:,.2f}" if tp3_val else "  |  Watching for TP2"
        caption = (f"✅ TP1 HIT  |  XAU/USD  |  {timeframe}\n"
                   f"{direction}  Entry: {entry:,.2f}  TP1: {tp1:,.2f}\n"
                   f"Partial profit: +{abs(entry - exit_price):,.2f} pts{watching}")

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


async def _fire_signal(bot, subs: Set[int], a, tf: str) -> bool:
    """Broadcast a single-TF entry signal: entry card + live chart."""
    # 1. Send the entry card (same format as /recommend Part 2)
    text = early_entry_card(a)
    dead, delivered = await _broadcast_text(
        bot, subs, text, return_result=True
    )
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
        f"to {len(subs)} sub(s), text_delivered={delivered}"
    )
    return delivered


async def _fire_confluence(
    bot, subs: Set[int], signal_list: list, direction: str
) -> bool:
    """
    Broadcast ONE grouped alert when 3+ timeframes align on the same direction.
    signal_list: list of (tf, MarketAnalysis) tuples — all same direction.
    """
    from src.utils.formatting import confluence_alert_card

    # Reference TF priority is mode-independent: use the highest available
    # timeframe because it carries the broadest structure.
    from src.utils.formatting import timeframe_rank
    tf_priority = sorted(
        {tf for tf, _ in signal_list},
        key=timeframe_rank,
        reverse=True,
    )
    tfs_present = {tf for tf, _ in signal_list}
    ref_tf = next((tf for tf in tf_priority if tf in tfs_present), signal_list[0][0])
    ref_a  = next(a for tf, a in signal_list if tf == ref_tf)

    text = confluence_alert_card(signal_list, direction, ref_tf)
    dead, delivered = await _broadcast_text(
        bot, subs, text, return_result=True
    )
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
        f"ref={ref_tf}  to {len(subs)} sub(s), text_delivered={delivered}"
    )
    return delivered


_STARTUP_STAMP = os.path.join(os.path.dirname(__file__), "..", "data", "startup_last.txt")
_STARTUP_COOLDOWN = 2 * 60 * 60  # 2 hours — suppresses spam on frequent restarts


def _startup_cooldown_ok() -> bool:
    """Return True only if at least 2 hours have passed since the last send."""
    try:
        with open(_STARTUP_STAMP) as f:
            last = float(f.read().strip())
        if time.time() - last < _STARTUP_COOLDOWN:
            return False
    except (FileNotFoundError, ValueError):
        pass
    return True


def _mark_startup_sent() -> None:
    try:
        with open(_STARTUP_STAMP, "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.warning(f"Could not write startup stamp: {e}")


async def send_startup_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a reconnect card to all subscribers when the bot starts up.
    Suppressed if sent within the last 2 hours to prevent restart spam."""
    from src.utils.formatting import restart_summary_card

    if not _startup_cooldown_ok():
        logger.info("Startup summary suppressed — sent less than 2 hours ago.")
        return

    bot  = context.application.bot
    subs = _load()

    if not subs:
        logger.info("Startup summary: no subscribers.")
        return

    try:
        all_trades   = trade_tracker.get_all_trades()
        open_trades  = trade_tracker.get_active_trades()
        recent       = [t for t in all_trades if not trade_tracker.is_active_trade(t)][:5]
        stats        = trade_tracker.get_stats()
        text         = restart_summary_card(open_trades, recent, stats)
        dead         = await _broadcast_text(bot, subs, text)
        if dead:
            subs -= dead
            _save(subs)
        _mark_startup_sent()
        logger.info(f"Startup summary sent to {len(subs)} subscriber(s).")
    except Exception as e:
        logger.error(f"Startup summary failed: {e}")


async def _check_and_alert_once(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _prev_market_open, _open_notif_sent_at, _close_notif_sent_at
    _sync_mode_state()
    mode_cfg = get_mode_config()
    scan_timeframes = get_scan_timeframes()

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
    # Use each open trade's own timeframe candle (high/low) rather than a
    # single spot-price snapshot — a 30s poll can miss a brief wick through
    # TP/SL and either report the wrong exit price or miss the touch outright.
    #
    # We look at the last 3 candles (not just the newest) so a wick that fired
    # on a candle already scrolled out of the live spot price is still caught.
    # The worst-case downside is a slightly stale exit price shown in the card,
    # which is always safer than silently missing the hit entirely.
    #
    # tfs_closed_this_cycle: tracks TFs whose trade closed in this scan so the
    # signal-scanner below skips them — prevents same-cycle re-entry before the
    # post-SL cooldown has been saved to disk and the analysis re-fetched.
    tfs_closed_this_cycle: set = set()
    try:
        current_price = await get_gold_price()
        if current_price > 0:
            # Include tp2_hit trades that are still watching for TP3
            active_trades = [
                t for t in trade_tracker.get_active_trades()
                if t.get("timeframe")
            ]
            open_tfs = {t.get("timeframe") for t in active_trades}

            # For each TF, record the earliest trade open time — we will only
            # use candles from AFTER that point to avoid false SL/TP hits from
            # historical data that predates the trade.
            tf_opened_at: Dict[str, float] = {}
            for t in active_trades:
                tf = t.get("timeframe")
                oa = t.get("opened_at", 0.0)
                if tf and (tf not in tf_opened_at or oa < tf_opened_at[tf]):
                    tf_opened_at[tf] = oa

            tf_extremes: Dict[str, tuple] = {}
            if open_tfs:
                ohlcv_results = await asyncio.gather(
                    *[fetch_ohlcv(tf) for tf in open_tfs],
                    return_exceptions=True,
                )
                for tf, data in zip(open_tfs, ohlcv_results):
                    if isinstance(data, Exception) or data is None or not data.highs:
                        continue

                    opened_at = tf_opened_at.get(tf, 0.0)
                    ts_list   = getattr(data, "timestamps", [])

                    if ts_list and opened_at > 0:
                        # Prefer candles whose open time is on or after the trade
                        # open.  Include one candle before open_at as a buffer for
                        # the candle that was forming when the trade was entered.
                        indices = [
                            i for i, ts in enumerate(ts_list)
                            if ts >= opened_at
                        ]
                        if not indices:
                            # No candle has opened since this trade was placed.
                            # Using the pre-entry candle's extremes for SL
                            # detection causes false immediate SL hits: for a
                            # SELL the SL sits just above the candle that formed
                            # the signal high, so that same candle's high would
                            # instantly trigger SL on the very next scan.
                            # Fall back to current_price only — the next poll
                            # after a new candle forms gives proper post-entry
                            # extremes.
                            hi = current_price
                            lo = current_price
                        else:
                            hi = max(data.highs[i] for i in indices)
                            lo = min(data.lows[i]  for i in indices)
                    else:
                        # Without timestamps we cannot prove that a historical
                        # candle formed after entry.  Use spot only rather than
                        # reusing a pre-entry wick that can falsely hit SL/TP
                        # immediately after a trade opens.
                        hi = current_price
                        lo = current_price

                    tf_extremes[tf] = (hi, lo)

            events = trade_tracker.check_trades(current_price, tf_extremes=tf_extremes)
            for ev in events:
                closed_tf = ev["trade"].get("timeframe")
                if ev["event"] == "EXPIRED":
                    # Silently release the lock — no message sent for expired trades.
                    # Without this, the timeframe stays locked after expiry and the
                    # next genuine entry signal is permanently suppressed.
                    logger.info(f"[{closed_tf}] Trade expired — signal lock released.")
                    if closed_tf:
                        clear_signal_lock(closed_tf)
                        tfs_closed_this_cycle.add(closed_tf)
                    continue
                await _send_result_image(bot, subs, ev["trade"], ev["event"], ev["exit_price"])
                # TP1 is a partial milestone, not a closed trade. Keep the
                # timeframe locked while TP2/TP3 is still being tracked.
                # Only terminal events release the entry lock.
                if closed_tf and not trade_tracker.is_active_trade(ev["trade"]):
                    is_loss = ev["event"] in ("SL", "TP1_SL")
                    clear_signal_lock(closed_tf, after_sl=is_loss)
                    tfs_closed_this_cycle.add(closed_tf)
                elif closed_tf:
                    logger.info(
                        f"[{closed_tf}] {ev['event']} recorded — trade remains active; "
                        "entry lock retained."
                    )
    except Exception as e:
        logger.error(f"Trade check failed: {e}")

    # ── Scan timeframes for entry signals ─────────────────────────────────────
    # Each timeframe alerts independently. One card per timeframe per direction
    # change — lock releases only when the signal flips or the trade closes.
    analyses = await asyncio.gather(
        *[_safe_analyze(tf) for tf in scan_timeframes],
        return_exceptions=True,
    )

    # Pass 1 — log all results, collect newly-triggered signals
    new_signals: list = []   # (tf, MarketAnalysis) pairs that should fire this cycle
    state_changed = False

    for tf, a in zip(scan_timeframes, analyses):
        if a is None or isinstance(a, Exception):
            if isinstance(a, Exception):
                logger.error(f"[{tf}] Analysis raised: {a}")
            continue

        # Skip TFs whose trade just closed this cycle — the post-SL cooldown
        # is set, but a fresh analysis could pass _should_send before the lock
        # is fully persisted.  Skipping here guarantees no same-cycle re-entry.
        if tf in tfs_closed_this_cycle:
            logger.info(f"[{tf}] Skipping signal scan — trade closed this cycle, cooldown pending.")
            continue

        logger.info(
            f"[{tf}] scan: action={a.action} grade={a.setup_quality} "
            f"conf={a.confidence}% win={a.win_probability}% adx={a.adx:.1f}"
        )

        if a.action not in ("BUY", "SELL"):
            # Do NOT clear the lock on WAIT — the market briefly returning WAIT
            # between two candles of the same direction is normal oscillation,
            # not a genuine signal reset. Clearing here caused the bot to
            # re-fire the same SELL/BUY alert every time analysis dipped to WAIT
            # for one cycle. Lock is released only by: trade close/SL/expire, or
            # a confirmed flip to the opposite direction.
            logger.info(f"[{tf}] No signal ({a.action}) — signal lock preserved.")

            # Pre-signal: 3 indicators agree but full signal not confirmed yet.
            # Warn the trader to watch the chart and prepare — early enough to
            # place a limit order in the OTE zone before the move starts.
            # Only fires when there is no active lock on this TF.
            if not _active_signal.get(tf):
                forming_dir = None
                if a.buy_votes >= 3 and a.buy_votes > a.sell_votes and a.adx >= 15:
                    forming_dir = "BUY"
                elif a.sell_votes >= 3 and a.sell_votes > a.buy_votes and a.adx >= 15:
                    forming_dir = "SELL"
                if forming_dir:
                    active_trade = next(
                        (
                            t for t in trade_tracker.get_active_trades()
                            if t.get("timeframe") == tf
                            and t.get("direction") != forming_dir
                        ),
                        None,
                    )
                    if active_trade:
                        # An opposing pre-signal is still actionable
                        # information even though it cannot open a second
                        # trade. Warn before the full signal is confirmed.
                        await _send_momentum_shift_warning(
                            bot, subs, active_trade, tf, forming_dir
                        )
                    elif not _active_signal.get(tf):
                        await _send_setup_forming_alert(bot, subs, a, tf, forming_dir)
                else:
                    # Direction collapsed — reset forming state so next build-up fires fresh
                    _forming_alert_sent.pop(tf, None)
            continue

        # Full signal fired — reset the forming-alert state for this TF
        _forming_alert_sent.pop(tf, None)

        if _should_send(tf, a.action):
            # Warn before the higher-timeframe gate below. A valid reversal
            # must not disappear silently just because it cannot become a new
            # entry while the current trade still owns this timeframe.
            active_trade = next(
                (
                    t for t in trade_tracker.get_active_trades()
                    if t.get("timeframe") == tf
                    and t.get("direction") != a.action
                ),
                None,
            )
            if active_trade:
                await _send_momentum_shift_warning(
                    bot, subs, active_trade, tf, a.action
                )

            # Block simulated data — never alert on fake prices
            if getattr(a, "is_simulated", False):
                logger.warning(
                    f"[{tf}] Alert BLOCKED — running on simulated data (YF fetch failed). "
                    f"Will retry on next scan cycle."
                )
                continue

            # ── Cross-TF coherence block ──────────────────────────────────────
            # Prevent HTF signals from contradicting a confirmed lower-TF lock.
            # If M15 or M30 already has a confirmed SELL lock (active trade),
            # a H4/H1 BUY alert would send the opposite signal — confusing and
            # dangerous. The lower-TF lock means the trend is confirmed down on
            # the immediate price action; the HTF lagging indicators haven't
            # caught up yet. Block the contradicting signal until the lower lock
            # is released by a trade close or direction flip.
            TF_ORDER = scan_timeframes
            tf_rank  = TF_ORDER.index(tf) if tf in TF_ORDER else -1
            lower_conflict = False
            if tf_rank > 0:
                lower_tfs = TF_ORDER[:tf_rank]
                for ltf in lower_tfs:
                    ltf_lock = _active_signal.get(ltf)
                    if ltf_lock and ltf_lock != a.action:
                        lower_conflict = True
                        logger.info(
                            f"[{tf}] Blocked — contradicts active {ltf_lock} lock "
                            f"on {ltf}. HTF lagging; waiting for lower TF to clear."
                        )
                        break
            if lower_conflict:
                continue

            # ── HTF alignment gate — block STRONG counter-trend only ──────────
            # "Slightly" counter-trend is a valid pullback opportunity: the engine
            # already applies a -12 confidence penalty, and the alert card shows
            # the counter-trend warning. Hard-blocking it means missing valid entries.
            # Exception: ChoCH confirmed on this TF overrides the HTF block —
            # a structural break is the PRO signal that the trend HAS reversed,
            # even before the HTF bias catches up.
            choch = getattr(a, "choch", "") or ""
            choch_aligned = (
                (a.action == "BUY"  and choch == "BULLISH_CHOCH") or
                (a.action == "SELL" and choch == "BEARISH_CHOCH")
            )
            htf_strongly_against = (
                (a.action == "BUY"  and a.htf_bias == "Bearish") or
                (a.action == "SELL" and a.htf_bias == "Bullish")
            )
            # Lower-TF same-direction lock overrides the HTF counter-trend block.
            # If M15 is already confirmed SELL and M30 wants to fire SELL, that IS
            # multi-TF confluence — not a counter-trend trade. The HTF block was
            # designed to stop isolated signals, not to suppress aligned TF stacks.
            lower_same_dir = any(
                _active_signal.get(ltf) == a.action
                for ltf in TF_ORDER[:tf_rank]
            ) if tf_rank > 0 else False

            if htf_strongly_against and not choch_aligned and not lower_same_dir:
                logger.info(
                    f"[{tf}] Filtered — strong counter-trend "
                    f"({a.action} vs HTF={a.htf_bias}). Too risky."
                )
                continue

            # Quality gate — A/A+ OR ChoCH structural bypass.
            # Standard path: grade A/A+ + win ≥ 62%.
            # ChoCH bypass: confirmed market structure break + grade B or better
            #   + win ≥ 58% — ChoCH IS the pro signal for early reversal entry.
            #   We allow it even when HTF hasn't caught up yet (ChoCH overrides
            #   the HTF block above, and lowers the win threshold here).
            is_quality    = (
                a.win_probability >= mode_cfg.alert_min_win_probability
                and a.setup_quality in mode_cfg.alert_min_grades
            )
            choch_quality = (
                choch_aligned
                and a.win_probability >= max(50, mode_cfg.alert_min_win_probability - 4)
                and a.setup_quality in (*mode_cfg.alert_min_grades, "B")
            )

            if not is_quality and not choch_quality:
                logger.info(
                    f"[{tf}] Filtered — quality too low "
                    f"(win={a.win_probability}% grade={a.setup_quality} "
                    f"adx={a.adx:.1f} choch={choch or 'none'}). "
                    f"Need win≥{mode_cfg.alert_min_win_probability}%+"
                    f"{'/'.join(mode_cfg.alert_min_grades)}, or ChoCH bypass."
                )
                continue

            # Claim synchronously before the first await in pass 2.  A chart
            # upload can take longer than the scheduler interval; without this
            # claim, the next overlapping scan can pass _should_send too.
            _pending_signal[tf] = a.action
            new_signals.append((tf, a))

    if state_changed:
        _save_signal_state()

    if not new_signals:
        return

    # ── Pass 2 — fire each timeframe independently, no HTF gate ──────────────
    # Each TF sends its own alert when it has a new signal. No direction filter.
    # Anti-spam is handled by _should_send() above: same direction on same TF
    # is suppressed until the trade closes or reverses.
    async def _process(sig_list: list, direction: str) -> None:
        """Fire alert (confluence or individual) and record state + open trades."""
        # ── Conflicting-trade guard ────────────────────────────────────────────
        # If a TF already has an open trade in the OPPOSITE direction, send a
        # heads-up warning but DO NOT fire the new entry signal.
        # Entering a SELL while a BUY is still open puts the user in two
        # opposing trades simultaneously. The new signal is suppressed until
        # the existing trade resolves (TP or SL hit).
        all_open = {
            t["timeframe"]: t for t in trade_tracker.get_active_trades()
            if t.get("timeframe")
        }
        clean_signals = []
        for tf, a in sig_list:
            existing = all_open.get(tf)
            if existing:
                # An active trade owns its timeframe regardless of direction.
                # Do not send a second entry while the first plan is still
                # being tracked; a direction change gets a single warning.
                _pending_signal.pop(tf, None)
                if existing.get("direction") == direction:
                    logger.info(
                        f"[{tf}] Entry suppressed — active {direction} trade "
                        f"{existing.get('id')} already owns this timeframe."
                    )
                    continue
                # Only send the warning once per TF per shift direction —
                # the scanner runs every 15s so without this it spams the
                # same message continuously while the trade is open.
                if _momentum_shift_warned.get(tf) != direction:
                    await _send_momentum_shift_warning(
                        bot, subs, existing, tf, direction
                    )
                else:
                    logger.info(
                        f"[{tf}] Momentum shift already warned ({direction}) — suppressing repeat."
                    )
                # Entry is held back — do not add to clean_signals
            else:
                clean_signals.append((tf, a))

        sig_list = clean_signals
        if not sig_list:
            return

        claimed_tfs = {tf for tf, _ in sig_list}
        if len(sig_list) >= mode_cfg.confluence_min_tfs:
            delivered = await _fire_confluence(bot, subs, sig_list, direction)
        else:
            delivered_signals = []
            for tf, a in sig_list:
                if await _fire_signal(bot, subs, a, tf):
                    delivered_signals.append((tf, a))
            sig_list = delivered_signals

        # Do not lock or create a tracked trade when Telegram did not accept
        # the alert. The next scan must retry a transient delivery failure.
        delivered_tfs = {tf for tf, _ in sig_list}
        if not sig_list or (
            len(sig_list) >= mode_cfg.confluence_min_tfs and not delivered
        ):
            for tf in claimed_tfs:
                _pending_signal.pop(tf, None)
            logger.warning(
                f"[{direction}] Alert not delivered; leaving signal state unlocked "
                "for retry."
            )
            return

        now_ts = time.time()
        delivered_tfs = {tf for tf, _ in sig_list}
        # Individual delivery can partially succeed.  Release claims for
        # failed timeframes so a later scan can retry them, while preserving
        # the lock for the timeframes whose alert was accepted.
        for tf in claimed_tfs - delivered_tfs:
            _pending_signal.pop(tf, None)
        for tf, a in sig_list:
            _active_signal[tf] = direction
            _tf_last_fired[tf] = now_ts
            try:
                invalidate_cache(tf)
                opened = trade_tracker.open_trade(
                    direction=a.action, entry=a.entry, sl=a.stop_loss,
                    tp1=a.tp1, tp2=a.tp2, timeframe=tf,
                    confidence=a.confidence, rr_ratio=a.rr_ratio,
                    tp3=getattr(a, "tp3", None),
                    atr=getattr(a, "atr", 0.0),
                    mode=get_mode(),
                )
                if not opened:
                    logger.warning(
                        f"[{tf}] Alert delivered but trade plan was not opened; "
                        "keeping a local signal lock to prevent re-entry spam."
                    )
                    _active_signal[tf] = direction
                    _tf_last_fired[tf] = now_ts
                _pending_signal.pop(tf, None)
            except Exception as e:
                _pending_signal.pop(tf, None)
                logger.error(f"Trade open failed ({tf}): {e}")
        _save_signal_state()

    # Group by direction so confluence alerts bundle same-direction TFs together
    buys  = [(tf, a) for tf, a in new_signals if a.action == "BUY"]
    sells = [(tf, a) for tf, a in new_signals if a.action == "SELL"]

    if buys:
        await _process(buys, "BUY")
    if sells:
        await _process(sells, "SELL")


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run one alert scan at a time.

    The scheduler interval is intentionally short, but analysis and chart
    delivery are network-bound.  Serializing scans prevents duplicate entry
    claims while preserving the fast polling cadence when a scan is quick.
    """
    if _scan_lock.locked():
        logger.info("Alert scan skipped — previous scan is still running.")
        return
    async with _scan_lock:
        await _check_and_alert_once(context)


def _determine_htf_bias(analyses: list, timeframes: list) -> str:
    """
    Determine the master trend direction from H4 alone.

    Rules:
      - H4 BUY  → BUY  (only send BUY signals on lower TFs)
      - H4 SELL → SELL (only send SELL signals on lower TFs)
      - H4 WAIT → WAIT (no clear trend, suppress all)
      - H4 WAIT → WAIT (no directional read)

    Returns 'BUY', 'SELL', or 'WAIT'.
    """
    tf_map = {
        tf: a for tf, a in zip(timeframes, analyses)
        if a is not None and not isinstance(a, Exception)
    }
    h4_action = tf_map["H4"].action if "H4" in tf_map else "WAIT"
    return h4_action if h4_action in ("BUY", "SELL") else "WAIT"


async def _safe_analyze(tf: str):
    try:
        return await analyze(tf)
    except Exception as e:
        logger.error(f"Alert scan — analysis failed for {tf}: {e}")
        return None


async def send_trade_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs every 10 minutes. Sends timeframe-scaled milestone reminders:
      • ~1 candle — missed-alert nudge (only if price still near entry)
      • ~2.5 candles — trade-still-running update with live P&L
      • ~6 candles — long-running trade update with live P&L
    Each milestone fires at most once per trade.
    """
    global _reminded_trade_ids

    all_trades = trade_tracker.get_all_trades()
    open_trades = trade_tracker.get_active_trades()
    if not open_trades:
        return

    subs = _load()
    if not subs:
        return

    try:
        current_price = await get_gold_price()
    except Exception as e:
        logger.warning(f"Reminder — could not fetch price: {e}")
        return

    now = time.time()
    for trade in open_trades:
        trade_id  = trade.get("id", "")
        opened_at = trade.get("opened_at", 0)
        age_secs  = now - opened_at
        entry     = trade.get("entry", 0)
        direction = trade.get("direction", "")
        tf        = trade.get("timeframe", "")
        sl        = trade.get("sl", 0)
        tp1       = trade.get("tp1", 0)
        tp2       = trade.get("tp2")
        tp3       = trade.get("tp3")
        conf      = trade.get("confidence", 0)

        if trade_id not in _reminded_trade_ids:
            _reminded_trade_ids[trade_id] = set()
        sent_milestones = _reminded_trade_ids[trade_id]
        reminder_sent_this_run = False

        sl_dist = abs(entry - sl)
        rr1 = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist > 0 and tp1 else 0
        rr3 = round(abs(tp3 - entry) / sl_dist, 1) if sl_dist > 0 and tp3 else 0

        dir_emoji = "🟢" if direction == "BUY" else "🔴"

        for label, min_age, max_age, require_near in _reminder_milestones(tf):
            if label in sent_milestones:
                continue
            if age_secs < min_age:
                continue
            if reminder_sent_this_run:
                break

            entry_reachable = (
                entry > 0
                and abs(current_price - entry) / entry <= 0.0015
            )

            # The first milestone is specifically a missed-entry nudge.  If
            # price has already moved away, do not send a confusing "missed
            # alert" telling the trader not to chase.  Mark the milestone as
            # handled so the same stale entry is not reconsidered every ten
            # minutes.  Later milestones are status updates and still send.
            if require_near and not entry_reachable:
                sent_milestones.add(label)
                logger.info(
                    f"[REMINDER:{label}] {direction} {tf} skipped — "
                    f"price moved away from entry ({entry:,.2f} → "
                    f"{current_price:,.2f})"
                )
                continue

            age_min = int(age_secs // 60)
            age_str = f"{age_min}m" if age_min < 60 else f"{age_min // 60}h {age_min % 60}m"

            # Live P&L for update milestones
            if direction == "BUY":
                pnl = current_price - entry
            else:
                pnl = entry - current_price
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_note = f"P&L  : {pnl_sign}{pnl:,.1f} pts ({'in profit ✅' if pnl >= 0 else 'in loss ⚠️'})\n"

            # Detect post-TP1 retrace — affects what we show in the message
            tp1_was_hit     = trade.get("tp1_hit", False)
            tp1_retraced    = False
            tp1_retrace_warning = ""
            if tp1_was_hit:
                if direction == "BUY" and current_price <= entry:
                    tp1_retraced = True
                    tp1_retrace_warning = (
                        f"\n⚠️ <b>TP1 was hit but price has since fallen below entry.</b>\n"
                        f"   Break-even SL is now active at {entry:,.2f}.\n"
                        f"   Consider closing manually to protect the TP1 gain.\n"
                    )
                elif direction == "SELL" and current_price >= entry:
                    tp1_retraced = True
                    tp1_retrace_warning = (
                        f"\n⚠️ <b>TP1 was hit but price has since risen above entry.</b>\n"
                        f"   Break-even SL is now active at {entry:,.2f}.\n"
                        f"   Consider closing manually to protect the TP1 gain.\n"
                    )

            # Only show TP2/TP3 targets when the trade is still moving in profit.
            # After a retrace below entry, showing optimistic targets is misleading.
            if tp1_retraced:
                tp2_line = ""
                tp3_line = ""
            else:
                tp2_line = f"TP2  : <b>{tp2:,.2f}</b>\n" if tp2 else ""
                tp3_line = f"TP3  : <b>{tp3:,.2f}</b>  (1:{rr3})\n" if tp3 else ""

            if label == "entry":
                header = f"⚠️ <b>MISSED ALERT — ENTRY STILL OPEN</b>"
                subtext = f"Fired {age_str} ago — entry still reachable\n"
                pnl_note = ""   # no P&L at the first-candle reminder
            elif label == "update_2x":
                header = f"📊 <b>TRADE UPDATE — {direction} STILL RUNNING</b>"
                subtext = f"Opened {age_str} ago\n"
            else:  # 6h
                header = f"🕐 <b>LONG-RUNNING TRADE — {direction} XAU/USD {tf}</b>"
                subtext = f"Open for {age_str} — still watching for TP/SL\n"

            text = (
                f"{header}\n"
                f"{'─' * 30}\n"
                f"{dir_emoji} <b>{direction}  XAU/USD  {tf}</b>  |  Conf {conf}%\n"
                f"{subtext}"
                f"{'─' * 30}\n"
                f"Entry : <b>{entry:,.2f}</b>  (now {current_price:,.2f})\n"
                f"{pnl_note}"
                f"SL   : <b>{sl:,.2f}</b>\n"
                f"TP1  : <b>{tp1:,.2f}</b>  {'✅ hit' if tp1_was_hit else f'(1:{rr1})'}\n"
                f"{tp2_line}"
                f"{tp3_line}"
                f"{tp1_retrace_warning}"
                f"{'─' * 30}\n"
                f"Use /active to track live.  Use /signal for latest scan."
            )

            dead = await _broadcast_text(context.bot, subs, text)
            if dead:
                subs -= dead
                _save(subs)

            sent_milestones.add(label)
            reminder_sent_this_run = True
            logger.info(
                f"[REMINDER:{label}] {direction} {tf} @ {entry:.2f} — "
                f"age={age_str}, price={current_price:.2f}, sent to {len(subs)} sub(s)"
            )


# Load persisted signal state on module import
_load_signal_state()
