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

COMBINED_MODE = "scalp_interval"
SCALP_STREAM = "scalp"
INTERVAL_STREAM = "interval"

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
    if mode == COMBINED_MODE:
        scalp_cfg = MODES["scalp"]
        interval_cfg = MODES["intraday"]
        legacy_tf = saved.get("timeframe")
        scalp_timeframe = saved.get("scalp_timeframe")
        interval_timeframe = saved.get("interval_timeframe")
        if scalp_timeframe not in scalp_cfg.scan_timeframes:
            scalp_timeframe = (
                legacy_tf
                if legacy_tf in scalp_cfg.scan_timeframes
                else scalp_cfg.preferred_timeframe
            )
        if interval_timeframe not in interval_cfg.scan_timeframes:
            interval_timeframe = (
                legacy_tf
                if legacy_tf in interval_cfg.scan_timeframes
                else interval_cfg.preferred_timeframe
            )
        normalized = {
            "mode": mode,
            # Keep the legacy primary timeframe field pointed at Scalp so
            # older callers still have a useful default.
            "timeframe": scalp_timeframe,
            "scalp_timeframe": scalp_timeframe,
            "interval_timeframe": interval_timeframe,
        }
    else:
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


def get_combined_timeframes(chat_id: int) -> Dict[str, str]:
    """Return the independently selected Scalp and Interval timeframes."""
    preferences = get_preferences(chat_id)
    if preferences["mode"] != COMBINED_MODE:
        raise ValueError("Combined timeframes are only available in Scalp / Interval Mode.")
    return {
        SCALP_STREAM: preferences["scalp_timeframe"],
        INTERVAL_STREAM: preferences["interval_timeframe"],
    }


def get_monitoring_streams(chat_id: int) -> list[tuple[str, str, str]]:
    """Return (display label, timeframe, analysis mode) alert streams."""
    mode = get_mode(chat_id)
    if mode == COMBINED_MODE:
        timeframes = get_combined_timeframes(chat_id)
        return [
            ("SCALP", timeframes[SCALP_STREAM], "scalp"),
            ("INTRA-HOUR", timeframes[INTERVAL_STREAM], "intraday"),
        ]
    return [
        (
            MODES[mode].label.upper(),
            get_timeframe(chat_id),
            mode,
        )
    ]


def set_mode(chat_id: int, mode: str) -> ModeConfig:
    if mode not in MODES:
        raise ValueError(f"Unknown analysis mode '{mode}'.")
    data = _load()
    current = get_preferences(chat_id)
    cfg = MODES[mode]
    if mode == COMBINED_MODE:
        scalp_cfg = MODES["scalp"]
        interval_cfg = MODES["intraday"]
        if current.get("mode") == COMBINED_MODE:
            scalp_timeframe = current.get("scalp_timeframe")
            interval_timeframe = current.get("interval_timeframe")
        else:
            # A combined session starts with its documented independent
            # defaults instead of copying one single-mode timeframe into both
            # streams.
            scalp_timeframe = scalp_cfg.preferred_timeframe
            interval_timeframe = interval_cfg.preferred_timeframe
        if scalp_timeframe not in scalp_cfg.scan_timeframes:
            scalp_timeframe = scalp_cfg.preferred_timeframe
        if interval_timeframe not in interval_cfg.scan_timeframes:
            interval_timeframe = interval_cfg.preferred_timeframe
        data[_key(chat_id)] = {
            "mode": mode,
            "timeframe": scalp_timeframe,
            "scalp_timeframe": scalp_timeframe,
            "interval_timeframe": interval_timeframe,
        }
        timeframe = f"scalp={scalp_timeframe}, interval={interval_timeframe}"
    else:
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


def set_combined_timeframe(chat_id: int, stream: str, timeframe: str) -> str:
    """Persist one side of the combined monitor without changing the other."""
    if stream == SCALP_STREAM:
        cfg = MODES["scalp"]
        field = "scalp_timeframe"
    elif stream == INTERVAL_STREAM:
        cfg = MODES["intraday"]
        field = "interval_timeframe"
    else:
        raise ValueError(f"Unknown combined stream '{stream}'.")
    if timeframe not in cfg.scan_timeframes:
        raise ValueError(
            f"Timeframe '{timeframe}' is not available for {stream.title()} alerts."
        )

    data = _load()
    current = get_preferences(chat_id)
    if current["mode"] != COMBINED_MODE:
        raise ValueError("Select Scalp / Interval Mode before setting stream timeframes.")
    updated = {
        "mode": COMBINED_MODE,
        "timeframe": current["scalp_timeframe"],
        "scalp_timeframe": current["scalp_timeframe"],
        "interval_timeframe": current["interval_timeframe"],
    }
    updated[field] = timeframe
    updated["timeframe"] = updated["scalp_timeframe"]
    data[_key(chat_id)] = updated
    _save(data)
    logger.info(
        "Account %s selected %s alert timeframe %s.",
        chat_id,
        stream,
        timeframe,
    )
    return timeframe