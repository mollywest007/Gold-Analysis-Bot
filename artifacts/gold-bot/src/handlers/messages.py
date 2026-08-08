import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters, Application

from src.handlers.commands import (
    cmd_alerts,
    cmd_analyze,
    cmd_active,
    cmd_history,
    cmd_levels,
    cmd_news,
    cmd_outlook,
    cmd_recommend,
    cmd_mode,
    cmd_settings,
    cmd_signal,
    cmd_trend,
)
from src.utils.keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route dashboard buttons through the same handlers as slash commands.

    Telegram reply-keyboard buttons arrive as ordinary text messages.  Keeping
    a second implementation here caused the dashboard and /commands to drift:
    some buttons used different analysis paths and different error handling.
    One routing table makes both entry points behave identically.
    """
    text = (update.message.text or "").strip().lower()

    handlers = {
        "alerts": cmd_alerts,
        "🔔 alerts": cmd_alerts,
        "recommend": cmd_recommend,
        "analyze": cmd_analyze,
        "signal": cmd_signal,
        "trend": cmd_trend,
        "levels": cmd_levels,
        "outlook": cmd_outlook,
        "active": cmd_active,
        "news": cmd_news,
        "settings": cmd_settings,
        "mode": cmd_mode,
        "history": cmd_history,
    }
    handler = handlers.get(text)
    if handler is not None:
        await handler(update, context)
        return

    await update.message.reply_text(
        "Use the menu or a command.\nType /help for all commands.",
        reply_markup=main_menu_keyboard(),
    )


def register_message_handlers(app: Application) -> None:
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
