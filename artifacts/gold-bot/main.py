import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from telegram import BotCommand, Update
from telegram.ext import Application, ContextTypes, TypeHandler
from telegram.ext import ApplicationHandlerStop
from src.config import (
    TELEGRAM_BOT_TOKEN,
    GOOGLE_AI_KEY,
    ALLOWED_USER_ID,
    ALLOWED_USERNAMES,
)
from src.key_health import validate_keys_on_startup, periodic_key_health_check
from src.handlers import (
    register_command_handlers,
    register_callback_handlers,
    register_message_handlers,
    register_photo_handlers,
)
from src.alerts import (
    check_and_alert,
    send_market_conditions_summary,
    send_startup_summary,
    send_trade_reminder_for_accounts,
    register_user,
    is_alerts_disabled,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
# HTTP request URLs can contain bot/API credentials as query parameters.
# Keep those URLs out of workflow logs while retaining application-level logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ALERT_INTERVAL_SECONDS  = 15    # 15 seconds — catch entries before the move extends
CACHE_REFRESH_SECONDS   = 60    # 1 minute — keeps analysis fresh


async def _access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block all users except the configured owner. Raises ApplicationHandlerStop for strangers."""
    user = update.effective_user
    if user is None:
        raise ApplicationHandlerStop

    logger.info(f"User: @{user.username} (id={user.id})")

    # Determine authorization. A numeric owner ID and the explicit username
    # allowlist are additive so adding an approved account never disables the
    # owner's access.
    username = (user.username or "").strip().lstrip("@").lower()
    authorized = (
        (ALLOWED_USER_ID and user.id == ALLOWED_USER_ID)
        or username in ALLOWED_USERNAMES
        or (not ALLOWED_USER_ID and not ALLOWED_USERNAMES)
    )

    if authorized:
        # Any successful interaction proves this chat can receive messages.
        # Do not require the user to know about /start before alerts work.
        message_text = (
            (update.message.text or "").strip().lower()
            if update.message and update.message.text
            else ""
        )
        # /alerts must be able to unsubscribe the chat; all other authorized
        # interactions keep the chat subscribed so alerts work by default.
        callback_data = (
            update.callback_query.data
            if update.callback_query and update.callback_query.data
            else ""
        )
        is_alert_control = callback_data in ("alerts:on", "alerts:off", "alerts:status")
        if (
            message_text not in ("/alerts", "alerts")
            and not is_alert_control
            and not is_alerts_disabled(update.effective_chat.id)
        ):
            register_user(update.effective_chat.id)
        return

    # Reject the stranger
    logger.warning(f"Unauthorized: @{user.username} (id={user.id})")
    if update.message:
        await update.message.reply_text("⛔ Unauthorized.")
    elif update.callback_query:
        await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
    raise ApplicationHandlerStop


async def _warm_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-fetch every subscribed account's configured analysis set."""
    from src.market_hours import market_status
    from src.analysis.cache import warm
    from src.alerts import _load
    from src.user_preferences import get_mode_config as get_user_mode_config
    if not market_status()["is_open"]:
        logger.info("Cache warm skipped — market closed.")
        return
    requested = {
        (cfg.name, tf)
        for account_id in _load()
        for cfg in [get_user_mode_config(account_id)]
        for tf in cfg.scan_timeframes
    }
    if not requested:
        requested = {("intraday", "H1")}
    logger.info("Warming account-configured analysis cache: %s", sorted(requested))
    for mode, tf in sorted(requested):
        await warm([tf]) if mode == "intraday" else await warm([tf])


async def _refresh_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the union of subscribed accounts' analysis sets."""
    from src.market_hours import market_status
    from src.analysis.cache import get_analysis
    from src.alerts import _load
    from src.user_preferences import get_mode_config as get_user_mode_config
    import asyncio
    if not market_status()["is_open"]:
        return
    try:
        requested = {
            (cfg.name, tf)
            for account_id in _load()
            for cfg in [get_user_mode_config(account_id)]
            for tf in cfg.scan_timeframes
        }
        if not requested:
            return
        results = await asyncio.gather(
            *[
                get_analysis(tf, max_age=0, mode=mode)
                for mode, tf in sorted(requested)
            ],
            return_exceptions=True,
        )
        summary = ", ".join(
            f"{mode}/{tf}:{result.action}/{result.confidence}%"
            for (mode, tf), result in zip(sorted(requested), results)
            if not isinstance(result, Exception)
        )
        logger.info(f"Account-configured cache refreshed — {summary}")
    except Exception as e:
        logger.warning(f"Cache refresh failed: {e}")


BOT_COMMANDS = [
    BotCommand("start",     "Open the bot and register for alerts"),
    BotCommand("alerts",    "Toggle automatic signal notifications"),
    BotCommand("recommend", "Full analysis with entry, SL, and targets"),
    BotCommand("active",    "View open trades with live P&L"),
    BotCommand("signal",    "Current BUY / SELL / WAIT signal"),
    BotCommand("analyze",   "Detailed market analysis"),
    BotCommand("trend",     "Trend direction and momentum"),
    BotCommand("levels",    "Key support and resistance levels"),
    BotCommand("outlook",   "Market outlook report"),
    BotCommand("chart",     "Send a chart image for AI analysis"),
    BotCommand("history",   "Recent closed trade results"),
    BotCommand("news",      "Latest gold market headlines"),
    BotCommand("settings",  "Change mode and analysis timeframe"),
    BotCommand("mode",      "Switch Scalp, Intraday, Swing, or Position mode"),
    BotCommand("help",      "Show all commands"),
]


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled exceptions from handlers and jobs so they are never silently swallowed."""
    logger.error("Unhandled exception in bot handler/job", exc_info=context.error)


async def _set_commands(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot commands registered with Telegram.")


def main() -> None:
    # ── Key validation ─────────────────────────────────────────────────────────
    # Runs BEFORE the bot starts polling. Exits immediately if the Telegram
    # token is missing or invalid. Warns (but continues) for GOOGLE_AI_KEY.
    validate_keys_on_startup(TELEGRAM_BOT_TOKEN, GOOGLE_AI_KEY)

    logger.info("Starting XAU/USD Gold Analysis Bot...")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_set_commands)
        .build()
    )

    # Global error handler — logs all uncaught exceptions from handlers/jobs
    app.add_error_handler(_error_handler)

    # Access gate — runs before every handler in group -1
    app.add_handler(TypeHandler(Update, _access_gate), group=-1)

    register_command_handlers(app)
    register_callback_handlers(app)
    register_photo_handlers(app)
    register_message_handlers(app)

    # ── Jobs ──────────────────────────────────────────────────────────────────

    # One-time cache warm shortly after startup
    app.job_queue.run_once(_warm_cache, when=15, name="cache_warm")

    # One-time startup summary — sent 30s after boot so cache is warm
    app.job_queue.run_once(send_startup_summary, when=30, name="startup_summary")

    # Recurring background cache refresh (keeps commands fast)
    app.job_queue.run_repeating(
        _refresh_cache,
        interval=CACHE_REFRESH_SECONDS,
        first=20,
        name="cache_refresh",
    )

    # Alert scanner (BUY/SELL broadcast + trade TP/SL check)
    app.job_queue.run_repeating(
        check_and_alert,
        interval=ALERT_INTERVAL_SECONDS,
        first=25,
        name="alert_scanner",
    )

    # Market conditions summary — broadcast every 4 hours during market hours
    # first=4*3600 so it never fires on startup/restart, only on schedule
    app.job_queue.run_repeating(
        send_market_conditions_summary,
        interval=4 * 3600,
        first=4 * 3600,
        name="market_conditions",
    )

    # Missed-alert reminder — check every 10 minutes for open trades still near entry
    app.job_queue.run_repeating(
        send_trade_reminder_for_accounts,
        interval=10 * 60,
        first=10 * 60,
        name="trade_reminder",
    )

    # API key health check — runs every 6 hours, sends Telegram warning if a
    # key has expired or been rotated without updating the secret.
    app.job_queue.run_repeating(
        periodic_key_health_check,
        interval=6 * 3600,
        first=6 * 3600,   # first check after 6 hours (startup already validated)
        name="key_health",
    )

    logger.info(
        f"Jobs scheduled — cache warm: 15s | "
        f"cache refresh: {CACHE_REFRESH_SECONDS}s | "
        f"alert scan: {ALERT_INTERVAL_SECONDS}s | "
        f"market conditions: 4h | key health: 6h"
    )

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
