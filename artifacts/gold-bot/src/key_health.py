"""
Key health checks for TELEGRAM_BOT_TOKEN and GOOGLE_AI_KEY.

Provides:
  - validate_keys_on_startup() — hard-exits the process if a required key is
    missing or clearly invalid before the bot starts polling.
  - _check_telegram_token() / _check_google_ai_key() — async probes used by
    both startup validation and the periodic runtime health-check job.
  - periodic_key_health_check() — APScheduler job that runs every 6 hours,
    sends a Telegram warning to all subscribers if either key has become
    invalid, and logs the result either way.
"""

import asyncio
import logging
import sys
import time
from typing import Tuple

logger = logging.getLogger(__name__)

# Timestamp of the last health-check warning sent to Telegram.
# Prevents flooding the user if the key stays bad across multiple cycles.
_last_warn_sent: float = 0.0
_WARN_COOLDOWN   = 6 * 3600   # at most one Telegram warning per 6-hour cycle


async def _check_telegram_token(token: str) -> Tuple[bool, str]:
    """
    Verify a Telegram bot token by calling getMe.
    Returns (ok, human_readable_reason).
    """
    if not token:
        return False, "TELEGRAM_BOT_TOKEN is not set"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getMe"
            )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True, "OK"
        if resp.status_code == 401:
            return False, "Token invalid or revoked (401 Unauthorized) — generate a new token via @BotFather"
        return False, f"Unexpected Telegram response: HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Network error reaching Telegram: {e}"


async def _check_google_ai_key(key: str) -> Tuple[bool, str]:
    """
    Verify a Google AI key by listing one model (cheap, read-only call).
    Returns (ok, human_readable_reason).
    """
    if not key:
        return False, "GOOGLE_AI_KEY is not set — chart analysis (/chart command) will not work"
    try:
        import httpx
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={key}&pageSize=1"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return True, "OK"
        if resp.status_code in (400, 401):
            return False, "Key is invalid or malformed — visit aistudio.google.com to create a new key"
        if resp.status_code == 403:
            return False, "Key is expired, revoked, or quota exceeded — check aistudio.google.com"
        return False, f"Unexpected Google AI response: HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Network error reaching Google AI: {e}"


def _sync_check_telegram(token: str) -> Tuple[bool, str]:
    """Synchronous Telegram token probe — safe to call before any event loop."""
    if not token:
        return False, "TELEGRAM_BOT_TOKEN is not set"
    try:
        import httpx
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True, "OK"
        if resp.status_code == 401:
            return False, "Token invalid or revoked (401) — generate a new token via @BotFather"
        return False, f"Unexpected Telegram response: HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Network error reaching Telegram: {e}"


def _sync_check_google_ai(key: str) -> Tuple[bool, str]:
    """Synchronous Google AI key probe — safe to call before any event loop."""
    if not key:
        return False, "GOOGLE_AI_KEY is not set — /chart command will not work"
    try:
        import httpx
        resp = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={key}&pageSize=1",
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "OK"
        if resp.status_code in (400, 401):
            return False, "Key invalid or malformed — visit aistudio.google.com to create a new key"
        if resp.status_code == 403:
            return False, "Key expired, revoked, or quota exceeded — check aistudio.google.com"
        return False, f"Unexpected Google AI response: HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Network error reaching Google AI: {e}"


def validate_keys_on_startup(token: str, google_key: str) -> None:
    """
    Synchronous startup check.  Runs BEFORE the bot enters its polling loop.
    Uses plain synchronous HTTP so it never touches the asyncio event loop.

    - TELEGRAM_BOT_TOKEN missing/invalid → log error + sys.exit(1).
      The bot cannot function at all without it.
    - GOOGLE_AI_KEY missing/invalid → log a warning only.
      The bot runs fine; only the /chart command is affected.
    """
    logger.info("Validating API keys before startup…")

    tg_ok,  tg_msg  = _sync_check_telegram(token)
    gai_ok, gai_msg = _sync_check_google_ai(google_key)

    if tg_ok:
        logger.info("✅ TELEGRAM_BOT_TOKEN — valid")
    else:
        logger.error(f"❌ TELEGRAM_BOT_TOKEN — {tg_msg}")
        logger.error("Cannot start without a valid Telegram token. Exiting.")
        sys.exit(1)

    if gai_ok:
        logger.info("✅ GOOGLE_AI_KEY — valid")
    else:
        logger.warning(f"⚠️  GOOGLE_AI_KEY — {gai_msg}")
        logger.warning("Bot will start, but /chart command will not work until this is fixed.")


async def periodic_key_health_check(context) -> None:  # type: ignore[type-arg]
    """
    APScheduler job — runs every 6 hours.

    Checks both keys silently when healthy.
    If either key has degraded since startup, sends one Telegram warning
    message to all subscribers and logs the failure.  The warning is
    rate-limited to one per 6-hour window so it never floods the chat.
    """
    global _last_warn_sent

    from src.config import TELEGRAM_BOT_TOKEN, GOOGLE_AI_KEY

    tg_ok,  tg_msg  = await _check_telegram_token(TELEGRAM_BOT_TOKEN)
    gai_ok, gai_msg = await _check_google_ai_key(GOOGLE_AI_KEY)

    all_ok = tg_ok and gai_ok

    if all_ok:
        logger.info("Key health check — all keys valid ✅")
        return

    # Build a warning message
    lines = ["⚠️ <b>API KEY HEALTH WARNING</b>", "─" * 30]
    if not tg_ok:
        lines.append(f"🔴 <b>Telegram token:</b> {tg_msg}")
    if not gai_ok:
        lines.append(f"🟡 <b>Google AI key:</b> {gai_msg}")
    lines += [
        "─" * 30,
        "Update the affected secret in Replit and restart the bot.",
        "The bot will continue running but degraded functionality may occur.",
    ]
    text = "\n".join(lines)

    # Rate-limit warnings to one per cooldown window
    now = time.time()
    if now - _last_warn_sent < _WARN_COOLDOWN:
        logger.warning(
            "Key health check — degraded keys detected, but warning already "
            "sent recently. Skipping Telegram message."
        )
        return

    # Broadcast to all subscribers
    try:
        from src.alerts import _load, _broadcast_text  # type: ignore[attr-defined]
        bot  = context.application.bot
        subs = _load()
        if subs:
            await _broadcast_text(bot, subs, text)
            _last_warn_sent = now
            logger.warning(
                f"Key health warning sent to {len(subs)} subscriber(s). "
                f"tg_ok={tg_ok} gai_ok={gai_ok}"
            )
        else:
            logger.warning(
                f"Key health degraded but no subscribers to notify. "
                f"tg_ok={tg_ok} gai_ok={gai_ok}"
            )
    except Exception as e:
        logger.error(f"Key health check — failed to send warning: {e}")
