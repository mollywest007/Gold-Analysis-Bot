import json
import logging
import os
import time
from typing import Set, Dict, Tuple

from telegram.ext import ContextTypes

from src.analysis import analyze
from src.analysis.market_data import get_gold_price, invalidate_cache
from src.utils.formatting import alert_card
from src import trade_tracker
from src.image_gen import generate_result_image

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "subscribers.json")

_last_sent: Dict[str, Tuple[str, float]] = {}
RESEND_AFTER_SECONDS = 55 * 60


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


def is_subscribed(chat_id: int) -> bool:
    return chat_id in _load()


def subscribe(chat_id: int) -> bool:
    subs = _load()
    if chat_id in subs:
        return False
    subs.add(chat_id)
    _save(subs)
    return True


def unsubscribe(chat_id: int) -> bool:
    subs = _load()
    if chat_id not in subs:
        return False
    subs.discard(chat_id)
    _save(subs)
    return True


def subscriber_count() -> int:
    return len(_load())


def _should_send(tf: str, signal_key: str) -> bool:
    if tf not in _last_sent:
        return True
    last_key, last_ts = _last_sent[tf]
    age = time.time() - last_ts
    if last_key != signal_key:
        return True
    if age >= RESEND_AFTER_SECONDS:
        return True
    remaining = int((RESEND_AFTER_SECONDS - age) / 60)
    logger.info(f"Alert suppressed — same signal {int(age/60)}m ago. Next in ~{remaining}m.")
    return False


async def _send_result_image(
    bot,
    subs: Set[int],
    trade: dict,
    event: str,
    exit_price: float,
) -> None:
    """Generate and send WIN or LOSS result image to all subscribers."""
    direction  = trade["direction"]
    entry      = trade["entry"]
    sl         = trade["sl"]
    tp1        = trade["tp1"]
    tp2        = trade["tp2"]
    confidence = trade.get("confidence", 80)
    timeframe  = trade.get("timeframe", "H1")
    rr_ratio   = trade.get("rr_ratio", 2.0)

    if event == "SL":
        result = "LOSS"
        caption = (
            f"STOP LOSS HIT  |  XAU/USD\n"
            f"Direction: {direction}  |  Entry: {entry:,.2f}  |  Exit: {exit_price:,.2f}\n"
            f"Loss: {abs(entry - exit_price):,.2f} pts"
        )
    elif event == "TP2":
        result  = "WIN_TP2"
        caption = (
            f"ALL TARGETS HIT  |  XAU/USD\n"
            f"Direction: {direction}  |  Entry: {entry:,.2f}  |  TP2: {tp2:,.2f}\n"
            f"Full profit: +{abs(entry - exit_price):,.2f} pts"
        )
    else:  # TP1
        result  = "WIN_TP1"
        caption = (
            f"TP1 HIT  |  XAU/USD\n"
            f"Direction: {direction}  |  Entry: {entry:,.2f}  |  TP1: {tp1:,.2f}\n"
            f"Partial profit: +{abs(entry - exit_price):,.2f} pts"
        )

    try:
        img_bytes = generate_result_image(
            direction=direction,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            exit_price=exit_price,
            result=result,
            confidence=confidence,
            timeframe=timeframe,
            rr_ratio=rr_ratio,
        )
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        img_bytes = None

    dead: Set[int] = set()
    for chat_id in list(subs):
        try:
            if img_bytes:
                import io
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=io.BytesIO(img_bytes),
                    caption=caption,
                )
            else:
                await bot.send_message(chat_id=chat_id, text=f"<pre>{caption}</pre>", parse_mode="HTML")
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
            else:
                logger.warning(f"Image send failed for {chat_id}: {e}")

    if dead:
        subs -= dead
        _save(subs)

    logger.info(f"Result image sent: {result} @ {exit_price:.2f} to {len(subs) - len(dead)} subscriber(s)")


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.market_hours import market_status
    ms = market_status()
    if not ms["is_open"]:
        logger.info(f"Alert scan skipped — {ms['status_text']} ({ms['note']})")
        return

    subs = _load()
    if not subs:
        logger.info("Alert scan: no subscribers.")
        return

    bot = context.application.bot

    # ── 1. Check open trades for TP/SL hits ──────────────────────────────────
    try:
        current_price = await get_gold_price()
        if current_price > 0:
            events = trade_tracker.check_trades(current_price)
            for ev in events:
                await _send_result_image(bot, subs, ev["trade"], ev["event"], ev["exit_price"])
    except Exception as e:
        logger.error(f"Trade check failed: {e}")

    # ── 2. Scan for new entry signals ─────────────────────────────────────────
    tf = "H1"
    try:
        a = await analyze(tf)
    except Exception as e:
        logger.error(f"Alert scan — analysis failed: {e}")
        return

    logger.info(
        f"Alert scan: action={a.action} confidence={a.confidence}% "
        f"rr={a.rr_ratio} adx={a.adx:.1f} "
        f"buy={a.buy_votes} sell={a.sell_votes}"
    )

    if a.action not in ("BUY", "SELL"):
        logger.info(f"Alert scan: no signal (action={a.action})")
        return

    signal_key = f"{a.action}:{tf}:{round(a.entry, 0)}"
    if not _should_send(tf, signal_key):
        return

    # ── 3. Send signal card ───────────────────────────────────────────────────
    text = alert_card(a)
    sent = 0
    dead: Set[int] = set()

    for chat_id in list(subs):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
                logger.warning(f"Removing dead subscriber {chat_id}")
            else:
                logger.warning(f"Alert send failed for {chat_id}: {e}")

    if sent > 0:
        _last_sent[tf] = (signal_key, time.time())
        logger.info(f"Alert sent to {sent} subscriber(s): {a.action} @ {a.entry:.2f}")

        # ── 4. Register trade in tracker ─────────────────────────────────────
        try:
            invalidate_cache(tf)   # expire cache so next scan gets fresh data
            trade_tracker.open_trade(
                direction  = a.action,
                entry      = a.entry,
                sl         = a.stop_loss,
                tp1        = a.tp1,
                tp2        = a.tp2,
                timeframe  = tf,
                confidence = a.confidence,
                rr_ratio   = a.rr_ratio,
            )
        except Exception as e:
            logger.error(f"Trade open failed: {e}")

    if dead:
        subs -= dead
        _save(subs)
