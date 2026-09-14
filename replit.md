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

## Institutional analysis engine

The bot analyzes only the timeframe selected in the active mode/settings for each
entry decision. It does not wait for, require, or use higher- or lower-timeframe
confirmation. Single-timeframe results include a serializable institutional report
covering market structure,
liquidity/SMC, Wyckoff, Fibonacci, volume profile, volatility, momentum, sessions,
macro-event gating, and optional intermarket confirmation.

The engine uses real OHLCV data for actionable conclusions; simulated candles are
retained for diagnostics and cannot create a final bias. Other timeframe reports
may be requested for explicit diagnostics, but never gate a normal entry.
Cross-asset data is fetched from Yahoo Finance with a short cache. An economic
calendar is intentionally not invented: to enable the imminent-event gate, set
`HIGH_IMPACT_EVENTS_UTC` to comma-separated `ISO-8601 timestamp|event name` values,
for example `2026-09-07T12:30:00Z|CPI`.
