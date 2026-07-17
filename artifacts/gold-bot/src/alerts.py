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
# Track which trade IDs have already had a reminder sent (avoid double-nudge)
_reminded_trade_ids: Set[str] = set()

SCAN_TIMEFRAMES = ["M15", "M30", "H1", "H4"]  # M5 removed — too noisy for gold entries

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

# Confluence alert — fires ONE grouped card when this many TFs agree.
# Below this threshold each TF fires its own individual card.
CONFLUENCE_MIN_TFS = 3

# Higher timeframes used to determine the master trend bias.
# Lower-TF signals that disagree with this bias are suppressed.
HTF_ANCHOR = ["H4"]   # H4 is the sole trend anchor

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
    except FileNotFoundError:
        pass  # Normal on first run
    except json.JSONDecodeError as e:
        logger.warning(f"Signal state file corrupted ({e}) — starting fresh.")


def _save_signal_state() -> None:
    """Write signal state to disk so restarts don't re-fire stale signals."""
    os.makedirs(os.path.dirname(SIGNAL_STATE_PATH), exist_ok=True)
    with open(SIGNAL_STATE_PATH, "w") as f:
        json.dump({"active_signal": _active_signal, "last_fired": _tf_last_fired}, f)


def _should_send(tf: str, action: str) -> bool:
    """
    Fire alert whenever the direction is new for this TF.
    Same direction = same trade still open, no re-alert until it resets.
    Resets happen when: signal flips to WAIT/opposite, or trade closes.
    Also auto-clears locks older than SIGNAL_LOCK_MAX_AGE to prevent
    a missed TP/SL detection from permanently suppressing future signals.
    """
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


def clear_signal_lock(tf: str) -> None:
    """Call after a trade closes so the next signal on this timeframe fires freely."""
    _active_signal.pop(tf, None)
    _tf_last_fired.pop(tf, None)
    _save_signal_state()
    logger.info(f"[{tf}] Signal lock cleared — ready for next entry.")


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
        caption = (f"🔴 SL HIT (after TP1)  |  XAU/USD  |  {timeframe}\n"
                   f"{direction}  Entry: {entry:,.2f}  SL: {sl:,.2f}\n"
                   f"TP1 {tp1:,.2f} was hit — SL then triggered at {exit_price:,.2f}")
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
    tf_priority = ["H4", "H1", "M30", "M15", "M5"]
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


