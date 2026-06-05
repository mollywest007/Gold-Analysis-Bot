import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application

from src.analysis import analyze
from src.utils.formatting import (
    welcome_text, help_text, analysis_card, signal_card,
    trend_card, levels_card, outlook_card
)
from src.utils.keyboards import main_menu_keyboard, timeframe_keyboard

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "Trader"
    await update.message.reply_text(
        welcome_text(name),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text(), parse_mode="HTML")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Analyzing XAU/USD...")
    try:
        tf = _get_tf(context)
        a = await analyze(tf)
        await msg.edit_text(analysis_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"analyze error: {e}")
        await msg.edit_text("Analysis failed. Please try again.")


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Scanning for trade setup...")
    try:
        tf = _get_tf(context)
        a = await analyze(tf)
        await msg.edit_text(signal_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"signal error: {e}")
        await msg.edit_text("Signal scan failed. Please try again.")


async def cmd_trend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Reading trend...")
    try:
        tf = _get_tf(context)
        a = await analyze(tf)
        await msg.edit_text(trend_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"trend error: {e}")
        await msg.edit_text("Trend read failed. Please try again.")


async def cmd_levels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Calculating levels...")
    try:
        tf = _get_tf(context)
        a = await analyze(tf)
        await msg.edit_text(levels_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"levels error: {e}")
        await msg.edit_text("Level calculation failed. Please try again.")


async def cmd_outlook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Generating outlook...")
    try:
        tf = _get_tf(context)
        a = await analyze(tf)
        await msg.edit_text(outlook_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"outlook error: {e}")
        await msg.edit_text("Outlook generation failed. Please try again.")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.utils.keyboards import settings_keyboard
    tf = _get_tf(context)
    text = (
        "<b>Settings</b>\n\n"
        f"Current Timeframe: <b>{tf}</b>\n\n"
        "Select a timeframe to update your default analysis window."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=settings_keyboard(tf))


def register_command_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("trend", cmd_trend))
    app.add_handler(CommandHandler("levels", cmd_levels))
    app.add_handler(CommandHandler("outlook", cmd_outlook))
    app.add_handler(CommandHandler("settings", cmd_settings))
