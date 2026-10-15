---
name: Gold Bot Single-Timeframe Entry Strategy
description: The retained selected-timeframe XAU/USD entry path and backtest evidence boundary.
---

Live analysis uses the selected chart timeframe and its mode-scaled MomentumPullbackProfile. The adaptive weighted multi-timeframe engine was explicitly removed; do not reintroduce it. Preserve the Scalp, Intraday, Swing, and Position modes, combined Scalp/Intraday monitoring, alerts, and trade tracking.

**Why:** The user chose to remove the adaptive strategy while retaining other bot features and the separate existing analysis path.

**How to apply:** Analyze the selected timeframe with the profile resolved for the active mode and timeframe. Keep mode switching, combined streams, and non-analysis bot features intact. Do not label the retained path as adaptive or use the removed multi-timeframe engine.

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