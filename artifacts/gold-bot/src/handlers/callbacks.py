import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, Application

from src.analysis import analyze
from src.market_hours import market_status
from src.utils.formatting import (
    analysis_card, signal_card, trend_card, levels_card,
    outlook_card, recommend_card, multi_timeframe_card,
)
from src.utils.keyboards import settings_keyboard, main_menu_keyboard, refresh_keyboard

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
    data = query.data or ""

    # ── Timeframe settings (always available) ─────────────────────────────────
    if data.startswith("set_tf:"):
        await query.answer()
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
        await query.answer()
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
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([])
            )
        except Exception:
            pass   # message may be identical — Telegram rejects no-op edits
        return

    # ── Ignore header-only buttons ─────────────────────────────────────────────
    if data in ("settings:tf_header",):
        await query.answer()
        return

    # ── Refresh buttons ────────────────────────────────────────────────────────
    if data.startswith("refresh:"):
        parts = data.split(":")          # ["refresh", command, tf]
        command = parts[1] if len(parts) > 1 else ""
        tf_arg  = parts[2] if len(parts) > 2 else _get_tf(context)
        tf      = tf_arg if tf_arg != "all" else _get_tf(context)
        kb = refresh_keyboard(command, tf_arg)

        _unchanged = False
        try:
            # ── Commands available 24/7 (no market-open check) ────────────────
            if command == "active":
                from src import trade_tracker
                from src.analysis.market_data import get_gold_price
                from src.utils.formatting import active_trades_card
                open_trades = [
                    t for t in trade_tracker.get_all_trades()
                    if t.get("status") in ("open", "tp1_hit")
                    or (t.get("status") == "tp2_hit" and t.get("tp3") and not t.get("tp3_hit"))
                ]
                try:
                    price = await get_gold_price()
                except Exception:
                    price = 0.0
                await query.edit_message_text(
                    active_trades_card(open_trades, price), parse_mode="HTML", reply_markup=kb
                )

            elif command == "news":
                from src.news import fetch_gold_news
                from src.utils.formatting import news_card
                items = await fetch_gold_news()
                await query.edit_message_text(
                    news_card(items), parse_mode="HTML", reply_markup=kb
                )

            elif command == "history":
                from src import trade_tracker
                from src.utils.formatting import history_card
                trades = trade_tracker.get_all_trades()
                stats  = trade_tracker.get_stats()
                await query.edit_message_text(
                    history_card(trades, stats), parse_mode="HTML", reply_markup=kb
                )

            elif command == "chart":
                # Re-run engine analysis for the chart TF
                from src.utils.formatting import pro_analysis_card
                await query.answer()
                await query.edit_message_text(f"Re-analysing {tf}…", reply_markup=kb)
                a = await analyze(tf)
                await query.edit_message_text(
                    pro_analysis_card(a), parse_mode="HTML", reply_markup=kb
                )
                return  # answer already called above

            else:
                # ── Market-open gate for analysis commands ────────────────────────
                if not _is_open():
                    await query.edit_message_text(_closed_text(), parse_mode="HTML",
                                                  reply_markup=kb)
                elif command == "analyze":
                    await query.answer()
                    await query.edit_message_text("Analyzing all timeframes...", reply_markup=kb)
                    results = await asyncio.gather(
                        analyze("M5"), analyze("M15"), analyze("M30"),
                        analyze("H1"), analyze("H4"), analyze("D1"),
                        return_exceptions=True,
                    )
                    analyses = [r for r in results if not isinstance(r, Exception)]
                    await query.edit_message_text(
                        multi_timeframe_card(analyses), parse_mode="HTML", reply_markup=kb
                    )
                    return  # answer already called above

                elif command == "signal":
                    await query.answer()
                    await query.edit_message_text("Scanning for setup...", reply_markup=kb)
                    a = await analyze(tf)
                    await query.edit_message_text(
                        signal_card(a), parse_mode="HTML", reply_markup=kb
                    )
                    return

                elif command == "trend":
                    await query.answer()
                    await query.edit_message_text("Reading trend...", reply_markup=kb)
                    a = await analyze(tf)
                    await query.edit_message_text(
                        trend_card(a), parse_mode="HTML", reply_markup=kb
                    )
                    return

                elif command == "levels":
                    await query.answer()
                    await query.edit_message_text("Calculating levels...", reply_markup=kb)
                    a = await analyze(tf)
                    await query.edit_message_text(
                        levels_card(a), parse_mode="HTML", reply_markup=kb
                    )
                    return

                elif command == "outlook":
                    await query.answer()
                    await query.edit_message_text("Generating outlook...", reply_markup=kb)
                    a = await analyze(tf)
                    await query.edit_message_text(
                        outlook_card(a), parse_mode="HTML", reply_markup=kb
                    )
                    return

                elif command == "recommend":
                    from src.utils.formatting import recommend_multi_card
                    await query.answer()
                    await query.edit_message_text("Scanning all timeframes...", reply_markup=kb)
                    results = await asyncio.gather(
                        analyze("M5"), analyze("M15"), analyze("M30"),
                        analyze("H1"), analyze("H4"), analyze("D1"),
                        return_exceptions=True,
                    )
                    analyses = [r for r in results if not isinstance(r, Exception)]
                    await query.edit_message_text(
                        recommend_multi_card(analyses), parse_mode="HTML", reply_markup=kb
                    )
                    return

        except Exception as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                _unchanged = True
            else:
                logger.error(f"refresh:{command} error: {e}")
                await query.answer()
                try:
                    await query.edit_message_text(
                        "Refresh failed — try again in a moment.", reply_markup=kb
                    )
                except Exception:
                    pass
                return

        # Give feedback: toast if unchanged, silent dismiss if updated
        if _unchanged:
            await query.answer("✓ Already up to date")
        else:
            await query.answer()
        return

    # ── All analysis callbacks — blocked when market is closed ─────────────────
    await query.answer()
    if not _is_open():
        await query.edit_message_text(_closed_text(), parse_mode="HTML")
        return

    tf = data.split(":")[1] if ":" in data else _get_tf(context)
    context.user_data["timeframe"] = tf

    if data.startswith("recommend:"):
        await query.edit_message_text("Scanning all timeframes...")
        try:
            from src.utils.formatting import recommend_multi_card as _rmc
            import re as _re
            results = await asyncio.gather(
                analyze("M5"), analyze("M15"), analyze("M30"),
                analyze("H1"), analyze("H4"), analyze("D1"),
                return_exceptions=True,
            )
            analyses = [r for r in results if not isinstance(r, Exception)]
            card = _rmc(analyses)
            try:
                await query.edit_message_text(card, parse_mode="HTML")
            except Exception as html_err:
                logger.warning(f"callback recommend HTML error (falling back to plain): {html_err}")
                plain = _re.sub(r"<[^>]+>", "", card).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                await query.edit_message_text(plain)
        except Exception as e:
            logger.error(f"callback recommend: {e}")
            await query.edit_message_text("Scanning failed — please try again in a moment.")

    elif data.startswith("analyze:"):
        await query.edit_message_text("Analyzing all timeframes...")
        try:
            results = await asyncio.gather(
                analyze("M5"), analyze("M15"), analyze("M30"),
                analyze("H1"), analyze("H4"), analyze("D1"),
                return_exceptions=True,
            )
            analyses = [r for r in results if not isinstance(r, Exception)]
            await query.edit_message_text(multi_timeframe_card(analyses), parse_mode="HTML")
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
