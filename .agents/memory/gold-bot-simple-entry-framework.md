---
name: Gold Bot Simple Entry Framework
description: The active XAU/USD entry decision framework and its intentional confirmation boundary.
---

The production XAU/USD decision should remain simple: EMA20 versus EMA50 defines the market direction, RSI14 only checks directional momentum support, local price action identifies a breakout, continuation, rejection, or developing pullback, and ATR14 sets the risk distance. Use a balanced moderate-entry gate: one strong local confirmation or two reasonable confirmations; do not require every indicator, higher timeframe, or institutional factor.

**Why:** The former institutional evidence chain could withhold alerts for days even when a usable directional setup existed, and its R:R display could differ from the R:R used by the final gate. The user explicitly chose a middle ground: enough confirmation to avoid premature entries, but a timely opportunity before most of the move is gone.

**How to apply:** A `MODERATE ENTRY` requires real data, a directional EMA context, and either one strong local event (rejection/breakout) or two reasonable aligned signals. `WAIT`/`DEVELOPING` keeps scanning, `MISSED` waits for a pullback/retest, and `INVALID` cancels the thesis. The selected timeframe remains the only entry decision source.