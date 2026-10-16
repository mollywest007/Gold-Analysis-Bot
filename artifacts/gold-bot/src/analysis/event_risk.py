"""High-impact event windows from an explicitly operator-supplied UTC schedule.

This is not a news or economic-calendar provider. With no configured schedule
the result stays unavailable and is surfaced to the user; no event clearance
is inferred.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re

from .risk_policy import RISK_POLICY

_MAJOR_EVENT = re.compile(r"\b(CPI|PCE|NFP|FOMC)\b", re.IGNORECASE)


def assess_event_risk(
    now: datetime | None = None,
    schedule: str | None = None,
) -> dict:
    """Assess only major events explicitly listed as ``ISO-UTC|name`` entries."""
    raw = os.getenv("HIGH_IMPACT_EVENTS_UTC", "") if schedule is None else schedule
    if not str(raw).strip():
        return {
            "status": "UNAVAILABLE_NO_CALENDAR_FEED",
            "blocks_signal": False,
            "source": "unavailable",
            "event": None,
            "reason": "No economic-calendar feed or operator event schedule is configured.",
        }

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    before_seconds = RISK_POLICY.event_filter_before_minutes * 60
    after_seconds = RISK_POLICY.event_filter_after_minutes * 60
    events: list[tuple[datetime, str]] = []
    malformed = []
    for item in str(raw).split(","):
        if not item.strip():
            continue
        timestamp_text, separator, name = item.partition("|")
        if not separator or not name.strip():
            malformed.append(item.strip())
            continue
        try:
            stamp_text = timestamp_text.strip().replace("Z", "+00:00")
            event_at = datetime.fromisoformat(stamp_text)
            if event_at.tzinfo is None:
                malformed.append(item.strip())
                continue
            if _MAJOR_EVENT.search(name):
                events.append((event_at.astimezone(timezone.utc), name.strip()))
        except (TypeError, ValueError, OverflowError):
            malformed.append(item.strip())

    if malformed:
        return {
            "status": "INVALID_OPERATOR_SCHEDULE",
            "blocks_signal": True,
            "source": "operator_supplied_schedule",
            "event": None,
            "reason": "Configured event schedule contains an invalid UTC entry.",
        }

    for event_at, name in events:
        delta = (event_at - now_utc).total_seconds()
        if -after_seconds <= delta <= before_seconds:
            return {
                "status": "BLOCKED_CONFIGURED_EVENT",
                "blocks_signal": True,
                "source": "operator_supplied_schedule",
                "event": name,
                "event_at_utc": event_at.isoformat(),
                "reason": (
                    f"{name} is inside the configured event-risk window "
                    f"(-{RISK_POLICY.event_filter_before_minutes}/"
                    f"+{RISK_POLICY.event_filter_after_minutes} minutes)."
                ),
            }

    return {
        "status": "AVAILABLE_NO_EVENT_IN_WINDOW",
        "blocks_signal": False,
        "source": "operator_supplied_schedule",
        "event": None,
        "reason": (
            "No configured CPI, PCE, NFP, or FOMC event is inside the "
            "risk window; this is not a live calendar check."
        ),
    }
