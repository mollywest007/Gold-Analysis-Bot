---
name: Gold Bot Notification Delivery
description: Durable rule for Telegram alert state and fallback-data safety.
---

Notification deduplication and reminder milestones must be consumed only after at least one Telegram delivery succeeds. Failed sends remain eligible for retry, and simulated or fallback market data must never produce actionable setup, entry, or momentum-shift notifications.

**Why:** Transient Telegram failures should not permanently suppress alerts, while fallback prices are not reliable enough to justify trading actions.

**How to apply:** Keep delivery-result checks around setup-forming, momentum-shift, entry, and missed-entry notifications, and reject simulated analyses before any actionable notification path.