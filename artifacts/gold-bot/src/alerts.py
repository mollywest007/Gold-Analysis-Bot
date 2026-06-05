import json
import logging
import os
import time
from typing import Set, Dict, Tuple

from telegram.ext import ContextTypes

from src.analysis import analyze
from src.utils.formatting import alert_card

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "subscribers.json")

_last_sent: Dict[str, Tuple[str, float]] = {}

RESEND_AFTER_SECONDS = 55 * 60


def _load() -> Set[int]:
    try:
        with open(DATA_PATH, "r") as f:
            data = json.load(f)
            return set(data.get("subscribers", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save(subscribers: Set[int]) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump({"subscribers": list(subscribers)}, f)


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
    logger.info(f"Alert suppressed — same signal already sent {int(age/60)}m ago. Next in ~{remaining}m.")
    return False


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    subs = _load()
    if not subs:
        logger.info("Alert scan: no subscribers.")
        return

    tf = "H1"
    try:
        a = await analyze(tf)
    except Exception as e:
        logger.error(f"Alert scan — analysis failed: {e}")
        return

    logger.info(
        f"Alert scan: action={a.action} confidence={a.confidence}% "
        f"rr={a.rr_ratio} buy_votes={a.buy_votes} sell_votes={a.sell_votes}"
    )

    if a.action not in ("BUY", "SELL"):
        logger.info(f"Alert scan: no signal to send (action={a.action}, reason={a.wait_reason})")
        return

    signal_key = f"{a.action}:{tf}:{round(a.entry, 0)}"

    if not _should_send(tf, signal_key):
        return

    text = alert_card(a)
    sent = 0
    dead: Set[int] = set()

    for chat_id in list(subs):
        try:
            await context.application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
                logger.warning(f"Removing unreachable subscriber {chat_id}")
            else:
                logger.warning(f"Alert send failed for {chat_id}: {e}")

    if sent > 0:
        _last_sent[tf] = (signal_key, time.time())
        logger.info(f"Alert sent to {sent} subscriber(s): {a.action} @ {a.entry}")

    if dead:
        subs -= dead
        _save(subs)
