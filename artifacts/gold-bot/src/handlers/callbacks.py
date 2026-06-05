import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, Application

from src.analysis import analyze
from src.utils.formatting import (
    analysis_card, signal_card, trend_card, levels_card,
    outlook_card, recommend_card
)
from src.utils.keyboards import settings_keyboard

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("set_tf:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        text = (
            "<b>Settings</b>\n\n"
            f"Timeframe updated: <b>{tf}</b>\n\n"
            "Select a timeframe to update your default analysis window."
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=settings_keyboard(tf))
        return

    if data.startswith("recommend:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        await query.edit_message_text("Scanning indicators...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(recommend_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback recommend error: {e}")
            await query.edit_message_text("Recommendation failed. Please try again.")
        return

    if data.startswith("analyze:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        await query.edit_message_text("Analyzing XAU/USD...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(analysis_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback analyze error: {e}")
            await query.edit_message_text("Analysis failed. Please try again.")
        return

    if data.startswith("signal:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        await query.edit_message_text("Scanning for trade setup...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(signal_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback signal error: {e}")
            await query.edit_message_text("Signal scan failed. Please try again.")
        return

    if data.startswith("trend:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        await query.edit_message_text("Reading trend...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(trend_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback trend error: {e}")
            await query.edit_message_text("Trend read failed. Please try again.")
        return

    if data.startswith("levels:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        await query.edit_message_text("Calculating levels...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(levels_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback levels error: {e}")
            await query.edit_message_text("Level calculation failed. Please try again.")
        return

    if data.startswith("outlook:"):
        tf = data.split(":")[1]
        context.user_data["timeframe"] = tf
        await query.edit_message_text("Generating outlook...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(outlook_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback outlook error: {e}")
            await query.edit_message_text("Outlook generation failed. Please try again.")
        return

    if data in ("settings:tf_header",):
        return

    if data in ("settings:back", "back:main"):
        await query.edit_message_text("Use the menu below to navigate.")
        return


def register_callback_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_callback))
