from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        ["Recommend", "Analyze"],
        ["Signal", "Trend"],
        ["Levels", "Outlook"],
        ["Active", "News"],
        ["Settings"],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


def settings_keyboard(current_tf: str) -> InlineKeyboardMarkup:
    tf_options = ["M5", "M15", "M30", "H1", "H4", "D1"]
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

    rows = [
        [InlineKeyboardButton("-- Timeframe --", callback_data="settings:tf_header")],
        *tf_buttons,
        [InlineKeyboardButton("Back", callback_data="settings:back")],
    ]
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back:main")]])
