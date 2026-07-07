import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

DEFAULT_TIMEFRAME = "H1"
VALID_TIMEFRAMES = ["M5", "M15", "M30", "H1", "H4", "D1"]
CONFIDENCE_THRESHOLD = 75
MIN_RR_RATIO = 2.0

GOLD_SYMBOL = "XAU/USD"

# Access control — only the owner may use this bot.
#
# ALLOWED_USER_ID (preferred, immutable): set this to your numeric Telegram
#   user ID. Find it in the bot logs after your first /start — look for
#   "Authorized: @username (id=XXXXXXX)". Set ALLOWED_USER_ID=XXXXXXX as a
#   Replit secret to enable ID-based auth (more secure than username).
#
# ALLOWED_USERNAME (fallback): used only when ALLOWED_USER_ID is not set.
#   Telegram usernames are mutable and can be reclaimed — set the numeric ID
#   as soon as possible.
_raw_user_id = os.environ.get("ALLOWED_USER_ID", "").strip()
ALLOWED_USER_ID: int = int(_raw_user_id) if _raw_user_id.isdigit() else 0
ALLOWED_USERNAME = os.environ.get("ALLOWED_USERNAME", "nailythachad")
