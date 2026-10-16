---
name: Gold Bot Analysis Layout
description: Durable presentation rules for Telegram analysis and entry decision panels.
---

The primary Telegram analysis card is compact and decision-first: symbol/mode/timeframe, market trend and price action, setup, the actual blocker, status, entry trigger, and invalidation. It omits raw indicators and repeated trade-plan detail.

**Why:** On 2026-10-16 the user asked to understand direction, setup, missing confirmation, trigger, and invalidation at a glance without changing the strategy.

**How to apply:** Reuse one compact renderer across analysis, recommendation, signal, and entry-alert messages. Derive waiting conditions and triggers from the selected engine's real rejection reasons and configured thresholds; never substitute generic confirmation text or modify entry/risk rules for presentation.

Telegram clients wrap normal text automatically, so keep labels concise and fields in a stable left-to-right decision order rather than using fixed-width columns.

**Why:** Fixed-width columns and long preformatted sections are hard to scan on narrow Telegram screens.

**How to apply:** Use short inline labels and a distinct status marker for WAIT/BUY/SELL; state explicitly when no structural invalidation level exists instead of inventing one.