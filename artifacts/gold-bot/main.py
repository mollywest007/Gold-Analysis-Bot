import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from telegram.ext import Application
from src.config import TELEGRAM_BOT_TOKEN
from src.handlers import (
    register_command_handlers,
    register_callback_handlers,
    register_message_handlers,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        sys.exit(1)

    logger.info("Starting XAU/USD Gold Analysis Bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    register_command_handlers(app)
    register_callback_handlers(app)
    register_message_handlers(app)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
