---
name: Gold Bot Engine v4 Upgrade
description: What was added in the v4 analysis engine upgrade and key decisions made.
---

## What was upgraded

### New indicator functions (engine.py)
- `compute_cci(period=20)` — Commodity Channel Index; 8th core indicator; overbought/oversold + trend-aligned mid-zone scoring via `_score_cci()`
- `compute_vwap(session_bars=24)` — VWAP using volume-weighted TP; used in confluence (above/below bias) and early-entry priority waterfall
- `compute_bb_bandwidth()` — BB width as % of midline; feeds `detect_market_regime()`
- `detect_hidden_divergence(lookback=30)` — Bullish/bearish hidden divergence for trend continuation; optional indicator vote + confluence
- `detect_chart_pattern(lookback=60)` — H&S, Inverse H&S, Double Top/Bottom, Bull/Bear Flag, Ascending/Descending Triangle, Rising/Falling Wedge
- `detect_market_regime()` — TRENDING | RANGING | VOLATILE | SQUEEZE | NORMAL; uses ADX + BB bandwidth; adjusts win probability (TRENDING +3, RANGING −3, SQUEEZE +2)
- `compute_supertrend()` — replaced single-bar approximation with proper iterative Wilder-smoothed ATR + band-locking algorithm

### Candlestick patterns added
Three Inside Up/Down, Bullish/Bearish Kicker, Bullish/Bearish Abandoned Baby, Bullish/Bearish Belt Hold, Bullish/Bearish Counterattack

### Indicator list (now 8 core)
RSI(14) 0.17, MACD 0.18, EMA Stack 0.20, ADX DI 0.17, CCI(20) 0.12, BB %B 0.09, Williams%R 0.05, Supertrend 0.02
Optional: Candle 0.10, RSI Div 0.08, Chart Pat 0.09, Hidden Div 0.07

### Early entry priority waterfall (BUY)
1. Order Block → 2. VWAP pullback → 3. FVG → 4. OTE (61.8%) → 5. Fib 50% → 6. Fib 38.2% → 7. EMA fallback
(SELL is mirrored)

### MarketAnalysis new fields
cci_value, vwap, chart_pattern, chart_pattern_signal, market_regime, hidden_divergence, bb_bandwidth

### Setup quality grade
Core votes now include CCI(20) (6 possible: RSI, MACD, EMA, ADX, CCI, BB). TRENDING regime counts as extra effective vote for grade A.

**Why:** Gold is cyclical (CCI excels), VWAP is the institutional benchmark, chart patterns add classical TA conviction, iterative Supertrend prevents false band-flip signals on single bars.

**How to apply:** All new fields on MarketAnalysis are safe to read with getattr(..., default) in formatting; no None risk. Votes display should show /8 not /7.
