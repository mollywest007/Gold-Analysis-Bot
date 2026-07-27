import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from telegram import BotCommand, Update
from telegram.ext import Application, ContextTypes, TypeHandler
from telegram.ext import ApplicationHandlerStop
from src.config import TELEGRAM_BOT_TOKEN, GOOGLE_AI_KEY, ALLOWED_USER_ID, ALLOWED_USERNAME
from src.key_health import validate_keys_on_startup, periodic_key_health_check
from src.handlers import (
    register_command_handlers,
    register_callback_handlers,
    register_message_handlers,
    register_photo_handlers,
)
from src.alerts import check_and_alert, send_market_conditions_summary, send_startup_summary, send_trade_reminder

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

ALERT_INTERVAL_SECONDS  = 15    # 15 seconds — catch entries before the move extends
CACHE_REFRESH_SECONDS   = 60    # 1 minute — keeps analysis fresh


async def _access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block all users except the configured owner. Raises ApplicationHandlerStop for strangers."""
    user = update.effective_user
    if user is None:
        raise ApplicationHandlerStop

    logger.info(f"User: @{user.username} (id={user.id})")

    # Determine authorization
    if ALLOWED_USER_ID:
        authorized = (user.id == ALLOWED_USER_ID)
    elif ALLOWED_USERNAME:
        authorized = (user.username == ALLOWED_USERNAME)
    else:
        authorized = True  # No restriction configured — open access

    if authorized:
        return

    # Reject the stranger
    logger.warning(f"Unauthorized: @{user.username} (id={user.id})")
    if update.message:
        await update.message.reply_text("⛔ Unauthorized.")
    elif update.callback_query:
        await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
    raise ApplicationHandlerStop


async def _warm_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-fetch M15 + H1 analysis 15 s after startup so first request is instant."""
    from src.market_hours import market_status
    from src.analysis.cache import warm
    if not market_status()["is_open"]:
        logger.info("Cache warm skipped — market closed.")
        return
    await warm(["M15", "H1"])


async def _refresh_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh M15 + H1 cache every minute while market is open."""
    from src.market_hours import market_status
    from src.analysis.cache import get_analysis
    import asyncio
    if not market_status()["is_open"]:
        return
    try:
        m15, h1 = await asyncio.gather(
            get_analysis("M15", max_age=0),
            get_analysis("H1",  max_age=0),
        )
        logger.info(
            f"Cache refreshed — M15:{m15.action}/{m15.confidence}% "
            f"H1:{h1.action}/{h1.confidence}% adx={h1.adx:.1f}"
        )
    except Exception as e:
        logger.warning(f"Cache refresh failed: {e}")


BOT_COMMANDS = [
    BotCommand("start",     "Open the bot and register for alerts"),
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
    BotCommand("settings",  "Change your analysis timeframe"),
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
        send_trade_reminder,
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
