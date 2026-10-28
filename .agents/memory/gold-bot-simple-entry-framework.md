---
name: Gold Bot Weighted Multi-Timeframe Entry Strategy
description: The active mode-configured XAU/USD analysis engine and its backtest evidence boundary.
---

The current XAU/USD engine uses mode-configured context, selected setup, and optional refinement timeframes with weighted evidence. A previous instruction removed adaptive analysis, but the user explicitly superseded it by asking to rebuild weighted multi-timeframe analysis. Preserve the four real modes, the combined Scalp/Intraday coordinator, alerts, and trade tracking.

**Why:** The user later explicitly requested the weighted multi-timeframe engine, so the earlier single-timeframe-only decision is no longer current.

**How to apply:** Keep the selected setup timeframe authoritative, derive context/refinement roles from the active mode, and ensure mode selection applies the matching score thresholds, weights, lookbacks, volatility limits, and R:R settings. Do not treat the combined coordinator as a standalone strategy.

## Preserve the existing engine

Inspect and improve the existing trading engine rather than rebuilding it. Preserve working behavior, and explain evidence and findings before making significant strategy changes.

**Why:** The user explicitly asked to retain the existing implementation and avoid unsupported strategy changes.

**How to apply:** Trace the live and backtest paths first; make narrow corrections for confirmed defects, and do not change mode thresholds or strategy rules without reporting the evidence and expected effect.

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

## Minimum versus preferred target R:R

Each mode's hard R:R minimum is separate from its preferred target R:R. Preferred R:R is an aim, never a distance cap: use the nearest confirmed opposing structure when it offers more room, and reject targets that fall below the hard minimum after available spread costs and published-price rounding.

**Why:** The user explicitly requires at least 1.5R, preferably 2R, and forbids inventing distant targets to reach a preferred ratio. The previous Scalp 1.3R floor and 1.5R preference contributed to small plans.

**How to apply:** Keep Scalp at minimum 1.5R / preferred 2R; Intraday at minimum 1.5R / preferred 2R; Swing at 2R / 2R; Position at 2R / 3R unless a validated per-mode override is configured. Let TP exceed the preference only when the nearest real structure supports it; never invent a farther target. Derive each displayed ratio from exact rounded prices.

## Spacing between structural target milestones

Require each later target to be at least the selected mode/timeframe's `minimum_target_room_atr × ATR` beyond the previous target. If confirmed structure does not provide that room, omit TP2/TP3 rather than presenting nearby pivots and zone centers as separate milestones.

**Why:** Swing pivots and support/resistance-zone centers can describe the same price cluster, creating misleadingly close target levels.

**How to apply:** Keep TP1's structural and R:R validation authoritative; apply the spacing rule to optional later targets and the final trade-persistence guard. Never rewrite an existing plan retroactively.

## Mode-specific TP/SL scale

Keep mode-specific ATR stop floors and buffers, increasing with the intended holding horizon. Do not use a shared fixed-dollar distance; if real structure cannot support the stop and minimum target R:R, return WAIT.

**Why:** The user asked for more room in Scalp Mode; the inspected M15 plan had been pinned to a 0.715 ATR floor. The user also wants longer-horizon modes to retain progressively wider settings.

**How to apply:** Use base minimum stop distances of 1.0 ATR (Scalp), 1.1 ATR (Intraday), 1.25 ATR (Swing), and 1.5 ATR (Position), then apply timeframe scaling. Keep structural invalidation and mode caps authoritative; reject setups without enough opposing target room. Legacy `sl_mult_override` values are not used by normal live weighted analysis.

## Entry confirmation levels

Analysis must show numerical entry stages from live spot data and completed setup candles, while preserving the active setup type's real confirmation rule. Recalculate on each Analysis refresh. If a setup uses pattern confirmation without a fixed price threshold, say so instead of inventing one. Keep WAIT and EARLY provisional until the weighted context, score, and risk gates pass; an analysis confirmation is not a broker fill.

**Why:** The user explicitly asked for exact, step-by-step live price levels and required that entries never be labeled confirmed before the strategy conditions are met.

**How to apply:** Separate the buffered initial breakout trigger from post-retest confirmation, preserve direct-breakout paths where retests are optional, and show an explicit unavailable/WAIT state when verified live data or a numeric trigger is missing.