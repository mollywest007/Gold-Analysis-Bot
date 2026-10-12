---
name: Gold Bot Adaptive Entry Strategy
description: The shared mode-scaled XAU/USD breakout/pullback rules and backtest evidence boundary.
---

Production XAU/USD entries use one shared engine scaled by active mode and selected timeframe. Direction comes from the selected chart's confirmed HH/HL or LH/LL structure. Valid entry types are (1) a contained close beyond a confirmed swing level, without chasing an extended candle, and (2) an ATR-controlled pullback to a meaningful level. Direction plus valid location can qualify as an early, lower-grade setup; rejection or a minor structure break improves quality but is not mandatory. Strong opposing price evidence or RSI conflict still blocks a setup. EMA20/EMA50 are context, not directional gates. Stops remain beyond structural invalidation with mode/timeframe ATR buffer and a configured reasonableness cap. TP1 must come from real opposing structure with minimum planned R:R 1:1.5. Never use fixed pip/point distances or higher-timeframe confirmation. Combined mode must resolve each stream independently; invalid explicit modes must fail rather than inherit another profile.

**Why:** The user wants a moderate shared strategy that can take defensible early entries and contained momentum breaks, rather than requiring every pullback confirmation or allowing indicator-only signals. Risk/target levels must stay tied to observed structure and mode/timeframe volatility.

**How to apply:** Resolve one shared mode+timeframe profile before analysis and reuse it in the scanner, manual chart prompts, historical replay, and user-facing rules. Keep each mode's existing selectable timeframes and holding window. Reject chop, extended moves, strong RSI disagreement, unreasonable structural stops, and targets obstructed or short of the 1.5R floor. Do not label a quiet range as chop from candle-range compression alone; require failed directional progress with repeated reversals or EMA whipsaws, or a genuinely bounded support/resistance box. Keep entry grade lower when a valid direction/location setup lacks a rejection or minor break. Incomplete or ambiguous plans must not invent entries, stops, targets, or confirmation.

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