# Gold Analysis Bot

A Telegram bot that monitors XAU/USD (gold) across multiple timeframes and sends trade entry alerts and market condition summaries to a configured Telegram user.

## How to run

The **Gold Analysis Bot** workflow starts automatically. It runs:
```
cd artifacts/gold-bot && .venv/bin/python main.py
```

## Required secrets

| Secret | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather ✅ |
| `GOOGLE_AI_KEY` | Google AI Studio key for chart image analysis (get one free at aistudio.google.com/app/apikey) |

## Access control

Only the configured owner can use the bot. Set in `.replit` under `[userenv.shared]`:
- `ALLOWED_USER_ID` — numeric Telegram user ID (preferred, immutable)
- `ALLOWED_USERNAME` — Telegram username fallback

## Project structure

```
artifacts/gold-bot/       # Main Telegram bot (Python)
  main.py                 # Entry point — scheduler + bot setup
  src/
    config.py             # Env vars and constants
    handlers/             # Telegram command/message handlers
    analysis/             # Market data fetching + signal logic
    alerts.py             # Alert scanning and dispatch
    chart_generator.py    # Chart image generation
    chart_analysis.py     # Google AI chart analysis
    trade_tracker.py      # Open trade persistence
  data/                   # Runtime state (subscribers, signals)
  requirements.txt

artifacts/api-server/     # TypeScript/Express API server
lib/                      # Shared packages (db, api-spec, api-zod, api-client-react)
```

## User preferences

- Keep existing project structure — do not restructure or migrate
