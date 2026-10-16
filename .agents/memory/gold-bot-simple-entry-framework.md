---
name: Gold Bot Weighted Multi-Timeframe Entry Strategy
description: The active mode-configured XAU/USD analysis engine and its backtest evidence boundary.
---

The current XAU/USD engine uses mode-configured context, selected setup, and optional refinement timeframes with weighted evidence. A previous instruction removed adaptive analysis, but the user explicitly superseded it by asking to rebuild weighted multi-timeframe analysis. Preserve the four real modes, the combined Scalp/Intraday coordinator, alerts, and trade tracking.

**Why:** The user later explicitly requested the weighted multi-timeframe engine, so the earlier single-timeframe-only decision is no longer current.

**How to apply:** Keep the selected setup timeframe authoritative, derive context/refinement roles from the active mode, and ensure mode selection applies the matching score thresholds, weights, lookbacks, volatility limits, and R:R settings. Do not treat the combined coordinator as a standalone strategy.

## Backtest evidence boundary

Existing trade summaries without historical OHLCV cannot replay the active strategy.

**Why:** The user explicitly said not to claim a strategy is profitable or superior without evidence; old entry/exit summaries are not a strategy backtest.

**How to apply:** Only report backtest metrics from suitable historical candle data and at least 50 replayable closed outcomes. If candles are missing, label the metrics unavailable instead of substituting old trade outcomes.

Historical replay reports first-touch outcomes, not broker-exact or net P&L. It fills at the next candle open only when the planned entry is still actionable, and counts SL first if SL and TP1 both touch within one OHLC candle.

**Why:** The available feed has no historical bid/ask spread, slippage, or configured partial-position sizes, so a dollar return or net R result would imply unsupported execution precision.

**How to apply:** Label TP1-first and SL-first rates as first-touch metrics, disclose excluded costs and partial sizing, and do not infer net profitability until historical execution data and sizing rules are defined.

## Live reference-spread screen

A fresh validated XAU/USD bid/ask spread may be used for a configurable mode-specific spread/ATR screen and conservative reference-spread R:R check. It is not a guarantee of broker execution. If the quote is unavailable, state that the spread screen was unavailable; never infer spread, commission, or slippage from OHLC candles.

**Why:** The bot's public spot quote is a market reference, not a connected brokerage feed, while historical OHLCV contains no bid/ask.

**How to apply:** Keep broker-specific execution costs explicitly unavailable until a broker feed supplies them, and label historical replays as not testing the live spread screen.