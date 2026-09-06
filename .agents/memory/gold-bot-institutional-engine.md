---
name: Institutional Engine Data Boundaries
description: Durable guardrails for the bot's institutional analysis layer and unavailable external data.
---

The institutional analysis layer may calculate deterministic OHLCV-derived context, but it must not invent macro calendars, real yields, session win rates, or dealer/order-flow data. Those inputs are explicitly marked unavailable until a live provider is configured.

**Why:** A plausible-looking fallback can turn a data outage into a false trading edge, which is more dangerous than returning WAIT.

**How to apply:** Keep unavailable external feeds visible in reports, let the final gate suppress trades when required confirmation is missing, and only promote an external feed after validating its timestamps, symbol semantics, and failure behavior.