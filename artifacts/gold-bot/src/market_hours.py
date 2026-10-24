"""
Gold futures (COMEX GC=F) trading hours:
  - Trades ~23h/day Mon–Fri
  - Daily maintenance break: 5:00–6:00 PM ET (21:00–22:00 UTC summer / 22:00–23:00 UTC winter)
  - Weekend break: Friday 5:00 PM ET → Sunday 6:00 PM ET
Uses America/New_York for proper DST handling.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _now_et() -> datetime:
    return datetime.now(tz=ET)


def market_status(now: datetime | None = None) -> dict:
    """
    Returns:
      is_open     bool
      status_text str   — short phrase for display
      note        str   — optional extra context
    """
    now = _now_et() if now is None else now
    now = now.replace(tzinfo=ET) if now.tzinfo is None else now.astimezone(ET)
    wd  = now.weekday()   # Mon=0 … Sun=6
    h   = now.hour
    m   = now.minute

    # Weekend: closed Fri 17:00 ET through Sun 17:59 ET
    if wd == 4 and (h, m) >= (17, 0):
        return _closed("MARKET CLOSED", _next_weekly_open(now))
    if wd == 5:
        return _closed("MARKET CLOSED", _next_weekly_open(now))
    if wd == 6 and (h, m) < (18, 0):
        return _closed("MARKET CLOSED", _next_weekly_open(now))

    # Daily maintenance break: 17:00–18:00 ET
    if 17 <= h < 18:
        next_open = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return _closed("DAILY MAINTENANCE", next_open)

    return {
        "is_open":     True,
        "status_text": "MARKET OPEN",
        "note":        _session_label(h),
        "next_open_at": None,
        "next_open_label": None,
    }


def _next_weekly_open(now: datetime) -> datetime:
    """Return the next Sunday 18:00 ET opening across the weekend break."""
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 18:
        days_until_sunday = 7
    return (now + timedelta(days=days_until_sunday)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )


def _format_opening_time(value: datetime) -> str:
    """Format an exact, timezone-labelled opening time for Telegram users."""
    return (
        f"{value.strftime('%A, %B')} {value.day} at "
        f"{value.strftime('%I:%M %p').lstrip('0')} ET"
    )


def _closed(status: str, next_open: datetime) -> dict:
    label = _format_opening_time(next_open)
    return {
        "is_open": False,
        "status_text": status,
        "note": f"Reopens {label}",
        "next_open_at": next_open.isoformat(),
        "next_open_label": label,
    }


def market_closed_message(status: dict | None = None) -> str:
    """Detailed closed-market notice shared by commands and dashboard actions."""
    status = status or market_status()
    next_open = status.get("next_open_label") or status.get("note", "unknown time")
    lines = [
        "MARKET CLOSED  |  XAU/USD",
        "=" * 30,
        f"Status:  {status.get('status_text', 'MARKET CLOSED')}",
        f"Reopens: {next_open}",
        "=" * 30,
        "Trading and analysis tools are",
        "unavailable until the market opens.",
        "Gold futures: Sun 6 PM to Fri 5 PM ET",
        "Daily break: 5–6 PM ET",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def market_closed_alert(status: dict | None = None) -> str:
    """Short text that fits Telegram's callback alert limit."""
    status = status or market_status()
    next_open = status.get("next_open_label") or status.get("note", "unknown time")
    return f"MARKET CLOSED · Reopens {next_open}"[:200]


def _session_label(h: int) -> str:
    if 8 <= h < 16:
        return "New York session"
    if 18 <= h < 22 or 0 <= h < 5:
        return "Asian / Sydney session"
    if 3 <= h < 9:
        return "London session"
    return "Pre-market / overlap"


def market_status_line() -> str:
    """Single-line for card headers, e.g.  'MARKET OPEN  New York session'"""
    ms = market_status()
    return f"{ms['status_text']}  |  {ms['note']}"
