---
name: Institutional Price-Action Framework
description: The bot's strict price-action decision rules for XAU/USD analysis.
---

The institutional decision layer uses only market structure, higher-timeframe trend, liquidity, order blocks, fair value gaps, supply/demand context, and strong candle confirmation. Chart-pattern libraries and low-value indicator votes may remain for compatibility, but they must not create a confirmed trade or appear as the primary framework output.

Confidence is scored directionally as Trend Alignment 25, Market Structure 25, Liquidity Confirmation 20, Order Block Reaction 15, FVG Confirmation 10, and Candlestick Confirmation 5. A score below 60 is always Neutral/WAIT; 80+ may be labeled Strong Buy or Strong Sell.

**Why:** The user explicitly prioritized institutional price action over quantity of indicators and required mixed evidence to remain no-trade.

**How to apply:** Analyze Daily, H4, H1, then M15. Only suggest a confirmed direction when all four real-data contexts agree and each clears the 60-point threshold; otherwise preserve WAIT and show the missing evidence.

The legacy indicator engine is a secondary confirmation layer: aligned legacy direction strengthens the result, a neutral/tied legacy result does not veto valid institutional evidence, and a direct conflict blocks the trade.

Manual analysis reports and automatic entry alerts must use this same strict consensus gate. Lower-timeframe scalp reports may retain their local indicator details, but they remain gated by the Daily → H4 → H1 → M15 consensus.

**Why:** Different gates made the bot's explanation say WAIT while its background scanner could still treat a local setup as actionable.

**How to apply:** Do not disable strict timeframe consensus in the automatic alert path; use the shared analysis entry point so Telegram alerts and `/analyze` cannot disagree.