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

## Entry strategy

All four trade modes use one selected-timeframe price-action engine. It can
identify a contained momentum breakout through a confirmed swing level or a
pullback continuation at a meaningful level. Direction comes from local
HH/HL or LH/LL structure. Normally, direction plus a valid entry location is
enough for an early, lower-grade setup; a rejection candle or minor structure
break adds confirmation and improves the rule-based grade. RSI14 is optional
momentum context and only a strong conflict blocks an otherwise valid setup.
EMA20/EMA50 are context, never standalone direction gates.

Stops sit beyond the setup's structural invalidation level with a
mode/timeframe-specific ATR14 buffer. Reject stops outside the configured
volatility range. TP1 must be a real opposing structural level with at least
1.5R of clear room; further targets are optional and cannot be invented.
Avoid extended breakouts, chop, and obstructed targets. A setup without a
defensible entry, stop, or target remains `WAIT`/`DEVELOPING` rather than
receiving fabricated prices.

Modes retain their existing timeframe scope and holding windows:

| Mode | Selectable timeframes | Typical intent |
|---|---|---|
| Scalp | M1, M3, M5, M15, M30, H1 | Short-term momentum and local structure |
| Intraday | M15, M30, H1 | Session/day moves |
| Swing | H1, H4, D1 | Multi-day moves and wider structure |
| Position | D1, W1, MN1 | Longer-horizon structure |

Each mode/timeframe resolves its own pivot scale, lookback, pullback depth,
extension limit, ATR stop buffer/cap, target room, and expected holding bars.
Combined mode resolves the Scalp and Intraday alert streams independently.
Invalid explicit modes/timeframes must fail rather than silently inherit a
different profile. No higher-timeframe confirmation is required.

The bot sends analysis alerts and tracks plans; it does not connect to a broker
or submit orders. It therefore cannot determine broker-specific contract/tick
specifications, position size, account equity, commission, or slippage. Do not
present its alerts as broker-sized orders or claim account-wide daily-loss/open-
risk enforcement.

Historical replay uses real completed candles, but the available history is a
futures proxy normalized to spot—not executable broker XAU/USD data. The feed
does not contain historical bid/ask spread, commissions, slippage, or lot size.
Replay metrics are first-touch estimates before costs, not net P&L; forward
testing and old-strategy comparisons require suitable execution-cost data.

The saved trade history does not include the OHLCV candles needed to replay this
strategy. Do not present the old trade summaries as a backtest of the new
strategy, and do not claim profitability or superiority without suitable
historical evidence.

## User preferences

- Keep HTTP client request logging below INFO level (httpx/httpcore set to WARNING) to avoid leaking bot token from query params in logs.

## Analysis data boundary

The bot analyzes only the timeframe selected in the active mode/settings for
each entry decision. It does not wait for, require, or use higher- or
lower-timeframe confirmation. Legacy report fields remain for client
compatibility; they must not create synthetic macro, intermarket, or
institutional conviction.

Only real OHLCV data can create an actionable entry. Simulated candles are
diagnostic only. Other timeframe reports may be requested for explicit
diagnostics but never gate a normal entry.
