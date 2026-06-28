import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from telegram.ext import Application, ContextTypes
from src.config import TELEGRAM_BOT_TOKEN
from src.handlers import (
    register_command_handlers,
    register_callback_handlers,
    register_message_handlers,
    register_photo_handlers,
)
from src.alerts import check_and_alert

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


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)

    logger.info("Starting XAU/USD Gold Analysis Bot...")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
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

    logger.info(
        f"Jobs scheduled — cache warm: 15s | "
        f"cache refresh: {CACHE_REFRESH_SECONDS}s | "
        f"alert scan: {ALERT_INTERVAL_SECONDS}s"
    )

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
