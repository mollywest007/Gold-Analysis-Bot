import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application

from src.analysis import analyze
from src.alerts import is_subscribed, subscribe, unsubscribe, subscriber_count
from src.market_hours import market_status
from src.utils.formatting import (
    welcome_text, help_text, analysis_card, signal_card,
    trend_card, levels_card, outlook_card, recommend_card
)
from src.utils.keyboards import main_menu_keyboard, settings_keyboard, alerts_keyboard

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


def _market_closed_text() -> str:
    ms = market_status()
    lines = [
        "MARKET CLOSED",
        "=" * 28,
        f"Status:  {ms['status_text']}",
        f"Info:    {ms['note']}",
        "=" * 28,
        "Analysis is only available",
        "when the market is open.",
        "─" * 28,
        "Gold futures trade:",
        "Sun 6 PM  to  Fri 5 PM ET",
        "Daily break: 5-6 PM ET",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def _is_market_open() -> bool:
    return market_status()["is_open"]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "Trader"
    await update.message.reply_text(
        welcome_text(name),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text(), parse_mode="HTML")


async def cmd_recommend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    msg = await update.message.reply_text("Scanning indicators...")
    try:
        tf = _get_tf(context)
        a  = await analyze(tf)
        await msg.edit_text(recommend_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"recommend error: {e}")
        await msg.edit_text("Recommendation failed. Please try again.")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    msg = await update.message.reply_text("Analyzing XAU/USD...")
    try:
        tf = _get_tf(context)
        a  = await analyze(tf)
        await msg.edit_text(analysis_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"analyze error: {e}")
        await msg.edit_text("Analysis failed. Please try again.")


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    msg = await update.message.reply_text("Scanning for trade setup...")
    try:
        tf = _get_tf(context)
        a  = await analyze(tf)
        await msg.edit_text(signal_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"signal error: {e}")
        await msg.edit_text("Signal scan failed. Please try again.")


async def cmd_trend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    msg = await update.message.reply_text("Reading trend...")
    try:
        tf = _get_tf(context)
        a  = await analyze(tf)
        await msg.edit_text(trend_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"trend error: {e}")
        await msg.edit_text("Trend read failed. Please try again.")


async def cmd_levels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    msg = await update.message.reply_text("Calculating levels...")
    try:
        tf = _get_tf(context)
        a  = await analyze(tf)
        await msg.edit_text(levels_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"levels error: {e}")
        await msg.edit_text("Level calculation failed. Please try again.")


async def cmd_outlook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    msg = await update.message.reply_text("Generating outlook...")
    try:
        tf = _get_tf(context)
        a  = await analyze(tf)
        await msg.edit_text(outlook_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"outlook error: {e}")
        await msg.edit_text("Outlook generation failed. Please try again.")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id    = update.effective_chat.id
    subscribed = is_subscribed(chat_id)
    status     = "ON" if subscribed else "OFF"
    ms         = market_status()
    mkt_note   = f"\nMarket is currently <b>{'OPEN' if ms['is_open'] else 'CLOSED'}</b> — {ms['note']}."
    text = (
        "<b>Alerts</b>\n\n"
        f"Status: <b>{status}</b>\n"
        f"{mkt_note}\n\n"
        "When alerts are ON you receive automatic notifications "
        "whenever a high-confidence BUY or SELL is detected.\n\n"
        "Alerts only fire during market hours. Checks run every 5 minutes."
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=alerts_keyboard(subscribed)
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tf   = _get_tf(context)
    text = (
        "<b>Settings</b>\n\n"
        f"Current Timeframe: <b>{tf}</b>\n\n"
        "Select a timeframe to update your default analysis window."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=settings_keyboard(tf))


def register_command_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("recommend", cmd_recommend))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(CommandHandler("signal",    cmd_signal))
    app.add_handler(CommandHandler("trend",     cmd_trend))
    app.add_handler(CommandHandler("levels",    cmd_levels))
    app.add_handler(CommandHandler("outlook",   cmd_outlook))
    app.add_handler(CommandHandler("alerts",    cmd_alerts))
    app.add_handler(CommandHandler("settings",  cmd_settings))
