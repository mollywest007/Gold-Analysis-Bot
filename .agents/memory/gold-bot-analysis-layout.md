---
name: Gold Bot Analysis Layout
description: Durable presentation rules for Telegram analysis and entry decision panels.
---

Keep the existing Telegram card's monospaced, sectioned visual style: market snapshot, WHAT TO DO, HOW IT IS ANALYZING, TRADE PLAN, CURRENT CONFIRMATION, and SKIPPED SETUP when a rejected candidate exists. Improve spacing and clarity rather than replacing it with a radically different summary layout.

**Why:** On 2026-10-17 the user clarified that the previous redesign changed the familiar card too much; they want the same structured analysis, easier to read and less crowded.

**How to apply:** Keep aligned label/value rows with readable wrapping and section dividers. Show current market behavior, setup, real blockers, trigger, and invalidation from the existing engine; remove duplicate/noisy details without changing strategy or risk rules.

Telegram clients display monospaced rows on narrow screens, so wrap long values onto indented continuation lines and keep labels concise.

**Why:** The user wants the original sectioned card to remain recognizable while being easier to scan on a phone.

**How to apply:** Use clear WAIT/BUY/SELL status markers and state explicitly when no structural invalidation level exists instead of inventing one.