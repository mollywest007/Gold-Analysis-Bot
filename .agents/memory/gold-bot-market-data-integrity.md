---
name: Gold Bot Market Data Integrity
description: Rules for preserving trustworthy candle boundaries and liquidity evidence.
---

Higher-timeframe aggregation must use complete UTC-aligned provider candles and discard partial edge buckets. Liquidity sweeps must compare the current wick with the prior range, never a range containing that same wick.

**Why:** Positional grouping can shift H4/M3 candles when a response begins mid-bucket, and including the current candle makes a strict sweep test impossible while still looking plausible in reports.

**How to apply:** Keep timestamps aligned with filtered OHLCV rows, reject incomplete aggregation buckets, and use the prior completed range for sweep detection. Treat simulated or stale candles as unavailable for actionable plans.