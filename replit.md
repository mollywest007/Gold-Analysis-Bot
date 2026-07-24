# Gold Analysis Bot

A Telegram bot that delivers XAU/USD (gold) trading signals, market analysis, chart reading, and trade tracking.

## Stack

- **Runtime**: Python 3.11 (virtualenv at `artifacts/gold-bot/.venv`)
- **Bot framework**: python-telegram-bot 20.7
- **AI**: Google AI (Gemini) via `GOOGLE_AI_KEY`
- **Charts**: matplotlib / mplfinance

## How to run

The bot runs via the **Gold Analysis Bot** workflow:

```
cd artifacts/gold-bot && .venv/bin/python main.py
```

## Required secrets

| Secret | Description |
|--------|-------------|
| `TELEGRAM_BOT_TOKEN` | From @BotFather on Telegram |
| `GOOGLE_AI_KEY` | Google AI (Gemini) API key |

## Access control

`ALLOWED_USER_ID` and `ALLOWED_USERNAME` are set in `.replit` under `[userenv.shared]`. Only the configured user can interact with the bot.

## Bot commands

| Command | Description |
|---------|-------------|
| `/start` | Open the bot and register for alerts |
| `/recommend` | Full analysis with entry, SL, and targets |
| `/signal` | Current BUY / SELL / WAIT signal |
| `/analyze` | Detailed market analysis |
| `/trend` | Trend direction and momentum |
| `/levels` | Key support and resistance levels |
| `/outlook` | Market outlook report |
| `/chart` | Send a chart image for AI analysis |
| `/active` | View open trades with live P&L |
| `/history` | Recent closed trade results |
| `/news` | Latest gold market headlines |
| `/settings` | Change analysis timeframe |
| `/help` | Show all commands |

## Project structure

```
artifacts/gold-bot/
  main.py          # Entry point, job scheduling
  requirements.txt
  src/
    config.py      # Env vars / access control
    handlers/      # Telegram command handlers
    analysis/      # Market analysis engine + cache
    alerts.py      # BUY/SELL alert broadcaster
    trade_tracker.py
    news.py
    chart_*.py
```

## User preferences

- Keep the project's existing structure and stack.
