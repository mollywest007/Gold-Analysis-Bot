---
name: Gold Bot Momentum-Pullback Framework
description: The production XAU/USD selected-timeframe momentum-pullback rules and backtest evidence boundary.
---

Production XAU/USD entries use clear local HH/HL or LH/LL swing structure on the selected timeframe. Require a controlled pullback near EMA20, the protected swing, or a prior breakout/breakdown level, followed by rejection and a close through the minor pullback swing. RSI is a momentum disagreement filter; EMA20/EMA50 do not define direction, and higher-timeframe confirmation is not required. Resolve the user's active mode before analysis and apply that mode's distinct sensitivity, pullback, confirmation, volatility, and structural risk/target profile to the shared sequence. Combined mode must resolve each stream independently; invalid explicit modes must fail rather than inherit another profile.

**Why:** The user requires mode-specific analysis, with the fast moderate momentum-pullback sequence as the shared core rather than one fixed strategy; they also reject unnecessary delay from extra confirmation.

**How to apply:** Load the requested mode profile before market analysis and preserve it through UI, cache, and alert paths. Reject chop, extension, strong RSI disagreement, stops above that mode's ATR cap, or insufficient room to a real opposing pivot under that mode's RR/room floor. Put the stop beyond the pullback extreme with that mode's ATR noise buffer. Use only actual opposing swing/liquidity pivots for targets; extra targets are optional, and a lone credible TP1 is final. Incomplete plans must not invent stops or targets.

## Backtest evidence boundary

Existing trade summaries without historical OHLCV cannot replay this strategy.

**Why:** The user explicitly said not to claim this strategy is profitable or superior without evidence; old entry/exit summaries are not a strategy backtest.

**How to apply:** Only report backtest metrics from suitable historical candle data and at least 50 replayable prior setups. If candles are missing, label the metrics unavailable instead of substituting old trade outcomes.