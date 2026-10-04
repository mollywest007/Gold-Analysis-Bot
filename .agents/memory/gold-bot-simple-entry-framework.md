---
name: Gold Bot Momentum-Pullback Framework
description: The production XAU/USD selected-timeframe momentum-pullback rules and backtest evidence boundary.
---

Production XAU/USD entries use clear local HH/HL or LH/LL swing structure on the selected timeframe. Require a controlled pullback near EMA20, the protected swing, or a prior breakout/breakdown level, followed by rejection and a close through the minor pullback swing. Interpret the price-action conditions together using available candles and mode parameters; do not invent signals or loosen conditions to force trades. If structure or confirmation is unclear, return no trade. RSI is a momentum disagreement filter; EMA20/EMA50 do not define direction, and higher-timeframe confirmation is not required. Resolve the user's active mode before analysis and apply that mode's distinct sensitivity, pullback, confirmation, volatility, and structural risk/target profile to the shared sequence. Combined mode must resolve each stream independently; invalid explicit modes must fail rather than inherit another profile.

**Why:** The user requires mode-specific analysis, with the fast moderate momentum-pullback sequence as the shared core rather than one fixed strategy. Subjective concepts must be translated consistently from market data without fabricating a signal; unclear or poor setups are skipped, while unnecessary confirmation delay is avoided.

**How to apply:** Load the requested mode profile before market analysis and preserve it through UI, cache, and alert paths. Evaluate the full sequence jointly; reject chop, extension, strong RSI disagreement, stops above that mode's ATR cap, or insufficient room to a real opposing pivot under that mode's RR/room floor. Put the stop beyond the pullback extreme with that mode's ATR noise buffer. Use only actual opposing swing/liquidity pivots for targets; extra targets are optional, and a lone credible TP1 is final. Incomplete or ambiguous plans must not invent entries, stops, targets, or confirmation.

## Backtest evidence boundary

Existing trade summaries without historical OHLCV cannot replay this strategy.

**Why:** The user explicitly said not to claim this strategy is profitable or superior without evidence; old entry/exit summaries are not a strategy backtest.

**How to apply:** Only report backtest metrics from suitable historical candle data and at least 50 replayable closed outcomes. If candles are missing, label the metrics unavailable instead of substituting old trade outcomes.

Historical replay reports first-touch outcomes, not broker-exact or net P&L. It fills at the next candle open only when the planned entry is still actionable, and counts SL first if SL and TP1 both touch within one OHLC candle.

**Why:** The available feed has no historical bid/ask spread, slippage, or configured partial-position sizes, so a dollar return or net R result would imply unsupported execution precision.

**How to apply:** Label TP1-first and SL-first rates as first-touch metrics, disclose excluded costs and partial sizing, and do not infer net profitability until historical execution data and sizing rules are defined.