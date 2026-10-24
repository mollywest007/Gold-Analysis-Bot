"""Block interactive bot features while XAU/USD is closed."""
from telegram.ext import ApplicationHandlerStop

from src.market_hours import (
    market_closed_alert,
    market_closed_message,
    market_status,
)
from src.utils.keyboards import main_menu_keyboard


async def stop_if_market_closed(update) -> None:
    """Answer every authorized Telegram interaction with the next open time."""
    status = market_status()
    if status["is_open"]:
        return

    query = getattr(update, "callback_query", None)
    if query is not None:
        await query.answer(market_closed_alert(status), show_alert=True)
        raise ApplicationHandlerStop

    message = getattr(update, "effective_message", None)
    if message is not None:
        await message.reply_text(
            market_closed_message(status),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        raise ApplicationHandlerStop
