import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters, Application

from src.analysis import analyze
from src.utils.formatting import analysis_card, signal_card, trend_card, levels_card, outlook_card
from src.utils.keyboards import main_menu_keyboard, timeframe_keyboard

logger = logging.getLogger(__name__)

MENU_MAP = {
    "analyze": "analyze",
    "signal": "signal",
    "trend": "trend",
    "levels": "levels",
    "outlook": "outlook",
    "settings": "settings",
}


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip().lower()

    if text == "analyze":
        msg = await update.message.reply_text("Analyzing XAU/USD...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(analysis_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg analyze error: {e}")
            await msg.edit_text("Analysis failed. Please try again.")

    elif text == "signal":
        msg = await update.message.reply_text("Scanning for trade setup...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(signal_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg signal error: {e}")
            await msg.edit_text("Signal scan failed. Please try again.")

    elif text == "trend":
        msg = await update.message.reply_text("Reading trend...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(trend_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg trend error: {e}")
            await msg.edit_text("Trend read failed. Please try again.")

    elif text == "levels":
        msg = await update.message.reply_text("Calculating levels...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(levels_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg levels error: {e}")
            await msg.edit_text("Level calculation failed. Please try again.")

    elif text == "outlook":
        msg = await update.message.reply_text("Generating outlook...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(outlook_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg outlook error: {e}")
            await msg.edit_text("Outlook generation failed. Please try again.")

    elif text == "settings":
        from src.utils.keyboards import settings_keyboard
        tf = _get_tf(context)
        text_out = (
            "<b>Settings</b>\n\n"
            f"Current Timeframe: <b>{tf}</b>\n\n"
            "Select a timeframe to update your default analysis window."
        )
        await update.message.reply_text(
            text_out, parse_mode="HTML", reply_markup=settings_keyboard(tf)
        )

    else:
        await update.message.reply_text(
            "Use the menu or a command.\nType /help for all commands.",
            reply_markup=main_menu_keyboard(),
        )


def register_message_handlers(app: Application) -> None:
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
