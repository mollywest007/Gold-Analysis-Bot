# Gold Analysis Bot

A Telegram bot that delivers real-time XAU/USD (Gold) trading signals and market analysis, powered by Google AI / Gemini.

## How to run

The bot runs via the **Gold Analysis Bot** workflow:
```
cd artifacts/gold-bot && uv run --with-requirements requirements.txt python main.py
```

## Required secrets

Set these in Replit Secrets before starting:

| Secret | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `GOOGLE_AI_KEY` | Google AI / Gemini API key |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID (get it from @userinfobot) |

`ALLOWED_USERNAME` can be used as a fallback if `ALLOWED_USER_ID` is not set, but the numeric ID is preferred (more secure).

## Project structure

```
artifacts/gold-bot/       — Python Telegram bot
  main.py                 — Entry point
  requirements.txt        — Python dependencies
  src/
    config.py             — Environment config
    alerts.py             — Signal alerts & scheduled jobs
    analysis/             — Market data, technical engine, cache
    handlers/             — Telegram command/callback/message/photo handlers
    chart_generator.py    — Chart rendering
    chart_annotator.py    — Chart annotation
    trade_tracker.py      — Trade tracking
    news.py               — News integration
    market_hours.py       — Market session logic
    key_health.py         — API key validation
  data/                   — Persistent JSON state (subscribers, trades, signals)

artifacts/api-server/     — TypeScript/Express API server (companion service)
lib/                      — Shared TypeScript libraries (api-spec, api-zod, api-client-react)
```

## User preferences

- Keep HTTP client request logging below INFO level (httpx/httpcore set to WARNING) to avoid leaking bot token in logs.
