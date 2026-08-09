---
name: Gold Bot Trade Detection Bugs
description: Known bugs and their fixes in the TP/SL detection and trade reminder system.
---

## SL/TP detection rules

**SL uses candle extremes (highs/lows); TP now also uses post-entry candle extremes.**

Both are filtered in `alerts.py` to only include candles whose open timestamp >= trade `opened_at`. When no post-entry candle exists (new trade, no completed candle yet), `tf_extremes[tf]` is set to `(current_price, current_price)` — collapses to spot-price only for that cycle.

**Why:** SL-only candle detection was asymmetric — genuine TP wick hits between 15-second polls were silently missed while SL wicks were caught.

## False immediate SL bug (fixed)

**Root cause:** When a trade was just opened and no new candle had formed yet, the fallback in `alerts.py` previously used the last completed candle (the pre-entry candle). For a SELL trade the SL sits just above the wick that formed the signal, so that candle's high immediately triggered SL.

**Fix:** When `indices` is empty (no post-entry candle), set `hi = lo = current_price`. SL detection waits for a real post-entry candle. File: `alerts.py`, `tf_extremes` building loop.

## Break-even SL after TP1 (added)

After TP1 is captured, entry becomes the effective SL. If price retraces back through entry before TP2 is hit, `check_trades` in `trade_tracker.py` fires a `TP1_SL` event at break-even. This prevents riding a full loss after a partial win.

**How to apply:** Logic runs inside `check_trades` before the original `sl_hit` check. Uses same `sl_lo`/`sl_hi` candle extremes.

## Reminder message when tp1 has retraced (fixed)

When `tp1_hit=True` and price is back below entry (BUY) or above entry (SELL):
- TP2/TP3 lines are suppressed from the reminder message
- A clear warning is shown: "TP1 was hit but price has retraced — break-even SL active"

**Why:** Showing TP2/TP3 targets while the trade is underwater was confusing and made it look like a TP notification.

## Near-entry reminder threshold

Tightened from 0.5% (≈$20 on gold) to 0.15% (≈$6 on gold) for the "entry still reachable" missed-alert nudge.

## Active timeframe ownership

**A timeframe can have only one active trade plan.** The scanner claims a signal before Telegram I/O, serializes scans, and the tracker rejects a second plan even when its entry is far from the first. A TP2 trade is active only when it has a TP3; TP2 without TP3 is terminal.

**Why:** Replacing same-direction trades on entry distance and overlapping 15-second scans caused repeated 1H BUY alerts. Treating terminal TP2 records as active would block legitimate re-entry after a genuine close.

**How to apply:** Use the shared `is_active_trade` definition for alert suppression, duplicate-entry checks, and open-trade counts. Require a strictly ordered target ladder before persisting a trade.

**Partial targets do not release locks:** TP1 and TP2-with-TP3 are milestones; only SL, break-even SL, terminal TP2, TP3, or expiry releases the timeframe lock.
