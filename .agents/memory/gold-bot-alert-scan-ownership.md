---
name: Alert Scan Ownership
description: Ownership boundary for Telegram alert scans and legacy maintenance callers.
---

Production Telegram scans must carry the owning account ID through trade lookup, signal state, cooldowns, and notification decisions. The no-account scan helper exists only for legacy maintenance and isolated tests; it must not use every persisted account's trades as if they belonged to the caller.

**Why:** A shared lookup can suppress or duplicate alerts when another account has the same timeframe open, creating incorrect user-visible behavior and nondeterministic tests.

**How to apply:** Preserve account IDs in scheduled scan paths, keep combined-mode state stream-qualified, and explicitly mock market status in scan tests so calendar state cannot make notification assertions flaky.