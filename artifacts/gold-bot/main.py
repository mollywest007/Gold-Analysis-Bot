import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from telegram import BotCommand
from telegram.ext import Application, ContextTypes
from src.config import TELEGRAM_BOT_TOKEN
from src.handlers import (
    register_command_handlers,
    register_callback_handlers,
    register_message_handlers,
    register_photo_handlers,
)
from src.alerts import check_and_alert, send_market_conditions_summary

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

ALERT_INTERVAL_SECONDS  = 60    # 1 minute — fast alert scanner
CACHE_REFRESH_SECONDS   = 60    # 1 minute — keeps analysis fresh


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


async def _set_commands(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot commands registered with Telegram.")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)

    logger.info("Starting XAU/USD Gold Analysis Bot...")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_set_commands)
        .build()
    )

    register_command_handlers(app)
    register_callback_handlers(app)
    register_photo_handlers(app)
    register_message_handlers(app)

    # ── Jobs ──────────────────────────────────────────────────────────────────

    # One-time cache warm shortly after startup
    app.job_queue.run_once(_warm_cache, when=15, name="cache_warm")

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

    logger.info(
        f"Jobs scheduled — cache warm: 15s | "
        f"cache refresh: {CACHE_REFRESH_SECONDS}s | "
        f"alert scan: {ALERT_INTERVAL_SECONDS}s | "
        f"market conditions: 4h"
    )

    logger.info("Bot is running. Press Ctrl+C to stop.")

    from telegram.error import Conflict
    import time as _time
    max_attempts = 6
    for attempt in range(max_attempts):
        try:
            app.run_polling(drop_pending_updates=True)
            break
        except Conflict:
            if attempt < max_attempts - 1:
                wait = 5 * (attempt + 1)
                logger.warning(
                    f"Startup conflict with Telegram (attempt {attempt + 1}/{max_attempts}) "
                    f"— waiting {wait}s for old session to expire..."
                )
                _time.sleep(wait)
            else:
                logger.error("Could not resolve Telegram polling conflict after all retries.")
                raise


if __name__ == "__main__":
    main()
