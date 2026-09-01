"""
Mode Manager — persists and serves the legacy/default Analysis Mode.

The selected mode is stored in data/mode.json and survives restarts. Account
handlers use src.user_preferences instead; this module remains the fallback
for background jobs and backwards compatibility.
"""
import json
import logging
import os
from typing import Optional

from src.analysis.modes import MODES, ModeConfig, DEFAULT_MODE

logger = logging.getLogger(__name__)

_MODE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mode.json")


def _load_state() -> dict:
    try:
        with open(_MODE_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load() -> str:
    mode = _load_state().get("mode", DEFAULT_MODE)
    return mode if mode in MODES else DEFAULT_MODE


def _save(mode: str, timeframe: Optional[str] = None) -> None:
    os.makedirs(os.path.dirname(_MODE_PATH), exist_ok=True)
    try:
        data = {"mode": mode}
        if timeframe:
            data["timeframe"] = timeframe
        with open(_MODE_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Could not save mode: {e}")


def get_mode() -> str:
    """Return the active mode name ('scalp', 'intraday', 'swing', 'position')."""
    return _load()


def get_mode_config(mode: Optional[str] = None) -> ModeConfig:
    """Return a mode config, using global persisted mode when omitted."""
    mode_name = mode if mode in MODES else _load()
    return MODES.get(mode_name, MODES[DEFAULT_MODE])


def get_timeframe() -> str:
    """Return the persisted chart/alert timeframe for the active mode."""
    cfg = get_mode_config()
    timeframe = _load_state().get("timeframe")
    return timeframe if timeframe in cfg.scan_timeframes else cfg.preferred_timeframe


def set_timeframe(timeframe: str) -> str:
    """Persist the chart/alert timeframe for the active mode."""
    cfg = get_mode_config()
    if timeframe not in cfg.scan_timeframes:
        raise ValueError(
            f"Timeframe '{timeframe}' is not available in {cfg.label} Mode."
        )
    _save(cfg.name, timeframe)
    logger.info(f"{cfg.label} Mode timeframe set to {timeframe}.")
    return timeframe


def set_mode(mode: str) -> ModeConfig:
    """
    Switch to a new mode. Returns the new ModeConfig.
    Raises ValueError if mode name is unknown.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'. Valid: {list(MODES)}")
    cfg = MODES[mode]
    previous_timeframe = _load_state().get("timeframe")
    timeframe = (
        previous_timeframe
        if previous_timeframe in cfg.scan_timeframes
        else cfg.preferred_timeframe
    )
    _save(mode, timeframe)
    logger.info(f"Analysis mode switched to: {cfg.label} — TFs: {cfg.scan_timeframes}")
    return cfg


def list_modes() -> list:
    """Return all ModeConfig objects in display order."""
    # Dict insertion order is the display order.  New modes are therefore
    # discoverable by the UI without changing this manager.
    return list(MODES.values())
