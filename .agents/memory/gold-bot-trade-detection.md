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

## Opposite-signal warnings

**A blocked opposite signal must warn before entry filters run.** When an active
trade owns a timeframe, a new opposite direction is never opened, but the
momentum-shift warning must be sent before higher-timeframe/counter-trend
filters can reject the candidate. The warning is deduplicated per timeframe and
direction.

**Why:** A valid short-term reversal can be classified as a full signal and
then silently disappear at the higher-timeframe gate, leaving the trader
unaware that the active plan is under pressure.

**How to apply:** Keep notification of an active-trade conflict separate from
eligibility to open a new trade. Full signals should warn; only forming signals
use the three-vote pre-alert.

## Mode-specific plans and reminders

**Risk settings belong to the mode that created the trade.** New Scalp plans
use their scalp ATR stop and 1.5R/2.5R/3.5R target ladder; Intraday, Swing,
and Position use their own wider stop and target policies. Existing trades keep
their frozen levels when the user switches modes.

**Why:** Recalculating an active trade after a mode switch would move the
promised SL/TP and invalidate the plan the trader entered.

**How to apply:** Persist the originating mode on new trades and show it in
the active panel. Missed-entry reminders must be due-based after their minimum
age because the reminder job runs every 10 minutes and bounded windows miss
short timeframes.

## Exit-data safety

Simulated or fallback OHLCV must never be used to trigger TP/SL state changes.
If verified post-entry candle data is unavailable, use the live spot price only
and leave wick-based exit detection for a later scan.

**Why:** Generated fallback candles can invent a wick through a real trade's
stop, especially during market-data outages or weekend sessions.

**How to apply:** Reject simulated candle extremes before passing timeframe
high/low data to the trade tracker; use only the newest verified post-entry
candle, and treat a missing or failed candle fetch as spot-only rather than
exit evidence.

## Delayed result safety

Terminal SL/BE notifications must be revalidated against the persisted,
account-owned trade immediately before delivery. If a newer active plan now
owns the same timeframe, suppress the delayed old result rather than making it
look like the active plan was stopped.

**Why:** Notification delivery can fail or be delayed after a trade closes and
the user may open a replacement plan before the pending result is retried.

**How to apply:** Keep result/cooldown delivery account-scoped and check the
current active timeframe owner before sending `SL` or `TP1_SL`.

## Entry delivery safety

An entry alert is sent only after its validated trade plan has been persisted.
If no recipient accepts the alert, roll back the untouched record; reconcile
signal locks against persisted active trades on every scan.

**Why:** Sending first allowed a malformed plan or persistence failure to show
an entry while `/active` had no matching position, and stale locks then hid
the next valid signal.

**How to apply:** Treat the persisted trade record as the position source of
truth; signal state is only an alert-delivery guard.

## Post-TP3 re-analysis cooldown

After a trade reaches its final TP3 target, entry alerts on that timeframe pause
for 10 minutes while the scanner continues re-analyzing the market. The same
direction is eligible again after the cooldown; TP1, TP2, SL, and break-even
events use their existing behavior.

**Why:** Immediately reopening after a completed full move can chase an
unchanged setup and create low-quality repeat entries.

**How to apply:** Keep this as a separate persisted cooldown from the
post-stop-loss cooldown so it applies only to final-target completion.

## Legacy terminal-lock migration

**Rule:** A persisted same-direction terminal lock from before the TP3 cooldown
format must be reconciled against trade history; an expired completed TP3 must
re-arm the timeframe instead of requiring an unrelated direction change.

**Why:** Older `closed_signal` records had no cooldown metadata and could
silently block a valid same-direction entry forever after the 10-minute window.

**How to apply:** When evaluating a closed-direction lock with no active
cooldown, recognize a matching historical `tp3_hit` trade whose cooldown has
expired, clear the legacy lock, and continue through normal entry filters.

## Restart recovery for stop-loss cooldowns

**Rule:** Restore future `cooldown_until` values from persisted stop-loss trade
records before removing signal locks that no longer have active trade owners.

**Why:** A restart can happen after a trade closes but before alert state saves
the cooldown; deleting the stale lock first makes the just-stopped timeframe
eligible for an immediate duplicate entry.

**How to apply:** Treat terminal persisted loss records as authoritative during
startup and every account scan, while keeping the regular two-candle cooldown
filter responsible for blocking new entries.
