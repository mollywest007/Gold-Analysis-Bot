import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application

from src.analysis import get_analysis
from src.alerts import is_subscribed, subscribe, unsubscribe, subscriber_count
from src.market_hours import market_status
from src.utils.formatting import (
    welcome_text, help_text, analysis_card, signal_card,
    trend_card, levels_card, outlook_card, recommend_card, news_card,
    pro_analysis_card, early_entry_card, no_early_entry_card,
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


def _age_note(tf: str) -> str:
    """Return a one-line freshness note, e.g. 'Data: 45s ago' or '' if live fetch."""
    from src.analysis import cache_age
    age = cache_age(tf)
    if age is None:
        return ""
    if age < 10:
        return "Data: live"
    if age < 60:
        return f"Data: {age}s ago"
    return f"Data: {age // 60}m ago"


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
    tf   = _get_tf(context)
    note = _age_note(tf)
    msg  = await update.message.reply_text(
        f"Running full market analysis on {tf}...{' (' + note + ')' if note else ''}"
    )
    try:
        a = await get_analysis(tf)

        # ── Part 1: Full professional market analysis ──────────────────────────
        await msg.edit_text(pro_analysis_card(a), parse_mode="HTML")

        # ── Part 2: Early entry signal OR explanation of what is missing ───────
        if a.action in ("BUY", "SELL") and a.setup_quality in ("A+", "A"):
            # Grade A/A+: send the early entry card
            await update.message.reply_text(early_entry_card(a), parse_mode="HTML")

            # Auto-attach live chart with entry details in caption
            try:
                import io
                from telegram import InputFile
                from src.chart_generator import generate_chart_image
                chart_msg = await update.message.reply_text(
                    f"Generating {tf} chart for {a.setup_quality} setup..."
                )
                img_bytes = await generate_chart_image(tf)
                if img_bytes:
                    await chart_msg.delete()
                    sl_dist = abs(a.entry - a.stop_loss)
                    rr1 = round(abs(a.tp1 - a.entry) / sl_dist, 1) if sl_dist > 0 else 0
                    rr3 = round(abs(a.tp3 - a.entry) / sl_dist, 1) if sl_dist > 0 else 0
                    entry_display = a.early_entry if a.early_entry and a.early_entry != a.entry else a.entry
                    caption = (
                        f"XAU/USD {tf}  |  {a.action}  |  Grade {a.setup_quality}\n"
                        f"Limit Entry : {entry_display:,.2f}  |  SL: {a.stop_loss:,.2f}\n"
                        f"TP1: {a.tp1:,.2f} (1:{rr1})  TP3: {a.tp3:,.2f} (1:{rr3})"
                    )
                    await update.message.reply_photo(
                        photo=InputFile(io.BytesIO(img_bytes), filename="xauusd_entry.jpg"),
                        caption=caption,
                    )
                else:
                    await chart_msg.delete()
            except Exception as chart_err:
                logger.warning(f"recommend chart failed: {chart_err}")

        elif a.action in ("BUY", "SELL"):
            # Has a direction but grade is B/C — explain what is missing
            await update.message.reply_text(no_early_entry_card(a), parse_mode="HTML")

        else:
            # Truly no signal — explain
            await update.message.reply_text(no_early_entry_card(a), parse_mode="HTML")

    except Exception as e:
        logger.error(f"recommend error: {e}")
        await msg.edit_text("Recommendation failed. Please try again.")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    tf   = _get_tf(context)
    note = _age_note(tf)
    msg  = await update.message.reply_text(f"Analyzing...{' (' + note + ')' if note else ''}")
    try:
        a = await get_analysis(tf)
        await msg.edit_text(analysis_card(a), parse_mode="HTML")
        # Auto-follow with signal if there is an actionable entry
        if a.action in ("BUY", "SELL"):
            await update.message.reply_text(signal_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"analyze error: {e}")
        await msg.edit_text("Analysis failed. Please try again.")


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: F811
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    tf   = _get_tf(context)
    note = _age_note(tf)
    msg  = await update.message.reply_text(f"Scanning for setup...{' (' + note + ')' if note else ''}")
    try:
        a = await get_analysis(tf)
        # Send signal card first (always fast)
        await msg.edit_text(signal_card(a), parse_mode="HTML")

        # When there is an actionable signal, attach a live chart automatically
        if a.action in ("BUY", "SELL"):
            try:
                import io
                from telegram import InputFile
                from src.chart_generator import generate_chart_image
                chart_msg = await update.message.reply_text(
                    f"Generating {tf} chart for this signal..."
                )
                img_bytes = await generate_chart_image(tf)
                if img_bytes:
                    await chart_msg.delete()
                    await update.message.reply_photo(
                        photo=InputFile(io.BytesIO(img_bytes), filename="xauusd_signal.jpg"),
                        caption=f"XAU/USD {tf} — {a.action} setup  |  Entry {a.entry:,.2f}  SL {a.stop_loss:,.2f}  TP1 {a.tp1:,.2f}",
                    )
                else:
                    await chart_msg.delete()
            except Exception as chart_err:
                logger.warning(f"signal chart attach failed: {chart_err}")
    except Exception as e:
        logger.error(f"signal error: {e}")
        await msg.edit_text("Signal scan failed. Please try again.")


async def cmd_trend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    tf   = _get_tf(context)
    note = _age_note(tf)
    msg  = await update.message.reply_text(f"Reading trend...{' (' + note + ')' if note else ''}")
    try:
        a = await get_analysis(tf)
        await msg.edit_text(trend_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"trend error: {e}")
        await msg.edit_text("Trend read failed. Please try again.")


async def cmd_levels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    tf   = _get_tf(context)
    note = _age_note(tf)
    msg  = await update.message.reply_text(f"Calculating levels...{' (' + note + ')' if note else ''}")
    try:
        a = await get_analysis(tf)
        await msg.edit_text(levels_card(a), parse_mode="HTML")
    except Exception as e:
        logger.error(f"levels error: {e}")
        await msg.edit_text("Level calculation failed. Please try again.")


async def cmd_outlook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    tf   = _get_tf(context)
    note = _age_note(tf)
    msg  = await update.message.reply_text(f"Generating outlook...{' (' + note + ')' if note else ''}")
    try:
        a = await get_analysis(tf)
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


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("Fetching gold headlines...")
    try:
        from src.news import fetch_gold_news
        items = await fetch_gold_news()
        await msg.edit_text(news_card(items), parse_mode="HTML")
    except Exception as e:
        logger.error(f"news error: {e}")
        await msg.edit_text("Could not fetch news right now. Try again shortly.")


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch live OHLCV data, render a chart, analyse it with Gemini Vision."""
    import html
    from src.chart_generator import generate_chart_image
    from src.chart_analysis import analyse_chart_bytes, ChartAnalysisResult
    from src.handlers.photos import _result_card
    from telegram import InputFile
    import io

    tf = _get_tf(context)
    msg = await update.message.reply_text(
        f"Fetching live XAU/USD {tf} data and generating chart...",
        parse_mode="HTML",
    )

    try:
        # 1. Generate chart image from live data
        img_bytes = await generate_chart_image(tf)
        if img_bytes is None:
            await msg.edit_text("Could not fetch market data right now. Try again shortly.")
            return

        # 2. Send the chart image
        await update.message.reply_photo(
            photo=InputFile(io.BytesIO(img_bytes), filename="xauusd_chart.jpg"),
            caption=f"XAU/USD {tf} — analysing with AI...",
            parse_mode="HTML",
        )

        await msg.edit_text("Analysing chart with Gemini Vision... this may take 15-30 seconds.")

        # 3. Analyse with Gemini Vision
        result = await analyse_chart_bytes(img_bytes)

        # 4. Send analysis card
        await update.message.reply_text(_result_card(result), parse_mode="HTML")
        await msg.delete()

    except Exception as e:
        logger.error(f"cmd_chart error: {e}", exc_info=True)
        await msg.edit_text(
            f"Chart analysis failed. Try again shortly.\n"
            f"<i>Error: {html.escape(type(e).__name__)}</i>",
            parse_mode="HTML",
        )


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
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("chart",     cmd_chart))
