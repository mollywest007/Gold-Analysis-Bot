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

The active XAU/USD analysis engine uses weighted context/setup/refinement
timeframes, selected from the active mode and the user's setup timeframe.
Its six configurable score groups are market structure (25), entry location
(20), price action (15), trend/momentum (15), liquidity/breakout/retest (15),
and volatility/execution (10). EMA20/50, RSI14, MACD, and ATR14 support the
evidence; none is a mandatory standalone gate. Missing or unverified data
earns no points.

The engine recognizes pullbacks, liquidity sweeps, breakouts, breakout-retests,
and support/resistance reactions. Stops use structural invalidation plus the
mode/timeframe ATR buffer. Targets use the nearest opposing confirmed structure
after a mode-specific R:R and available-spread check. Preferred R:R is an aim,
not a ceiling: a farther TP1 is allowed when it is the nearest confirmed
structure, and insufficient room is rejected rather than forced.
Reject invalid stops, obstructed or undersized targets, stale data, material
conflict, excessive volatility, and missed entries. `EARLY` is provisional;
only `MODERATE`/`STRONG` produces BUY/SELL. Otherwise remain `WAIT`.

Modes retain their existing timeframe scope and holding windows:

| Mode | Selectable timeframes | Typical intent |
|---|---|---|
| Scalp | M1, M3, M5, M15, M30, H1 | Short-term momentum and local structure |
| Intraday | M15, M30, H1 | Session/day moves |
| Swing | H1, H4, D1 | Multi-day moves and wider structure |
| Position | D1, W1, MN1 | Longer-horizon structure |

Each mode/timeframe resolves its own pivot scale, lookback, pullback depth,
extension limit, ATR stop buffer/cap, target room, and expected holding bars.
Each mode also configures score/category thresholds, timeframe weights,
breakout/sweep lookbacks, volatility and spread screens, minimum/preferred
R:R, and holding constraints. Combined mode remains a coordinator that resolves
the Scalp and Intraday alert streams independently. Invalid explicit
modes/timeframes fail rather than silently inheriting a different profile.

TP/SL settings can be overridden independently by mode with environment
variables. Use `XAU_SCALP_`, `XAU_INTRADAY_`, `XAU_SWING_`, or
`XAU_POSITION_` followed by `ATR_PERIOD`, `STOP_BUFFER_ATR`,
`MINIMUM_STOP_ATR`, `MAXIMUM_STOP_ATR`, `MINIMUM_TARGET_RR`, or
`PREFERRED_TARGET_RR`. Defaults enforce at least 1.5R; the Scalp minimum is
1.5R and its preferred target is 2R. Overrides must be positive, finite, and
keep preferred R:R at or above the minimum. Scalp's base stop floor is 0.70 ATR;
timeframe scaling and actual structural invalidation may require more room.
No dollar TP/SL amount is hardcoded.

Global account-risk limits remain controlled by `XAU_RISK_PER_TRADE_FRACTION`,
`XAU_MAX_SUGGESTED_RISK_FRACTION`, and `XAU_MAX_TOTAL_RISK_FRACTION`. A mode can
use its own risk fraction with, for example,
`XAU_SCALP_RISK_PER_TRADE_FRACTION`; it cannot exceed the global suggested-risk
cap. Position sizing and net dollar P&L require actual account equity, broker
contract size, lot constraints, commission, and slippage. Until those values
are supplied, alerts report dollar P&L as unavailable rather than treating a
price move as a dollar return. Leverage affects margin, not price-based P&L.

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

The active decision uses its configured context, setup, and optional
refinement timeframes. Only completed, fresh, spot-normalized candles from the
required context and setup roles can support an actionable signal. Missing,
stale, simulated, or unverified data must not create positive evidence.
Legacy report fields remain for client compatibility; they must not create
synthetic macro, intermarket, or institutional conviction.

Optional refinement data can add context only when validated; otherwise it is
treated as missing. The Scalp/Intraday coordinator keeps separate alert
streams and applies each stream's own mode profile.
