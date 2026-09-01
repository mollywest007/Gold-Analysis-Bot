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
from src.utils.keyboards import (
    alerts_keyboard, settings_keyboard, main_menu_keyboard, refresh_keyboard,
)
from src.alerts import is_registered, register_user, unregister_user
from src.user_preferences import (
    get_mode as get_user_mode,
    get_mode_config as get_user_mode_config,
    get_timeframe as get_user_timeframe,
    set_mode as set_user_mode,
    set_timeframe as set_user_timeframe,
)

logger = logging.getLogger(__name__)


def _get_tf(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    cfg = get_user_mode_config(chat_id)
    selected = context.user_data.get("timeframe")
    return selected if selected in cfg.scan_timeframes else get_user_timeframe(chat_id)


def _scan_timeframes(chat_id: int) -> list[str]:
    return list(get_user_mode_config(chat_id).scan_timeframes)


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
    chat_id = update.effective_chat.id

    # ── Explicit automatic-alert controls ──────────────────────────────────────
    if data in ("alerts:on", "alerts:off", "alerts:status"):
        chat_id = update.effective_chat.id
        if data == "alerts:on":
            register_user(chat_id)
            await query.answer("Automatic alerts turned ON.")
        elif data == "alerts:off":
            unregister_user(chat_id)
            await query.answer("Automatic alerts turned OFF.")
        else:
            await query.answer()

        is_on = is_registered(chat_id)
        state_text = "ON" if is_on else "OFF"
        await query.edit_message_text(
            f"<b>Automatic alerts: {state_text}</b>\n\n"
            "Choose exactly what you want. Your choice is saved immediately.\n\n"
            "You will still receive replies to commands when alerts are OFF.",
            parse_mode="HTML",
            reply_markup=alerts_keyboard(is_on),
        )
        return

    # ── Timeframe settings (always available) ─────────────────────────────────
    if data.startswith("set_tf:"):
        await query.answer()
        tf = data.split(":")[1]
        cfg = get_user_mode_config(chat_id)
        if tf not in cfg.scan_timeframes:
            await query.answer(
                f"{tf} is not available in {cfg.label} Mode.",
                show_alert=True,
            )
            return
        set_user_timeframe(chat_id, tf)
        context.user_data["timeframe"] = tf
        text = (
            "<b>Settings</b>\n\n"
            f"Timeframe updated: <b>{tf}</b>\n"
            f"Mode: <b>{cfg.emoji} {cfg.label}</b>\n\n"
            "Select a timeframe to update your default analysis window."
        )
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=settings_keyboard(tf, cfg.name),
        )
        return

    if data.startswith("set_mode:"):
        await query.answer()
        mode_name = data.split(":", 1)[1]
        try:
            cfg = set_user_mode(chat_id, mode_name)
        except ValueError:
            await query.answer("Unknown analysis mode.", show_alert=True)
            return
        # set_mode persists the previous timeframe when it is valid in the new
        # mode, otherwise it selects that mode's preferred timeframe.
        selected_tf = get_user_timeframe(chat_id)
        context.user_data["timeframe"] = selected_tf
        text = (
            "<b>Settings</b>\n\n"
            f"Analysis Mode: <b>{cfg.emoji} {cfg.label}</b>\n"
            f"{cfg.description}\n\n"
            f"Current timeframe: <b>{selected_tf}</b>\n"
            f"Scans: <b>{', '.join(cfg.scan_timeframes)}</b>\n\n"
            f"{cfg.tip}"
        )
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=settings_keyboard(selected_tf, cfg.name),
        )
        return

    # ── Back / navigation ─────────────────────────────────────────────────────
    if data in ("back:main", "settings:back"):
        await query.answer()
        from telegram import InlineKeyboardMarkup
        tf  = _get_tf(context, chat_id)
        ms  = market_status()
        mkt_status = "OPEN" if ms["is_open"] else "CLOSED"
        text = (
            f"Market: <b>{mkt_status}</b> — {ms['note']}\n\n"
            f"Mode: <b>{get_user_mode_config(chat_id).label}</b>\n"
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
    if data in ("settings:tf_header", "settings:mode_header"):
        await query.answer()
        return

    # ── Refresh buttons ────────────────────────────────────────────────────────
    if data.startswith("refresh:"):
        parts = data.split(":")          # ["refresh", command, tf]
        command = parts[1] if len(parts) > 1 else ""
        tf_arg  = parts[2] if len(parts) > 2 else _get_tf(context, chat_id)
        tf      = tf_arg if tf_arg != "all" else _get_tf(context, chat_id)
        kb = refresh_keyboard(command, tf_arg)
        mode_name = get_user_mode(chat_id)

        # Always answer the query first so Telegram never shows a frozen button
        await query.answer()

        _unchanged = False
        try:
            # ── Commands available 24/7 (no market-open check) ────────────────
            if command == "active":
                from src import trade_tracker
                from src.analysis.market_data import get_gold_price
                from src.utils.formatting import active_trades_card
                try:
                    price = await get_gold_price()
                except Exception:
                    price = 0.0
                # Refresh the persisted position snapshot after the network
                # wait so this panel cannot render a pre-update entry record.
                open_trades = trade_tracker.get_active_trades_for_account(
                    chat_id,
                    mode=get_user_mode(chat_id),
                    timeframe=_get_tf(context, chat_id),
                )
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
                await query.edit_message_text(f"Re-analysing {tf}…", reply_markup=kb)
                a = await analyze(tf, mode=mode_name)
                await query.edit_message_text(
                    pro_analysis_card(a), parse_mode="HTML", reply_markup=kb
                )

            else:
                # ── Market-open gate for analysis commands ────────────────────────
                if not _is_open():
                    await query.edit_message_text(_closed_text(), parse_mode="HTML",
                                                  reply_markup=kb)
                elif command == "analyze":
                    await query.edit_message_text("Analyzing all timeframes...", reply_markup=kb)
                    # Sequential — see messages.py for explanation
                    _analyses = []
                    for _tf in _scan_timeframes(chat_id):
                        try:
                            _analyses.append(await analyze(_tf, mode=mode_name))
                        except Exception as _e:
                            logger.warning(f"analyze({_tf}) skipped: {_e}")
                    await query.edit_message_text(
                        multi_timeframe_card(_analyses), parse_mode="HTML", reply_markup=kb
                    )

                elif command == "signal":
                    await query.edit_message_text("Scanning for setup...", reply_markup=kb)
                    a = await analyze(tf, mode=mode_name)
                    await query.edit_message_text(
                        signal_card(a), parse_mode="HTML", reply_markup=kb
                    )

                elif command == "trend":
                    await query.edit_message_text("Reading trend...", reply_markup=kb)
                    a = await analyze(tf, mode=mode_name)
                    await query.edit_message_text(
                        trend_card(a), parse_mode="HTML", reply_markup=kb
                    )

                elif command == "levels":
                    await query.edit_message_text("Calculating levels...", reply_markup=kb)
                    a = await analyze(tf, mode=mode_name)
                    await query.edit_message_text(
                        levels_card(a), parse_mode="HTML", reply_markup=kb
                    )

                elif command == "outlook":
                    await query.edit_message_text("Generating outlook...", reply_markup=kb)
                    a = await analyze(tf, mode=mode_name)
                    await query.edit_message_text(
                        outlook_card(a), parse_mode="HTML", reply_markup=kb
                    )

                elif command == "recommend":
                    from src.utils.formatting import recommend_multi_card
                    await query.edit_message_text("Scanning all timeframes...", reply_markup=kb)
                    results = await asyncio.gather(
                        *[
                            analyze(tf_name, mode=mode_name)
                            for tf_name in _scan_timeframes(chat_id)
                        ],
                        return_exceptions=True,
                    )
                    analyses = [r for r in results if not isinstance(r, Exception)]
                    await query.edit_message_text(
                        recommend_multi_card(analyses), parse_mode="HTML", reply_markup=kb
                    )

        except Exception as e:
            err_str = str(e).lower()
            if "message is not modified" not in err_str:
                logger.error(f"refresh:{command} error: {e}")
                try:
                    await query.edit_message_text(
                        "Refresh failed — try again in a moment.", reply_markup=kb
                    )
                except Exception:
                    pass
        return

    # ── All analysis callbacks — blocked when market is closed ─────────────────
    await query.answer()

    mode_name = get_user_mode(chat_id)
    tf      = data.split(":")[1] if ":" in data else _get_tf(context, chat_id)
    command = data.split(":")[0]          # e.g. "signal", "trend", "analyze" …
    kb      = refresh_keyboard(command, tf)
    context.user_data["timeframe"] = tf

    if not _is_open():
        try:
            await query.edit_message_text(_closed_text(), parse_mode="HTML",
                                          reply_markup=kb)
        except Exception:
            pass
        return

    if data.startswith("recommend:"):
        await query.edit_message_text("Scanning all timeframes…", reply_markup=kb)
        try:
            from src.utils.formatting import recommend_multi_card as _rmc
            import re as _re
            results = await asyncio.gather(
                *[
                    analyze(tf_name, mode=mode_name)
                    for tf_name in _scan_timeframes(chat_id)
                ],
                return_exceptions=True,
            )
            analyses = [r for r in results if not isinstance(r, Exception)]
            card = _rmc(analyses)
            try:
                await query.edit_message_text(card, parse_mode="HTML", reply_markup=kb)
            except Exception as html_err:
                logger.warning(f"callback recommend HTML error (falling back to plain): {html_err}")
                import re as _re2
                plain = _re2.sub(r"<[^>]+>", "", card).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                await query.edit_message_text(plain, reply_markup=kb)
        except Exception as e:
            logger.error(f"callback recommend: {e}")
            await query.edit_message_text("Scanning failed — please try again in a moment.",
                                          reply_markup=kb)

    elif data.startswith("analyze:"):
        await query.edit_message_text("Analyzing all timeframes…", reply_markup=kb)
        try:
            results = await asyncio.gather(
                *[
                    analyze(tf_name, mode=mode_name)
                    for tf_name in _scan_timeframes(chat_id)
                ],
                return_exceptions=True,
            )
            analyses = [r for r in results if not isinstance(r, Exception)]
            await query.edit_message_text(multi_timeframe_card(analyses),
                                          parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"callback analyze: {e}")
            await query.edit_message_text("Analysis failed. Please try again.",
                                          reply_markup=kb)

    elif data.startswith("signal:"):
        await query.edit_message_text("Scanning for trade setup…", reply_markup=kb)
        try:
            a = await analyze(tf, mode=mode_name)
            await query.edit_message_text(signal_card(a), parse_mode="HTML",
                                          reply_markup=kb)
        except Exception as e:
            logger.error(f"callback signal: {e}")
            await query.edit_message_text("Signal scan failed. Please try again.",
                                          reply_markup=kb)

    elif data.startswith("trend:"):
        await query.edit_message_text("Reading trend…", reply_markup=kb)
        try:
            a = await analyze(tf, mode=mode_name)
            await query.edit_message_text(trend_card(a), parse_mode="HTML",
                                          reply_markup=kb)
        except Exception as e:
            logger.error(f"callback trend: {e}")
            await query.edit_message_text("Trend read failed. Please try again.",
                                          reply_markup=kb)

    elif data.startswith("levels:"):
        await query.edit_message_text("Calculating levels…", reply_markup=kb)
        try:
            a = await analyze(tf, mode=mode_name)
            await query.edit_message_text(levels_card(a), parse_mode="HTML",
                                          reply_markup=kb)
        except Exception as e:
            logger.error(f"callback levels: {e}")
            await query.edit_message_text("Level calculation failed. Please try again.",
                                          reply_markup=kb)

    elif data.startswith("outlook:"):
        await query.edit_message_text("Generating outlook…", reply_markup=kb)
        try:
            a = await analyze(tf, mode=mode_name)
            await query.edit_message_text(outlook_card(a), parse_mode="HTML",
                                          reply_markup=kb)
        except Exception as e:
            logger.error(f"callback outlook: {e}")
            await query.edit_message_text("Outlook generation failed. Please try again.",
                                          reply_markup=kb)


def register_callback_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_callback))