_STARTUP_STAMP = "/tmp/gold_bot_startup_last.txt"
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
        open_trades  = [t for t in all_trades if t.get("status") == "open"]
        recent       = [t for t in all_trades if t.get("status") != "open"][:5]
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
    # Use each open trade's own timeframe candle (high/low) rather than a
    # single spot-price snapshot — a 30s poll can miss a brief wick through
    # TP/SL and either report the wrong exit price or miss the touch outright.
    try:
        current_price = await get_gold_price()
        if current_price > 0:
            open_tfs = {
                t.get("timeframe") for t in trade_tracker.get_all_trades()
                if t.get("status") in ("open", "tp1_hit") and t.get("timeframe")
            }
            tf_extremes: Dict[str, tuple] = {}
            if open_tfs:
                ohlcv_results = await asyncio.gather(
                    *[fetch_ohlcv(tf) for tf in open_tfs],
                    return_exceptions=True,
                )
                for tf, data in zip(open_tfs, ohlcv_results):
                    if isinstance(data, Exception) or data is None or not data.highs:
                        continue
                    tf_extremes[tf] = (data.highs[-1], data.lows[-1])

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
                    continue
                await _send_result_image(bot, subs, ev["trade"], ev["event"], ev["exit_price"])
                # Trade closed — unlock this timeframe so the next entry signal fires fresh
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
                    await _send_setup_forming_alert(bot, subs, a, tf, forming_dir)
                else:
                    # Direction collapsed — reset forming state so next build-up fires fresh
                    _forming_alert_sent.pop(tf, None)
            continue

        # Full signal fired — reset the forming-alert state for this TF
        _forming_alert_sent.pop(tf, None)

        if _should_send(tf, a.action):
            # Rotate lock immediately when direction flips — even if the new
            # signal doesn't pass the quality gate. Without this, a stale BUY
            # lock blocks the NEXT valid BUY after a failed SELL attempt.
            old_dir = _active_signal.get(tf)
            if old_dir and old_dir != a.action:
                _active_signal.pop(tf, None)
                state_changed = True
                logger.info(f"[{tf}] Lock rotated {old_dir} → {a.action}")

            # Block simulated data — never alert on fake prices
            if getattr(a, "is_simulated", False):
                logger.warning(
                    f"[{tf}] Alert BLOCKED — running on simulated data (YF fetch failed). "
                    f"Will retry on next scan cycle."
                )
                continue

            # HTF alignment gate — block STRONG counter-trend only.
            # "Slightly" counter-trend is a valid pullback opportunity: the engine
            # already applies a -12 confidence penalty, and the alert card shows
            # the counter-trend warning. Hard-blocking it means missing valid entries.
            htf_strongly_against = (
                (a.action == "BUY"  and a.htf_bias == "Bearish") or
                (a.action == "SELL" and a.htf_bias == "Bullish")
            )
            if htf_strongly_against:
                logger.info(
                    f"[{tf}] Filtered — strong counter-trend "
                    f"({a.action} vs HTF={a.htf_bias}). Too risky."
                )
                continue

            # Quality gate — only A+ and A setups, win probability ≥ 62%.
            # ADX is NOT re-checked here: the grade assignment in engine.py already
            # requires ADX ≥ 17 (kill zone) or ≥ 20 (normal) for grade A.
            # Double-gating on ADX blocks valid kill-zone signals at ADX 17–19.
            if a.win_probability < 62 or a.setup_quality not in ("A+", "A"):
                logger.info(
                    f"[{tf}] Filtered — quality too low "
                    f"(win={a.win_probability}% grade={a.setup_quality} adx={a.adx:.1f}). "
                    f"Need win≥62% + grade A/A+."
                )
                continue
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
                    tp3=getattr(a, "tp3", None),
                )
            except Exception as e:
                logger.error(f"Trade open failed ({tf}): {e}")
        _save_signal_state()

    # Group by direction so confluence alerts bundle same-direction TFs together
    buys  = [(tf, a) for tf, a in new_signals if a.action == "BUY"]
    sells = [(tf, a) for tf, a in new_signals if a.action == "SELL"]

    if buys:
        await _process(buys, "BUY")
    if sells:
        await _process(sells, "SELL")


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
    Runs every 10 minutes. For every open trade that is 8–25 minutes old and
    where price is still within 0.5% of the entry, broadcast a reminder nudge
    so users who missed the original alert can still act on it.
    A trade only ever gets ONE reminder — tracked in _reminded_trade_ids.
    """
    global _reminded_trade_ids

    all_trades = trade_tracker.get_all_trades()
    open_trades = [
        t for t in all_trades
        if t.get("status") in ("open", "tp1_hit")
    ]
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

        # Only remind once, and only in the 8–25 minute window after the alert
        if trade_id in _reminded_trade_ids:
            continue
        if not (8 * 60 <= age_secs <= 25 * 60):
            continue

        # Only remind if price is still within 0.5% of entry (entry still reachable)
        if entry <= 0 or abs(current_price - entry) / entry > 0.005:
            _reminded_trade_ids.add(trade_id)  # price moved away, skip forever
            continue

        direction = trade.get("direction", "")
        tf        = trade.get("timeframe", "")
        sl        = trade.get("sl", 0)
        tp1       = trade.get("tp1", 0)
        tp2       = trade.get("tp2")
        tp3       = trade.get("tp3")
        conf      = trade.get("confidence", 0)

        sl_dist = abs(entry - sl)
        rr1 = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist > 0 and tp1 else 0
        rr3 = round(abs(tp3 - entry) / sl_dist, 1) if sl_dist > 0 and tp3 else 0

        dir_emoji  = "🟢" if direction == "BUY" else "🔴"
        tp2_line   = f"\nTP2 : <b>{tp2:,.2f}</b>" if tp2 else ""
        tp3_line   = f"\nTP3 : <b>{tp3:,.2f}</b>  (1:{rr3})" if tp3 else ""
        age_min    = int(age_secs // 60)

        text = (
            f"⚠️ <b>MISSED ALERT REMINDER</b>\n"
            f"{'─' * 30}\n"
            f"{dir_emoji} <b>{direction}  XAU/USD  {tf}</b>\n"
            f"Fired {age_min} min ago — entry still reachable\n"
            f"{'─' * 30}\n"
            f"Entry : <b>{entry:,.2f}</b>  (now {current_price:,.2f})\n"
            f"SL    : <b>{sl:,.2f}</b>\n"
            f"TP1   : <b>{tp1:,.2f}</b>  (1:{rr1}){tp2_line}{tp3_line}\n"
            f"{'─' * 30}\n"
            f"Confidence: {conf}%\n"
            f"Use /active to track this trade live."
        )

        dead = await _broadcast_text(context.bot, subs, text)
        if dead:
            subs -= dead
            _save(subs)

        _reminded_trade_ids.add(trade_id)
        logger.info(
            f"[REMINDER] {direction} {tf} @ {entry:.2f} — "
            f"age={age_min}m, price={current_price:.2f}, sent to {len(subs)} sub(s)"
        )


# Load persisted signal state on module import
_load_signal_state()
