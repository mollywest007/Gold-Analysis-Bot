# Gold Analysis Bot

A private Telegram bot that delivers XAU/USD (Gold) trading analysis, market alerts, and chart signals to a single authorized user.

## How to run

Start the **Gold Analysis Bot** workflow. It runs:

```
cd artifacts/gold-bot && ../../.pythonlibs/bin/python main.py
```

To reinstall Python dependencies:

```
uv sync
```

## Required secrets

| Secret | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `GOOGLE_AI_KEY` | Gemini API key from [aistudio.google.com](https://aistudio.google.com/app/apikey) — used for chart image analysis |

## Access control

Only one user may interact with the bot. Set in environment variables:

- `ALLOWED_USER_ID` — numeric Telegram user ID (preferred, immutable)
- `ALLOWED_USERNAME` — Telegram username fallback (less secure)

## Scheduled jobs

| Job | Interval | Purpose |
|---|---|---|
| `cache_warm` | 15 s after start | Pre-fetches market data |
| `cache_refresh` | Every 60 s | Keeps analysis fresh |
| `alert_scanner` | Every 15 s | Checks for trade entry signals |
| `market_conditions` | Every 4 h | Sends market summary |
| `trade_reminder` | Every 10 min | Trade management reminders |
| `key_health` | Every 6 h | Validates API keys |

## Project structure

```
artifacts/gold-bot/
  main.py              # Entry point, scheduler setup
  requirements.txt
  src/
    config.py          # Env var loading, constants
    handlers/          # Telegram command/message/callback handlers
    analysis/          # Market analysis logic
    alerts.py          # Alert scanning and notifications
    chart_generator.py # Chart image generation
    chart_analysis.py  # Gemini-powered chart analysis
    trade_tracker.py   # Trade tracking state
    news.py            # News fetching
```

## Stack

- Python 3.13, python-telegram-bot 20.7
- OpenAI / Gemini (GOOGLE_AI_KEY) for chart analysis
- matplotlib + mplfinance for chart generation
- aiohttp for market data fetching

## User preferences

- Keep the existing project structure and stack.

## Pointers

- See the `pnpm-workspace` skill for workspace structure details.
