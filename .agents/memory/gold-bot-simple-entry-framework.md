---
name: Gold Bot Simple Entry Framework
description: The active XAU/USD entry decision framework and its intentional confirmation boundary.
---

The production XAU/USD decision should remain simple: EMA20 versus EMA50 defines the market direction, RSI14 only checks directional momentum support, local price action identifies a breakout, continuation, rejection, or developing pullback, and ATR14 sets the risk distance. Use a balanced moderate-entry gate: one strong local confirmation or two reasonable confirmations; do not require every indicator, higher timeframe, or institutional factor.

**Why:** The former institutional evidence chain could withhold alerts for days even when a usable directional setup existed, and its R:R display could differ from the R:R used by the final gate. The user explicitly chose a middle ground: enough confirmation to avoid premature entries, but a timely opportunity before most of the move is gone.

**How to apply:** A `MODERATE ENTRY` requires real data, a directional EMA context, and either one strong local event (rejection/breakout) or two reasonable aligned signals. `WAIT`/`DEVELOPING` keeps scanning, `MISSED` waits for a pullback/retest, and `INVALID` cancels the thesis. The selected timeframe remains the only entry decision source.

## Risk-plan consistency

The simple path must calculate TP1/TP2/TP3 from the final stop distance actually assigned to the trade, using the active mode's target multipliers. A structural stop that exceeds the timeframe's ATR cap must fall back to the mode's ATR stop.

**Why:** The prior path used a structural invalidation for SL but an ATR fallback for targets, which could produce a TP1 smaller than the real SL distance (including a recorded M15 plan at roughly 0.68R).

**How to apply:** Validate the final `entry → SL` distance before building the target ladder, cap oversized structural stops, and keep the displayed R:R equal to the persisted trade plan.