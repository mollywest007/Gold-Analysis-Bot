"""
Mode Manager — persists and serves the active Analysis Mode.

The selected mode is stored in data/mode.json and survives restarts.
All callers use get_mode_config() to retrieve the active ModeConfig.
"""
import json
import logging
import os
from typing import Optional

from src.analysis.modes import MODES, ModeConfig, DEFAULT_MODE

logger = logging.getLogger(__name__)

_MODE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mode.json")


def _load() -> str:
    try:
        with open(_MODE_PATH) as f:
            data = json.load(f)
            mode = data.get("mode", DEFAULT_MODE)
            if mode in MODES:
                return mode
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return DEFAULT_MODE


def _save(mode: str) -> None:
    os.makedirs(os.path.dirname(_MODE_PATH), exist_ok=True)
    try:
        with open(_MODE_PATH, "w") as f:
            json.dump({"mode": mode}, f)
    except Exception as e:
        logger.warning(f"Could not save mode: {e}")


def get_mode() -> str:
    """Return the active mode name ('scalp', 'intraday', 'swing', 'position')."""
    return _load()


def get_mode_config() -> ModeConfig:
    """Return the active ModeConfig object."""
    return MODES.get(_load(), MODES[DEFAULT_MODE])


def set_mode(mode: str) -> ModeConfig:
    """
    Switch to a new mode. Returns the new ModeConfig.
    Raises ValueError if mode name is unknown.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'. Valid: {list(MODES)}")
    _save(mode)
    cfg = MODES[mode]
    logger.info(f"Analysis mode switched to: {cfg.label} — TFs: {cfg.scan_timeframes}")
    return cfg


def list_modes() -> list:
    """Return all ModeConfig objects in display order."""
    return [MODES[k] for k in ("scalp", "intraday", "swing", "position")]
