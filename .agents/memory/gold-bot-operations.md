---
name: Gold Bot Operations
description: Operational constraints for running the Telegram bot safely.
---

Telegram long polling permits only one active `getUpdates` consumer for a bot token. A `409 Conflict` means another process or deployment is polling the same token; stop the duplicate before troubleshooting the bot code.

**Why:** Replit startup validation can succeed while message delivery still fails when an older local or deployed instance owns the polling session.

**How to apply:** Run exactly one polling workflow for the token, and treat repeated conflict errors as an operational duplicate-instance issue.

HTTP client debug logs must remain below INFO because Telegram bot tokens and Google API keys may be included in request URLs.

**Why:** Request URLs are emitted in verbose HTTP logs, which can expose credentials in workflow output.

**How to apply:** Keep `httpx` and `httpcore` at WARNING or stricter in bot workflows.

Reminder timing must be based on each trade's own candle timeframe, not a global
interval. The first missed-entry reminder should wait until at least one full
candle has elapsed; later status reminders should scale from that same period.

**Why:** A global short-timeframe reminder caused H1 trades to receive an
incorrect early "missed alert" notification.

**How to apply:** Use the trade's stored timeframe when calculating reminder
windows, while keeping the scheduler cadence independent of those windows.