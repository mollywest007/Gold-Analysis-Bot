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