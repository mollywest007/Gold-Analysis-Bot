---
name: Gold Bot Momentum-Pullback Framework
description: The production XAU/USD selected-timeframe momentum-pullback rules and backtest evidence boundary.
---

Production XAU/USD entries keep the direction → pullback → rejection → structure confirmation → entry sequence, with execution scaled by both active mode and selected timeframe. Use the selected chart's HH/HL or LH/LL pivots, then apply ATR-relative pivot radius/lookback, pullback window/depth, chase limit, break displacement, structural stop, and opposing-level target clearance. Scalp is prompt on lower charts; Intraday is more selective; Swing/Position use broader confirmed structure and ignore small intraday breaks. RSI14 is soft: prefer BUY above 45 and rising, SELL below 55 and falling; only a strong conflict (BUY below 35 or SELL above 65) vetoes. EMA20/EMA50 are context, not directional gates. Never use fixed pip/point distances or higher-timeframe confirmation. Combined mode must resolve each stream independently; invalid explicit modes must fail rather than inherit another profile.

**Why:** The user requires the same core setup to adapt to mode and timeframe instead of replacing the strategy or reusing identical rules. Strong-only RSI vetoes keep the oscillator from overriding price action, while wider pivot windows and ATR-normalized distances prevent lower-timeframe noise or fixed pip rules from governing higher-timeframe plans.

**How to apply:** Resolve one shared mode+timeframe profile before analysis and reuse it in the scanner, manual chart prompts, historical replay, and user-facing rules. Keep Scalp selectable on M1/M3/M5/M15/M30/H1 and Swing on H1/H4/D1; ensure each extended timeframe receives its own scaled profile. Evaluate the full sequence jointly; reject chop, extension, strong RSI disagreement, stops above the resolved ATR cap, or insufficient room to a real opposing pivot under its RR/room floor. Do not label a quiet range as chop from candle-range compression alone; require failed directional progress with repeated reversals or EMA whipsaws, or a genuinely bounded support/resistance box. Put the stop beyond the pullback extreme with the resolved ATR noise buffer. Use only actual opposing selected-timeframe swing/liquidity pivots for targets; extra targets are optional, and a lone credible TP1 is final. Incomplete or ambiguous plans must not invent entries, stops, targets, or confirmation.

## Backtest evidence boundary

Existing trade summaries without historical OHLCV cannot replay this strategy.

**Why:** The user explicitly said not to claim this strategy is profitable or superior without evidence; old entry/exit summaries are not a strategy backtest.

**How to apply:** Only report backtest metrics from suitable historical candle data and at least 50 replayable closed outcomes. If candles are missing, label the metrics unavailable instead of substituting old trade outcomes.

Historical replay reports first-touch outcomes, not broker-exact or net P&L. It fills at the next candle open only when the planned entry is still actionable, and counts SL first if SL and TP1 both touch within one OHLC candle.

**Why:** The available feed has no historical bid/ask spread, slippage, or configured partial-position sizes, so a dollar return or net R result would imply unsupported execution precision.

**How to apply:** Label TP1-first and SL-first rates as first-touch metrics, disclose excluded costs and partial sizing, and do not infer net profitability until historical execution data and sizing rules are defined.