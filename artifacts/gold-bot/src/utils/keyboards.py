from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        ["Recommend", "Analyze"],
        ["Signal", "Trend"],
        ["Levels", "Outlook"],
        ["Settings"],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


def timeframe_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("M5", callback_data=f"{prefix}:M5"),
            InlineKeyboardButton("M15", callback_data=f"{prefix}:M15"),
            InlineKeyboardButton("M30", callback_data=f"{prefix}:M30"),
        ],
        [
            InlineKeyboardButton("H1", callback_data=f"{prefix}:H1"),
            InlineKeyboardButton("H4", callback_data=f"{prefix}:H4"),
            InlineKeyboardButton("D1", callback_data=f"{prefix}:D1"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


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
        [InlineKeyboardButton("Timeframe", callback_data="settings:tf_header")],
        *tf_buttons,
        [InlineKeyboardButton("Back", callback_data="settings:back")],
    ]
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back:main")]])
