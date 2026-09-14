---
name: Institutional Price-Action Framework
description: The bot's strict price-action decision rules for XAU/USD analysis.
---

The institutional decision layer uses only the selected timeframe's market structure, liquidity, order blocks, fair value gaps, supply/demand context, and strong candle confirmation. Chart-pattern libraries and low-value indicator votes may remain for compatibility, but they must not create a confirmed trade or appear as the primary framework output.

Confidence is scored directionally as Trend Alignment 25, Market Structure 25, Liquidity Confirmation 20, Order Block Reaction 15, FVG Confirmation 10, and Candlestick Confirmation 5. A score below 60 is always Neutral/WAIT; 80+ may be labeled Strong Buy or Strong Sell.

**Why:** The user explicitly prioritized institutional price action over quantity of indicators and required mixed evidence to remain no-trade.

**How to apply:** Analyze only the timeframe selected by the user. Suggest a confirmed direction when that timeframe's real-data context clears the local entry criteria; never wait for another timeframe to agree.

The legacy indicator engine is a secondary confirmation layer: aligned legacy direction strengthens the result, a neutral/tied legacy result does not veto valid institutional evidence, and a direct conflict blocks the trade.

Manual analysis reports and automatic entry alerts must use the same selected-timeframe decision. Other timeframe reports may exist for explicit diagnostics, but they cannot gate or confirm a normal entry.

**Why:** The user wants entries as soon as the selected mode/timeframe meets its conditions instead of waiting for unrelated timeframe agreement.

**How to apply:** Keep Telegram alerts and `/analyze` on the shared local analysis entry point so both use only the selected timeframe and cannot reintroduce a multi-timeframe entry gate.