# Gold Analysis Bot

A Telegram bot for XAU/USD (gold) trading analysis. It monitors market conditions, scans for trade setups, sends alerts, generates annotated charts, and tracks active trades — all delivered via Telegram.

## How to run

The bot runs via the **Gold Analysis Bot** workflow:
```
cd artifacts/gold-bot && uv run --with-requirements requirements.txt python main.py
```

Only one instance can run at a time (Telegram long-polling limitation).

## Required secrets

| Secret | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather on Telegram |
| `GOOGLE_AI_KEY` | Google AI (Gemini) API key |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID (preferred, more secure) |
| `ALLOWED_USERNAME` | Fallback if `ALLOWED_USER_ID` is not set (defaults to `nailythachad`) |

To find your `ALLOWED_USER_ID`: send `/start` to the bot and check the workflow logs — it prints `User: @username (id=XXXXXXX)`.

## Stack

- Python 3.11 via `uv`
- `python-telegram-bot` 20.7 (long polling + job queue)
- Google Gemini API for AI-powered analysis
- APScheduler for periodic jobs (alerts every 15s, cache refresh every 60s, market summary every 4h)

## User preferences

- Keep HTTP client request logging below INFO level (httpx/httpcore set to WARNING) to avoid leaking bot token from query params in logs.
