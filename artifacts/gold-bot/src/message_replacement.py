"""Replaceable Telegram message groups.

Manual bot panels are grouped by feature (analysis, signal, news, etc.).  A
new request for the same feature removes the previous group so a chat does not
accumulate stale copies of the same panel.  The state is persisted because
Telegram message IDs remain usable after a bot restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "replaceable_messages.json"
)
_STATE_LOCK = asyncio.Lock()


def _load() -> dict[str, dict[str, list[int]]]:
    try:
        with open(STATE_PATH) as file:
            payload = json.load(file)
        chats = payload.get("chats", {}) if isinstance(payload, dict) else {}
        if not isinstance(chats, dict):
            return {}
        result: dict[str, dict[str, list[int]]] = {}
        for chat_id, slots in chats.items():
            if not isinstance(slots, dict):
                continue
            result[str(chat_id)] = {}
            for slot, message_ids in slots.items():
                if not isinstance(message_ids, list):
                    continue
                result[str(chat_id)][str(slot)] = [
                    int(message_id)
                    for message_id in message_ids
                    if str(message_id).lstrip("-").isdigit()
                ]
        return result
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _save(chats: dict[str, dict[str, list[int]]]) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    temporary_path = f"{STATE_PATH}.tmp"
    with open(temporary_path, "w") as file:
        json.dump({"chats": chats}, file, indent=2)
    os.replace(temporary_path, STATE_PATH)


def _message_id(message: Any) -> int | None:
    value = getattr(message, "message_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def remember_message(chat_id: int, slot: str, message: Any) -> None:
    """Remember one sent Telegram message as part of a replaceable panel."""
    message_id = _message_id(message)
    if message_id is None:
        return
    async with _STATE_LOCK:
        chats = _load()
        chat = chats.setdefault(str(int(chat_id)), {})
        ids = chat.setdefault(slot, [])
        if message_id not in ids:
            ids.append(message_id)
        _save(chats)


async def clear_previous(
    bot: Any,
    chat_id: int,
    slot: str,
    *,
    keep_message_id: int | None = None,
) -> None:
    """Delete the previous panel for a chat/feature.

    ``keep_message_id`` is used by inline refresh buttons: Telegram delivers
    the current card as the callback message, so that message must be edited
    rather than deleted.
    """
    async with _STATE_LOCK:
        chats = _load()
        chat = chats.get(str(int(chat_id)), {})
        previous = list(chat.get(slot, []))
        retained = (
            [keep_message_id]
            if keep_message_id is not None and keep_message_id in previous
            else []
        )
        if retained:
            chat[slot] = retained
        else:
            chat.pop(slot, None)
        if chat:
            chats[str(int(chat_id))] = chat
        else:
            chats.pop(str(int(chat_id)), None)
        _save(chats)

    for message_id in previous:
        if keep_message_id is not None and message_id == keep_message_id:
            continue
        try:
            await bot.delete_message(
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
        except Exception as error:
            text = str(error).lower()
            if "not found" not in text and "message to delete" not in text:
                logger.warning(
                    "Could not delete previous %s panel for %s/%s: %s",
                    slot,
                    chat_id,
                    message_id,
                    error,
                )
