import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GOOGLE_AI_KEY      = os.environ.get("GOOGLE_AI_KEY", "")

DEFAULT_TIMEFRAME = "H1"
VALID_TIMEFRAMES = ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
CONFIDENCE_THRESHOLD = 75
MIN_RR_RATIO = 2.0

GOLD_SYMBOL = "XAU/USD"

# Access control — only explicitly allowed accounts may use this bot.
#
# BOT_OWNER_TELEGRAM_ID (preferred, non-secret): set this to the owner's
# numeric Telegram user ID. This is intentionally a normal environment value:
# a Telegram user ID is an identifier, not a credential, and keeping the
# owner setting out of the secret-only path makes workflow reloads reliable.
#
# ALLOWED_USER_ID (legacy secret): set this to your numeric Telegram
#   user ID. Find it in the bot logs after your first /start — look for
#   "Authorized: @username (id=XXXXXXX)". Set ALLOWED_USER_ID=XXXXXXX as a
#   Replit secret if the non-secret setting is not available.
#
# ALLOWED_USERNAME (fallback): legacy single-username setting.
# ALLOWED_USERNAMES: comma-separated usernames allowed when no numeric owner ID
#   is configured. Telegram usernames are mutable and can be reclaimed.
_raw_user_id = (
    os.environ.get("BOT_OWNER_TELEGRAM_ID", "").strip()
    or os.environ.get("ALLOWED_USER_ID", "").strip()
)
ALLOWED_USER_ID: int = int(_raw_user_id) if _raw_user_id.isdigit() else 0
ALLOWED_USERNAME = os.environ.get("ALLOWED_USERNAME", "senpaipl9")
ALLOWED_USERNAMES = {
    username.strip().lstrip("@").lower()
    for username in os.environ.get(
        "ALLOWED_USERNAMES",
        ALLOWED_USERNAME,
    ).split(",")
    if username.strip()
}
