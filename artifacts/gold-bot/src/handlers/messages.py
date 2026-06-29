import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters, Application

from src.analysis import analyze
from src.alerts import is_subscribed, subscribe, unsubscribe
from src.market_hours import market_status
from src.utils.formatting import (
    analysis_card, signal_card, trend_card, levels_card,
    outlook_card, recommend_card, news_card
)
from src.utils.keyboards import main_menu_keyboard, settings_keyboard, alerts_keyboard

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


def _is_market_open() -> bool:
    return market_status()["is_open"]


def _market_closed_reply() -> str:
    ms = market_status()
    lines = [
        "MARKET CLOSED  |  XAU/USD",
        "=" * 28,
        f"Status:  {ms['status_text']}",
        f"Info:    {ms['note']}",
        "=" * 28,
        "Gold futures trade:",
        "Sun 6 PM  to  Fri 5 PM ET",
        "Daily break: 5:00–6:00 PM ET",
        "─" * 28,
        "Analysis is only available",
        "when the market is open.",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip().lower()

    # ── Analysis commands — require market open ────────────────────────────────
    if text in ("recommend", "analyze", "signal", "trend", "levels", "outlook"):
        if not _is_market_open():
            await update.message.reply_text(_market_closed_reply(), parse_mode="HTML")
            return

    if text == "recommend":
        msg = await update.message.reply_text("Scanning indicators...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(recommend_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg recommend error: {e}")
            await msg.edit_text("Recommendation failed. Please try again.")

    elif text == "analyze":
        msg = await update.message.reply_text("Analyzing XAU/USD...")
        try:
            tf = _get_tf(context)
            a = await analyze(tf)
            await msg.edit_text(analysis_card(a), parse_mode="HTML")
            # Auto-follow with signal if there is an actionable entry
            if a.action in ("BUY", "SELL"):
                await update.message.reply_text(signal_card(a), parse_mode="HTML")
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

    elif text == "news":
        msg = await update.message.reply_text("Fetching gold headlines...")
        try:
            from src.news import fetch_gold_news
            items = await fetch_gold_news()
            await msg.edit_text(news_card(items), parse_mode="HTML")
        except Exception as e:
            logger.error(f"msg news error: {e}")
            await msg.edit_text("Could not fetch news right now. Try again shortly.")

    elif text == "alerts":
        chat_id = update.effective_chat.id
        subscribed = is_subscribed(chat_id)
        status = "ON" if subscribed else "OFF"
        ms = market_status()
        mkt_status = "OPEN" if ms["is_open"] else "CLOSED"
        text_out = (
            "<b>Alerts</b>\n\n"
            f"Status: <b>{status}</b>\n"
            f"Market is currently <b>{mkt_status}</b> — {ms['note']}.\n\n"
            "When alerts are ON, you will receive automatic notifications whenever "
            "a high-confidence BUY or SELL entry is detected on XAU/USD.\n\n"
            "Checks run every 1 minute. Alerts fire as soon as a signal is detected."
        )
        await update.message.reply_text(
            text_out, parse_mode="HTML", reply_markup=alerts_keyboard(subscribed)
        )

    elif text == "settings":
        tf = _get_tf(context)
        text_out = (
            "<b>Settings</b>\n\n"
            f"Current Timeframe: <b>{tf}</b>\n\n"
            "Select a timeframe to update your default analysis window.\n\n"
            "<b>Trade types by timeframe:</b>\n"
            "M5 / M15  —  Scalp\n"
            "M30 / H1  —  Intraday\n"
            "H4        —  Swing\n"
            "D1        —  Position"
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
