import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, Application

from src.analysis import analyze
from src.market_hours import market_status
from src.utils.formatting import (
    analysis_card, signal_card, trend_card, levels_card,
    outlook_card, recommend_card
)
from src.utils.keyboards import settings_keyboard, main_menu_keyboard

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


def _closed_text() -> str:
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


def _is_open() -> bool:
    return market_status()["is_open"]


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # ── Timeframe settings (always available) ─────────────────────────────────
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

    # ── Back / navigation ─────────────────────────────────────────────────────
    if data in ("back:main", "settings:back"):
        from telegram import InlineKeyboardMarkup
        tf  = _get_tf(context)
        ms  = market_status()
        mkt_status = "OPEN" if ms["is_open"] else "CLOSED"
        text = (
            f"Market: <b>{mkt_status}</b> — {ms['note']}\n\n"
            f"Timeframe: <b>{tf}</b>\n\n"
            "Use the menu below to continue."
        )
        try:
            # Pass empty reply_markup to clear the inline keyboard
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([])
            )
        except Exception:
            pass   # message may be identical — Telegram rejects no-op edits
        return

    # ── Ignore header-only buttons ─────────────────────────────────────────────
    if data in ("settings:tf_header",):
        return

    # ── All analysis callbacks — blocked when market is closed ─────────────────
    if not _is_open():
        await query.edit_message_text(_closed_text(), parse_mode="HTML")
        return

    tf = data.split(":")[1] if ":" in data else _get_tf(context)
    context.user_data["timeframe"] = tf

    if data.startswith("recommend:"):
        await query.edit_message_text("Scanning indicators...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(recommend_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback recommend: {e}")
            await query.edit_message_text("Recommendation failed. Please try again.")

    elif data.startswith("analyze:"):
        await query.edit_message_text("Analyzing XAU/USD...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(analysis_card(a), parse_mode="HTML")
            # Auto-follow with signal if there is an actionable entry
            if a.action in ("BUY", "SELL"):
                await query.message.reply_text(signal_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback analyze: {e}")
            await query.edit_message_text("Analysis failed. Please try again.")

    elif data.startswith("signal:"):
        await query.edit_message_text("Scanning for trade setup...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(signal_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback signal: {e}")
            await query.edit_message_text("Signal scan failed. Please try again.")

    elif data.startswith("trend:"):
        await query.edit_message_text("Reading trend...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(trend_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback trend: {e}")
            await query.edit_message_text("Trend read failed. Please try again.")

    elif data.startswith("levels:"):
        await query.edit_message_text("Calculating levels...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(levels_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback levels: {e}")
            await query.edit_message_text("Level calculation failed. Please try again.")

    elif data.startswith("outlook:"):
        await query.edit_message_text("Generating outlook...")
        try:
            a = await analyze(tf)
            await query.edit_message_text(outlook_card(a), parse_mode="HTML")
        except Exception as e:
            logger.error(f"callback outlook: {e}")
            await query.edit_message_text("Outlook generation failed. Please try again.")


def register_callback_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_callback))
