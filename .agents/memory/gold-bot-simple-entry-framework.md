---
name: Gold Bot Adaptive Entry Strategy
description: The shared mode-scaled XAU/USD breakout/pullback rules and backtest evidence boundary.
---

Production XAU/USD analysis uses one configurable weighted multi-timeframe engine with distinct Scalp, Intraday, Swing, and Position profiles. Each profile defines context, setup, and optional refinement frames, thresholds, holding scope, and volatility/risk filters; an explicitly selected chart timeframe adapts the role frames without changing the selected mode. Setups include pullbacks, sweeps, breakouts, retests, and support/resistance reactions. EMA, RSI, and MACD are supporting context rather than mandatory gates; missing evidence earns no points. A score never overrides stale or incomplete data, an invalid structural stop, an obstructed target, or the configured minimum R:R. Combined mode remains an independent Scalp/Intraday coordinator, and invalid explicit modes must fail rather than inherit another profile.

**Why:** The user replaced the earlier selected-timeframe-only strategy with four distinct multi-timeframe profiles and requested early-to-moderate setups without requiring every indicator to agree. Risk/target levels must stay tied to observed structure and mode/timeframe volatility.

**How to apply:** Resolve the active mode and its frame roles before analysis; do not collapse the four modes into one timeframe or one threshold. Preserve the existing UI, selectable modes, combined streams, and non-analysis bot features. Keep scores and category thresholds rule-based and configurable, prevent correlated indicators from counting as separate confirmations, and fail closed on incomplete or stale data. Do not invent market prices, stops, targets, or execution costs. Do not describe starting thresholds as proven profitable.

Once the mode/timeframe strategy reports a valid entry, do not veto it again with generic win-probability, ADX, or ChoCH requirements. Keep market-data validation, structural risk checks, deduplication, and active-trade guards.

**Why:** The user explicitly requires the shared price-action rules to decide entries and does not want extra indicators to suppress an otherwise valid setup.

**How to apply:** Do not route valid shared-engine entries through generic confluence or win-probability gates; preserve the rule-based setup grade in the analysis/report.

## Backtest evidence boundary

Existing trade summaries without historical OHLCV cannot replay this strategy.

**Why:** The user explicitly said not to claim this strategy is profitable or superior without evidence; old entry/exit summaries are not a strategy backtest.

**How to apply:** Only report backtest metrics from suitable historical candle data and at least 50 replayable closed outcomes. If candles are missing, label the metrics unavailable instead of substituting old trade outcomes.

Historical replay reports first-touch outcomes, not broker-exact or net P&L. It fills at the next candle open only when the planned entry is still actionable, and counts SL first if SL and TP1 both touch within one OHLC candle.

**Why:** The available feed has no historical bid/ask spread, slippage, or configured partial-position sizes, so a dollar return or net R result would imply unsupported execution precision.

**How to apply:** Label TP1-first and SL-first rates as first-touch metrics, disclose excluded costs and partial sizing, and do not infer net profitability until historical execution data and sizing rules are defined.

## Live reference-spread screen

A fresh validated XAU/USD bid/ask spread may be used for a configurable mode-specific spread/ATR screen and conservative reference-spread R:R check. It is not a guarantee of broker execution. If the quote is unavailable, state that the spread screen was unavailable; never infer spread, commission, or slippage from OHLC candles.

**Why:** The bot's public spot quote is a market reference, not a connected brokerage feed, while historical OHLCV contains no bid/ask.

**How to apply:** Keep broker-specific execution costs explicitly unavailable until a broker feed supplies them, and label historical replays as not testing the live spread screen.