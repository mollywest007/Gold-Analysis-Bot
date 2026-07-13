import logging
import time
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application

from src.analysis import get_analysis
from src.alerts import register_user
from src.market_hours import market_status
from src.utils.formatting import (
    welcome_text, help_text, analysis_card, signal_card,
    trend_card, levels_card, outlook_card, recommend_card, news_card,
    pro_analysis_card, early_entry_card, no_early_entry_card,
)
from src.utils.keyboards import main_menu_keyboard, settings_keyboard, refresh_keyboard

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("timeframe", "H1")


def _open_trade_banner(tf: str) -> str:
    """
    If there's already a LOCKED open trade on this timeframe, return a clear
    HTML banner showing its real entry/SL/TP — the exact numbers from the
    original alert, never recalculated. Empty string if no open trade.

    This exists because /signal, /recommend etc. run a FRESH analysis every
    time (entry/SL/TP shift as price and indicators move), while an open
    trade's levels are frozen at the moment it was opened. Without this
    banner, users compare live-recalculated numbers to their real trade's
    numbers and think the bot is "reading wrong" — it isn't, they're two
    different things.
    """
    from src import trade_tracker
    open_trades = [
        t for t in trade_tracker.get_all_trades()
        if t.get("status") in ("open", "tp1_hit") and t.get("timeframe") == tf
    ]
    if not open_trades:
        return ""
    t = open_trades[0]
    age_secs = time.time() - t.get("opened_at", 0)
    age_str = f"{int(age_secs // 60)}m ago" if age_secs < 3600 else f"{int(age_secs // 3600)}h {int((age_secs % 3600) // 60)}m ago"
    tp2_str = f"  TP2: {t['tp2']:,.2f}" if t.get("tp2") else ""
    return (
        f"🔒 <b>You already have a LOCKED {t['direction']} trade on {tf}</b> (opened {age_str})\n"
        f"Entry: {t['entry']:,.2f}  SL: {t['sl']:,.2f}  TP1: {t['tp1']:,.2f}{tp2_str}\n"
        f"These are your <b>real, unchanging</b> trade levels — use /active for live P&amp;L.\n"
        f"The analysis below is a fresh market read and will show different "
        f"numbers as price moves — it is <b>not</b> a new trade."
    )


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
    name    = update.effective_user.first_name or "Trader"
    chat_id = update.effective_chat.id
    register_user(chat_id)
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

        # ── Simulated data guard — block before any card is shown ─────────────
        if getattr(a, "is_simulated", False):
            await msg.edit_text(
                "⚠️ <b>DATA UNAVAILABLE</b>\n"
                "Market data could not be fetched (Yahoo Finance is unreachable). "
                "Analysis is based on simulated prices and is <b>not reliable</b>.\n\n"
                "Please try again in a few minutes.",
                parse_mode="HTML",
            )
            return

        # ── Part 1: Full professional market analysis ──────────────────────────
        await msg.edit_text(pro_analysis_card(a), parse_mode="HTML",
                            reply_markup=refresh_keyboard("recommend", tf))

        banner = _open_trade_banner(tf)
        if banner:
            await update.message.reply_text(banner, parse_mode="HTML")

        # ── Part 2: Entry signal — only A/A+ setups get an entry card ────────────
        if a.action in ("BUY", "SELL"):

            # Grade gate — C setups should never be traded
            if a.setup_quality == "C":
                await update.message.reply_text(
                    f"⚠️ <b>Grade C — Do NOT enter this trade</b>\n\n"
                    f"The engine sees a {a.action} direction but the setup quality is too low "
                    f"(win probability {a.win_probability}%, ADX {a.adx:.1f}). "
                    f"Entering here has a poor risk/reward profile.\n\n"
                    f"<b>Wait for a Grade A or A+ setup.</b> Use /signal to keep scanning.",
                    parse_mode="HTML",
                )
                return

            await update.message.reply_text(early_entry_card(a), parse_mode="HTML")

            # Always attach the live chart for every BUY/SELL signal
            try:
                import io
                from telegram import InputFile
                from src.chart_generator import generate_chart_image
                chart_msg = await update.message.reply_text(
                    f"Generating {tf} chart..."
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

        else:
            # Genuinely no direction — engine gated it (ranging, Asian session, HTF block, etc.)
            await update.message.reply_text(no_early_entry_card(a), parse_mode="HTML")

    except Exception as e:
        logger.error(f"recommend error: {e}")
        await msg.edit_text("Recommendation failed. Please try again.")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_market_open():
        await update.message.reply_text(_market_closed_text(), parse_mode="HTML")
        return
    import asyncio
    from src.analysis import analyze as _analyze
    from src.utils.formatting import multi_timeframe_card
    msg = await update.message.reply_text("Analyzing all timeframes...")
    try:
        results = await asyncio.gather(
            _analyze("M5"), _analyze("M15"), _analyze("M30"),
            _analyze("H1"), _analyze("H4"), _analyze("D1"),
            return_exceptions=True,
        )
        analyses = [r for r in results if not isinstance(r, Exception)]
        await msg.edit_text(
            multi_timeframe_card(analyses), parse_mode="HTML",
            reply_markup=refresh_keyboard("analyze", "all"),
        )
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
        # Simulated data guard — block before showing any signal
        if getattr(a, "is_simulated", False):
            await msg.edit_text(
                "⚠️ <b>DATA UNAVAILABLE</b>\n"
                "Market data could not be fetched. Signal is based on simulated prices "
                "and is <b>not reliable</b>. Please try again in a few minutes.",
                parse_mode="HTML",
            )
            return

        await msg.edit_text(signal_card(a), parse_mode="HTML",
                            reply_markup=refresh_keyboard("signal", tf))

        banner = _open_trade_banner(tf)
        if banner:
            await update.message.reply_text(banner, parse_mode="HTML")

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
        await msg.edit_text(trend_card(a), parse_mode="HTML",
                            reply_markup=refresh_keyboard("trend", tf))
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
        await msg.edit_text(levels_card(a), parse_mode="HTML",
                            reply_markup=refresh_keyboard("levels", tf))
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
        await msg.edit_text(outlook_card(a), parse_mode="HTML",
                            reply_markup=refresh_keyboard("outlook", tf))
    except Exception as e:
        logger.error(f"outlook error: {e}")
        await msg.edit_text("Outlook generation failed. Please try again.")



async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src import trade_tracker
    from src.analysis.market_data import get_gold_price
    from src.utils.formatting import active_trades_card
    msg   = await update.message.reply_text("Fetching active trades...")
    open_trades = [t for t in trade_tracker.get_all_trades() if t.get("status") == "open"]
    try:
        price = await get_gold_price()
    except Exception:
        price = 0.0
    text = active_trades_card(open_trades, price)
    await msg.edit_text(text, parse_mode="HTML")


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


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src import trade_tracker
    from src.utils.formatting import history_card
    trades = trade_tracker.get_all_trades()
    stats  = trade_tracker.get_stats()
    await update.message.reply_text(history_card(trades, stats), parse_mode="HTML")


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch live OHLCV data, render a chart, analyse it with Gemini Vision."""
    import html as _html
    import asyncio as _asyncio
    from src.chart_generator import generate_chart_image
    from src.chart_analysis import analyse_chart_bytes
    from src.handlers.photos import _result_card
    from src.market_hours import market_status
    from telegram import InputFile
    from telegram.error import NetworkError, TimedOut
    import io

    tf  = _get_tf(context)
    ms  = market_status()
    msg = await update.message.reply_text(
        f"Generating XAU/USD {tf} chart...",
        parse_mode="HTML",
    )

    # ── Step 1: Generate chart ─────────────────────────────────────────────────
    try:
        img_bytes = await generate_chart_image(tf)
    except Exception as e:
        logger.error(f"cmd_chart — chart generation error: {e}", exc_info=True)
        await msg.edit_text("Chart generation failed. Try again shortly.")
        return

    if img_bytes is None:
        await msg.edit_text("Could not generate the chart. Try again shortly.")
        return

    # ── Step 2: Send chart photo (retry once on transient network error) ───────
    sent_photo = False
    for attempt in range(2):
        try:
            market_note = "" if ms["is_open"] else "  (market closed — showing last session data)"
            await update.message.reply_photo(
                photo=InputFile(io.BytesIO(img_bytes), filename="xauusd_chart.jpg"),
                caption=f"XAU/USD {tf}{market_note}",
            )
            sent_photo = True
            break
        except (NetworkError, TimedOut) as e:
            if attempt == 0:
                logger.warning(f"cmd_chart photo send attempt 1 failed ({type(e).__name__}), retrying...")
                await _asyncio.sleep(2)
            else:
                logger.error(f"cmd_chart photo send failed after retry: {e}")
                await msg.edit_text(
                    "Chart image generated but could not be sent — Telegram connection error.\n"
                    "Please try again in a moment."
                )
                return
        except Exception as e:
            logger.error(f"cmd_chart photo send error: {e}", exc_info=True)
            await msg.edit_text(f"Chart image could not be sent. Try again shortly.")
            return

    # ── Step 3: Gemini Vision analysis (with engine fallback on quota) ────────
    await msg.edit_text("Analysing chart with AI... this takes 15-30 seconds.")
    gemini_ok = False
    try:
        result = await analyse_chart_bytes(img_bytes)
        gemini_ok = True
    except Exception as e:
        err_str = str(e)
        logger.error(f"cmd_chart — Gemini analysis error: {e}", exc_info=True)
        is_quota = "429" in err_str or "quota" in err_str.lower()
        if is_quota:
            logger.info("cmd_chart — Gemini quota hit, falling back to engine analysis.")
            await msg.edit_text("AI vision quota reached — sending engine analysis instead.")
        else:
            short = _html.escape(err_str[:200])
            await msg.edit_text(
                f"Chart sent. AI analysis failed — try again shortly.\n<i>{short}</i>",
                parse_mode="HTML",
            )
            return

    # ── Step 4: Send analysis card ────────────────────────────────────────────
    if gemini_ok:
        try:
            await update.message.reply_text(_result_card(result), parse_mode="HTML")
            await msg.delete()
        except Exception as e:
            logger.warning(f"cmd_chart — result card send failed: {e}")
    else:
        # Engine fallback — same cards as /recommend
        try:
            from src.analysis import analyze
            from src.utils.formatting import pro_analysis_card, early_entry_card
            a = await analyze(tf)
            await update.message.reply_text(pro_analysis_card(a), parse_mode="HTML")
            await update.message.reply_text(early_entry_card(a), parse_mode="HTML")
            await msg.delete()
        except Exception as e:
            logger.error(f"cmd_chart — engine fallback failed: {e}")
            await msg.edit_text("Chart sent. Analysis unavailable right now — try /recommend instead.")


def register_command_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("recommend", cmd_recommend))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(CommandHandler("signal",    cmd_signal))
    app.add_handler(CommandHandler("trend",     cmd_trend))
    app.add_handler(CommandHandler("levels",    cmd_levels))
    app.add_handler(CommandHandler("outlook",   cmd_outlook))
    app.add_handler(CommandHandler("active",    cmd_active))
    app.add_handler(CommandHandler("settings",  cmd_settings))
    app.add_handler(CommandHandler("news",      cmd_news))
    app.add_handler(CommandHandler("chart",     cmd_chart))
    app.add_handler(CommandHandler("history",   cmd_history))
