import json
import logging
import os
from typing import Set, Dict, Any

from telegram.ext import ContextTypes

from src.analysis import analyze
from src.utils.formatting import alert_card

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "subscribers.json")

_last_signal: Dict[str, str] = {}


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


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    subs = _load()
    if not subs:
        return

    from telegram.ext import Application
    app: Application = context.application

    tf = "H1"
    try:
        a = await analyze(tf)
    except Exception as e:
        logger.error(f"Alert scan failed: {e}")
        return

    if a.action not in ("BUY", "SELL"):
        return

    signal_key = f"{a.action}:{a.timeframe}:{round(a.entry, 1)}"
    if _last_signal.get(tf) == signal_key:
        return

    _last_signal[tf] = signal_key
    text = alert_card(a)

    dead = set()
    for chat_id in list(subs):
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead.add(chat_id)
            else:
                logger.warning(f"Alert send failed for {chat_id}: {e}")

    if dead:
        subs -= dead
        _save(subs)
        logger.info(f"Removed {len(dead)} unreachable subscriber(s)")
