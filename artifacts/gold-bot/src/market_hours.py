"""
Gold futures (COMEX GC=F) trading hours:
  - Trades ~23h/day Mon–Fri
  - Daily maintenance break: 5:00–6:00 PM ET (21:00–22:00 UTC summer / 22:00–23:00 UTC winter)
  - Weekend break: Friday 5:00 PM ET → Sunday 6:00 PM ET
Uses America/New_York for proper DST handling.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _now_et() -> datetime:
    return datetime.now(tz=ET)


def market_status() -> dict:
    """
    Returns:
      is_open     bool
      status_text str   — short phrase for display
      note        str   — optional extra context
    """
    now = _now_et()
    wd  = now.weekday()   # Mon=0 … Sun=6
    h   = now.hour
    m   = now.minute

    # Weekend: closed Fri 17:00 ET through Sun 17:59 ET
    if wd == 4 and (h, m) >= (17, 0):
        return _closed("MARKET CLOSED", "Re-opens Sunday 6:00 PM ET")
    if wd == 5:
        return _closed("MARKET CLOSED", "Re-opens Sunday 6:00 PM ET")
    if wd == 6 and (h, m) < (18, 0):
        return _closed("MARKET CLOSED", f"Opens in ~{_opens_in(now)}h")

    # Daily maintenance break: 17:00–18:00 ET
    if 17 <= h < 18:
        mins_left = 60 - m
        return _closed("DAILY MAINTENANCE", f"Reopens in {mins_left} min")

    return {
        "is_open":     True,
        "status_text": "MARKET OPEN",
        "note":        _session_label(h),
    }


def _opens_in(now: datetime) -> int:
    """Hours until Sunday 18:00 ET."""
    wd  = now.weekday()
    h   = now.hour
    if wd == 6:
        return max(0, 18 - h)
    # Saturday
    return (18 - h) + 24


def _session_label(h: int) -> str:
    if 8 <= h < 16:
        return "New York session"
    if 18 <= h < 22 or 0 <= h < 5:
        return "Asian / Sydney session"
    if 3 <= h < 9:
        return "London session"
    return "Pre-market / overlap"


def _closed(status: str, note: str) -> dict:
    return {"is_open": False, "status_text": status, "note": note}


def market_status_line() -> str:
    """Single-line for card headers, e.g.  'MARKET OPEN  New York session'"""
    ms = market_status()
    return f"{ms['status_text']}  |  {ms['note']}"
