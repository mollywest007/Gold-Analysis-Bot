---
name: Gold Bot Momentum-Pullback Framework
description: The production XAU/USD selected-timeframe momentum-pullback rules and backtest evidence boundary.
---

Production XAU/USD entries use clear local HH/HL or LH/LL swing structure on the selected timeframe. Require a controlled pullback near EMA20, the protected swing, or a prior breakout/breakdown level, followed by rejection and a close through the minor pullback swing. RSI is a momentum disagreement filter; EMA20/EMA50 do not define direction, and higher-timeframe confirmation is not required.

**Why:** The user chose a fast, selective pullback strategy and explicitly rejected waiting for full trend completion or multiple higher-timeframe confirmations.

**How to apply:** Reject chop, extension, strong RSI disagreement, structural risk above 2.5 ATR, or less than 1R to the nearest real opposing pivot. Put the stop beyond the pullback extreme with an ATR noise buffer. Use only actual opposing swing/liquidity pivots for targets; extra targets are optional, and a lone credible TP1 is final. Incomplete plans must not invent stops or targets.

## Backtest evidence boundary

Existing trade summaries without historical OHLCV cannot replay this strategy.

**Why:** The user explicitly said not to claim this strategy is profitable or superior without evidence; old entry/exit summaries are not a strategy backtest.

**How to apply:** Only report backtest metrics from suitable historical candle data and at least 50 replayable prior setups. If candles are missing, label the metrics unavailable instead of substituting old trade outcomes.