---
name: Gold Bot Analysis Layout
description: Durable presentation rules for Telegram analysis and entry decision panels.
---

The Telegram analysis experience uses self-contained strict and early evidence cards: each card repeats the relevant market snapshot, raw indicator values, gate checks, plan levels, and invalidation so neither path depends on a truncated continuation.

**Why:** A single combined card could exceed Telegram's message limit and hide the end of the strict gate or early invalidation. Independent cards preserve the distinction between a manual provisional setup and a confirmed active-trade plan.

**How to apply:** Keep strict confirmation labeled as confirmed or waiting, keep provisional levels labeled manual-only and non-active, and keep each card below Telegram's message limit. Show neutral or unavailable fields explicitly as `Not used` or `UNAVAILABLE`.