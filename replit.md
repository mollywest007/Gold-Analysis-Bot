# XAU/USD Gold Analysis Telegram Bot

A professional Telegram bot that delivers institutional-grade XAU/USD market analysis, trade signals, trend detection, and support/resistance levels — for analysis only, no trade execution.

## Run & Operate

- `Gold Analysis Bot` workflow — runs `cd artifacts/gold-bot && python main.py`
- Required secret: `TELEGRAM_BOT_TOKEN` — set in Replit Secrets
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000, unused by bot)
- `pnpm run typecheck` — full typecheck across all packages

## Stack

- Python 3.11 + python-telegram-bot 20.7 (async polling)
- aiohttp for live price fetching (Yahoo Finance fallback to simulation)
- python-dotenv for env loading
- pnpm workspaces, Node.js 24, TypeScript 5.9 (existing monorepo infra)

## Where things live

- `artifacts/gold-bot/main.py` — bot entry point
- `artifacts/gold-bot/src/config.py` — constants and env loading
- `artifacts/gold-bot/src/analysis/engine.py` — market analysis engine (bias, levels, signals)
- `artifacts/gold-bot/src/analysis/market_data.py` — live price fetcher (Yahoo Finance) with simulation fallback
- `artifacts/gold-bot/src/utils/formatting.py` — all message card formatters
- `artifacts/gold-bot/src/utils/keyboards.py` — Telegram keyboard layouts
- `artifacts/gold-bot/src/handlers/commands.py` — /start /analyze /signal /trend /levels /outlook /settings /help
- `artifacts/gold-bot/src/handlers/callbacks.py` — inline button callbacks
- `artifacts/gold-bot/src/handlers/messages.py` — reply keyboard (text button) handlers

## Architecture decisions

- Analysis engine uses deterministic oscillator math seeded on price + timeframe + time bucket, so results are stable per timeframe window and change naturally as price moves.
- Live price fetched from Yahoo Finance (GC=F futures); falls back to a time-seeded simulation if the fetch fails — bot never crashes due to price feed.
- Signal gating: BUY/SELL only emitted when confidence ≥ 75% AND R:R ≥ 1:2 AND bias is not Neutral; otherwise WAIT with a reason.
- Timeframe preference stored in `context.user_data` per user session (no DB needed for MVP).
- All output uses `<pre>` HTML-formatted monospace cards for a trading-terminal aesthetic.

## Product

Users message the bot on Telegram and receive:
- Full XAU/USD market analysis (bias, trend, entry, SL, TP1, TP2, R:R, confidence)
- Trade signals with gating rules (BUY/SELL only when conditions are met, else WAIT)
- Trend direction and momentum summary
- Key support and resistance levels
- Market outlook report
- Configurable timeframe (M5 through D1) per user

## User preferences

- No emojis anywhere in the UI
- Mobile-first, minimal, premium trading-terminal appearance
- Clean monospace card layout with separator lines

## Access control

The bot is private — only the owner can use it. Auth priority:

1. **Numeric user ID (preferred):** Set `ALLOWED_USER_ID` in Replit Secrets. Your numeric ID appears in the bot logs after your first `/start` — look for `Authorized: @username (id=XXXXXXX)`. Numeric IDs are immutable and can't be reclaimed.
2. **Username fallback:** `ALLOWED_USERNAME` env var (defaults to the config value). Only active when `ALLOWED_USER_ID` is not set. Less secure — Telegram usernames are mutable.

## Gotchas

- Restart the `Gold Analysis Bot` workflow after any code change in `artifacts/gold-bot/`
- The bot uses long-polling (not webhook) — only one instance should run at a time
- `TELEGRAM_BOT_TOKEN` and `GOOGLE_AI_KEY` must be set in Replit Secrets before starting the workflow

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
