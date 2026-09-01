"""Per-account analysis preferences.

Telegram users share one bot process, but their selected mode and timeframe
must not be stored in the global mode.json state.
"""
import json
import logging
import os
from typing import Dict

from src.analysis.modes import DEFAULT_MODE, MODES, ModeConfig
from src.mode_manager import get_mode as get_legacy_mode
from src.mode_manager import get_timeframe as get_legacy_timeframe

logger = logging.getLogger(__name__)

PREFERENCES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "user_preferences.json"
)


def _load() -> Dict[str, Dict[str, str]]:
    try:
        with open(PREFERENCES_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: Dict[str, Dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(PREFERENCES_PATH), exist_ok=True)
    with open(PREFERENCES_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _key(chat_id: int) -> str:
    return str(int(chat_id))


def _default_preferences() -> Dict[str, str]:
    """Use the old global setting only as a one-time migration fallback."""
    legacy_mode = get_legacy_mode()
    if legacy_mode not in MODES:
        legacy_mode = DEFAULT_MODE
    cfg = MODES[legacy_mode]
    legacy_tf = get_legacy_timeframe()
    timeframe = (
        legacy_tf if legacy_tf in cfg.scan_timeframes else cfg.preferred_timeframe
    )
    return {"mode": legacy_mode, "timeframe": timeframe}


def get_preferences(chat_id: int) -> Dict[str, str]:
    """Return and, on first access, persist this account's own preferences."""
    data = _load()
    key = _key(chat_id)
    saved = data.get(key)
    if not isinstance(saved, dict):
        saved = _default_preferences()
        data[key] = saved
        _save(data)

    mode = saved.get("mode")
    if mode not in MODES:
        mode = DEFAULT_MODE
    cfg = MODES[mode]
    timeframe = saved.get("timeframe")
    if timeframe not in cfg.scan_timeframes:
        timeframe = cfg.preferred_timeframe
    normalized = {"mode": mode, "timeframe": timeframe}
    if saved != normalized:
        data[key] = normalized
        _save(data)
    return normalized


def get_mode(chat_id: int) -> str:
    return get_preferences(chat_id)["mode"]


def get_mode_config(chat_id: int) -> ModeConfig:
    return MODES[get_mode(chat_id)]


def get_timeframe(chat_id: int) -> str:
    return get_preferences(chat_id)["timeframe"]


def set_mode(chat_id: int, mode: str) -> ModeConfig:
    if mode not in MODES:
        raise ValueError(f"Unknown analysis mode '{mode}'.")
    data = _load()
    current = get_preferences(chat_id)
    cfg = MODES[mode]
    timeframe = (
        current["timeframe"]
        if current["timeframe"] in cfg.scan_timeframes
        else cfg.preferred_timeframe
    )
    data[_key(chat_id)] = {"mode": mode, "timeframe": timeframe}
    _save(data)
    logger.info(
        "Account %s switched analysis mode to %s with timeframe %s.",
        chat_id,
        mode,
        timeframe,
    )
    return cfg


def set_timeframe(chat_id: int, timeframe: str) -> str:
    cfg = get_mode_config(chat_id)
    if timeframe not in cfg.scan_timeframes:
        raise ValueError(
            f"Timeframe '{timeframe}' is not available in {cfg.label} Mode."
        )
    data = _load()
    data[_key(chat_id)] = {"mode": cfg.name, "timeframe": timeframe}
    _save(data)
    logger.info("Account %s selected timeframe %s.", chat_id, timeframe)
    return timeframe