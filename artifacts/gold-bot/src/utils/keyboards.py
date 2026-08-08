from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from src.analysis.modes import MODES


def refresh_keyboard(command: str, tf: str = "all") -> InlineKeyboardMarkup:
    """Single Refresh button — tapping re-runs the same card in place."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{command}:{tf}")]
    ])


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        ["Recommend", "Analyze"],
        ["Signal", "Trend"],
        ["Levels", "Outlook"],
        ["Active", "News"],
        ["History", "Mode"],
        ["🔔 Alerts", "Settings"],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


def settings_keyboard(current_tf: str, current_mode: str = "intraday") -> InlineKeyboardMarkup:
    tf_options = MODES.get(current_mode, MODES["intraday"]).scan_timeframes
    tf_buttons = []
    row = []
    for tf in tf_options:
        label = f"[{tf}]" if tf == current_tf else tf
        row.append(InlineKeyboardButton(label, callback_data=f"set_tf:{tf}"))
        if len(row) == 3:
            tf_buttons.append(row)
            row = []
    if row:
        tf_buttons.append(row)

    mode_buttons = []
    mode_row = []
    for mode_cfg in MODES.values():
        mode = mode_cfg.name
        label = f"{mode_cfg.emoji} {mode_cfg.label}"
        mode_row.append(InlineKeyboardButton(
            f"[{label}]" if mode == current_mode else label,
            callback_data=f"set_mode:{mode}",
        ))
        if len(mode_row) == 2:
            mode_buttons.append(mode_row)
            mode_row = []
    if mode_row:
        mode_buttons.append(mode_row)

    rows = [
        [InlineKeyboardButton("-- Analysis Mode --", callback_data="settings:mode_header")],
        *mode_buttons,
        [InlineKeyboardButton("-- Timeframe --", callback_data="settings:tf_header")],
        *tf_buttons,
        [InlineKeyboardButton("Back", callback_data="settings:back")],
    ]
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back:main")]])
