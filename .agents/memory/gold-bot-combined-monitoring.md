---
name: Gold Bot Combined Monitoring
description: Durable rules for the combined Scalp / Interval monitoring mode.
---

Combined monitoring is an additive coordinator: it runs the existing Scalp and Interval profiles independently, with one user-selected timeframe per stream. Existing single-mode behavior and state namespaces must remain unchanged.

**Why:** Traders need both alert streams without switching modes, while changing the state model for existing modes would risk duplicate alerts or stale locks.

**How to apply:** Namespace locks and active-trade ownership by stream only when combined mode is active. Prefix every combined-mode entry, setup, momentum, result, cooldown, and reminder notification with `SCALP ALERT` or `INTERVAL ALERT`.