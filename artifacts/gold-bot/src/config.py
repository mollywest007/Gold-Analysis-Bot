import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

DEFAULT_TIMEFRAME = "H1"
VALID_TIMEFRAMES = ["M5", "M15", "M30", "H1", "H4", "D1"]
CONFIDENCE_THRESHOLD = 75
MIN_RR_RATIO = 2.0

GOLD_SYMBOL = "XAU/USD"
