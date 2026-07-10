# XAU/USD Gold Analysis Bot

A Telegram bot that delivers real-time gold trading signals, AI-powered chart analysis, and automated alerts for XAU/USD.

## Stack

- **Language:** Python 3.11
- **Framework:** python-telegram-bot 20.7 (async, job-queue)
- **AI:** Google AI (Gemini) via `GOOGLE_AI_KEY` — used for chart image analysis
- **Data:** Live gold price fetched via aiohttp; analysis cached in-memory

## How to run

The bot starts automatically via the **Gold Analysis Bot** workflow:

```
cd artifacts/gold-bot && python main.py
```

## Required secrets

| Secret | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather — required |
| `GOOGLE_AI_KEY` | Google AI Studio key — needed for `/chart` AI analysis |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID — preferred auth method |
| `ALLOWED_USERNAME` | Your Telegram username — fallback if `ALLOWED_USER_ID` not set |

`ALLOWED_USERNAME` defaults to `nailythachad`. Set `ALLOWED_USER_ID` as soon as possible (find it in bot logs after first `/start`) — numeric IDs are immutable, usernames are not.

## Project layout

```
artifacts/gold-bot/
  main.py              # Entry point, job scheduling, access gate
  src/
    config.py          # Env-var config and auth settings
    alerts.py          # Alert scanner and subscriber broadcast
    analysis/
      engine.py        # Core technical analysis (trend, RSI, ADX, S/R)
      cache.py         # In-memory analysis cache
      market_data.py   # Live price fetching
    handlers/          # Telegram command/callback/message handlers
    chart_analysis.py  # Google AI chart image analysis
    trade_tracker.py   # Open/closed trade state (file-backed JSON)
    market_hours.py    # Forex market open/close detection
  data/                # Persistent JSON state files
```

## User preferences

- Keep project structure as-is; do not migrate or restructure without explicit request.
