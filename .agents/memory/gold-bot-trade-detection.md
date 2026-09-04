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

## Terminal exit evidence

**Rule:** Every terminal SL, break-even, or final-TP transition must persist the
evidence source, timeframe, observed high/low, live spot, and capture time.

**Why:** A terminal record containing only `sl_hit` or `tp3_hit` cannot
distinguish a genuine market wick from stale, simulated, or misaligned data
after the fact.

**How to apply:** Build evidence only after the validated extremes are selected;
store it on the trade and include it in the terminal event/log so future
investigations can reproduce the decision path.

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

## Stale analysis versus live quote

An entry signal must be rejected when the live quote has already crossed its
stop or first target before the analysis finishes.

**Why:** Analysis and spot-price requests run independently. Broadcasting a
late plan creates a position that can close on the next scan, making a valid
Telegram entry appear to be missing from `/active`.

**How to apply:** Validate the current live price immediately before persisting
and delivering a new plan; do not use simulated or unavailable prices for this
validation.

## Post-TP3 re-analysis cooldown

After a trade reaches its final TP3 target, entry alerts on that timeframe pause
for 10 minutes while the scanner continues re-analyzing the market. The same
direction is eligible again after the cooldown; TP1, TP2, SL, and break-even
events use their existing behavior.

**Why:** Immediately reopening after a completed full move can chase an
unchanged setup and create low-quality repeat entries.

**How to apply:** Keep this as a separate persisted cooldown from the
post-stop-loss cooldown so it applies only to final-target completion.

## Explicit post-TP reanalysis lifecycle

**Rule:** A final-target close is user-visible as `ALL TP HIT`, starts a
stream-scoped ten-minute reanalysis gate, and must deliver both a start and
completion notice before that stream can re-arm. Failed notices remain
retryable; completion clears the old entry, setup, momentum, pending, and
cooldown state before the next fresh analysis.

**Why:** A cooldown alone can suppress an entry while still leaving stale
direction or warning state available for reuse, and it gives no reliable
signal that the fresh-analysis phase has completed.

**How to apply:** Persist the gate independently for `scalp:<tf>` and
`interval:<tf>` in combined mode. Analyze during the gate, suppress
actionable alerts, and only admit the post-window analysis after completion
delivery succeeds.

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

## Restart recovery for pending alert claims

**Rule:** A persisted `pending_signal` is only an in-process delivery claim, not
proof that an alert is still being sent; claims present at the start of a new
serialized scan must be cleared and retried.

**Why:** A crash after persisting a claim can otherwise leave one direction
silently suppressed forever, even while analysis continues to show a valid
signal.

**How to apply:** Release orphaned claims before entry scanning, then let the
normal persistence-before-delivery and rollback logic establish the new claim.

## Live quote integrity

**Rule:** Never use a syntactically valid quote for analysis or exit detection
until it is consistent with the other spot source, the futures basis, or the
active trade's entry. Materially inconsistent feeds must fall back without
returning the rejected value.

**Why:** A provider returned a stale 2350 quote while XAU/USD was near 4480;
that false jump triggered an SL and made a still-open trade disappear from
`/active`.

**How to apply:** Validate source agreement and futures/spot deviation before
building OHLCV; skip exit transitions when a live quote is an impossible jump
from the persisted trade entry.

## Offline exit recovery

**Rule:** After a process restart, replay all verified candles after each
persisted trade's entry during a short startup window; during normal operation,
use only the newest verified candle.

**Why:** Newest-candle-only logic is safer against basis-shifted historical
wick data, but it misses an SL/TP candle formed while the bot was offline.

**How to apply:** Keep pre-entry and simulated candles excluded, use the fresh
feed only for startup recovery, and return to newest-candle-only detection after
the recovery window.
