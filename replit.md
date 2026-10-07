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

Production entries use the selected chart only. The bot requires clear local
HH/HL or LH/LL swings, a controlled pullback near EMA20, the protected swing,
or a prior breakout/breakdown level, and a rejection candle followed by a
close beyond the minor pullback swing. RSI is a momentum conflict filter;
EMA20/EMA50 alignment does not set the trade direction, and higher-timeframe
confirmation is not required.

The bot skips choppy conditions, extended breaks, structural stops wider than
2.5 ATR, and setups without at least 1R of room to an actual opposing swing.
Stops are placed beyond the pullback extreme with an ATR noise buffer.
Targets come only from nearby opposing swing/liquidity pivots; TP2/TP3 are
optional, and one credible TP1 is tracked as the final target. A provisional
or incomplete setup must not receive invented stop or target levels.

Analysis statuses remain `WAIT`, `DEVELOPING`, `MODERATE ENTRY`, `MISSED`, or
`INVALID`. Alerts must distinguish a confirmed entry from an incomplete setup.

## Mode- and timeframe-aware execution

The shared Momentum-Pullback sequence stays direction → pullback → rejection →
structure confirmation → entry, but its structural pivot radius, pullback
window/depth, breakout chase limit, confirmation displacement, structural stop
buffer/risk ceiling, and target clearance adapt to both the active mode and
selected timeframe. Measurements remain ATR-relative; no fixed pip or point
distances are shared across charts. EMA20 is pullback context, not a direction
gate. RSI14 is a soft preference (BUY above 45 and rising; SELL below 55 and
falling) and only strongly opposing RSI blocks an otherwise valid structure.

The momentum-pullback sequence is shared, but its thresholds and risk rules are
selected from the user's active mode before analysis begins. Scalp, Intraday,
Swing, and Position each keep their own sensitivity, pullback window, rejection
strictness, ATR limits, structural target requirements, and response timeframe.
Combined mode must resolve each stream to its own profile. Never fall back to a
different mode when an explicit mode is invalid, and show the active mode on
setup cards.

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
