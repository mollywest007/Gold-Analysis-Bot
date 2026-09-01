"""
Tracks open trades and detects TP / SL hits.
Persists state to data/trades.json.
Fires WIN or LOSS result images via Telegram when a level is hit.
"""
import json
import logging
import os
import threading
import time
from typing import List, Dict, Any, Set
from uuid import uuid4

logger = logging.getLogger(__name__)

TRADES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trades.json")

# Per-timeframe expiry — higher TFs need more time to reach their targets.
# A flat 48h was too short for H4 trades, which routinely take 3–7 days.
_TF_MAX_AGE = {
    "M1":  12 * 3600,
    "M3":  18 * 3600,
    "M5":  24 * 3600,
    "M15": 48 * 3600,
    "M30": 72 * 3600,
    "H1":  5 * 24 * 3600,   # 5 days
    "H4":  10 * 24 * 3600,  # 10 days
    "D1":  20 * 24 * 3600,  # 20 days
    "W1":  120 * 24 * 3600, # 120 days
    "MN1": 540 * 24 * 3600, # 18 months
}
_DEFAULT_MAX_TRADE_AGE = 5 * 24 * 3600
_TERMINAL_STATUSES = {"sl_hit", "tp1_sl_hit", "tp3_hit"}
_STORE_LOCK = threading.RLock()


def _account_key(account_id: int | str | None) -> str | None:
    """Normalize Telegram chat IDs for exact owner comparisons."""
    if account_id is None:
        return None
    return str(int(account_id))


def _belongs_to_account(trade: Dict[str, Any], account_id: int | str | None) -> bool:
    """Return whether a record belongs to the requested account.

    Unowned records are intentionally not treated as belonging to any account.
    The old data format cannot prove which user created a trade, so exposing
    those records would reintroduce cross-account leakage.
    """
    key = _account_key(account_id)
    return key is None or str(trade.get("account_id") or "") == key


def is_active_trade(trade: Dict[str, Any]) -> bool:
    """Whether a trade still owns its timeframe.

    TP2 is terminal when no TP3 was configured.  With TP3 configured, TP2 is
    an intermediate milestone and the trade remains active until TP3 or SL.
    """
    status = trade.get("status")
    if status in ("open", "tp1_hit"):
        return True
    return (
        status == "tp2_hit"
        and bool(trade.get("tp3"))
        and not trade.get("tp3_hit")
    )


def _mark_terminal(
    trade: Dict[str, Any],
    status: str,
    close_reason: str,
) -> None:
    """Record an irreversible terminal transition and its result notification."""
    trade["status"] = status
    trade["closed_at"] = time.time()
    trade["close_reason"] = close_reason
    # The alert layer consumes this only after Telegram delivery succeeds.
    trade["result_notification_pending"] = True


def _load() -> List[Dict[str, Any]]:
    with _STORE_LOCK:
        try:
            with open(TRADES_PATH, "r") as f:
                data = json.load(f)
            trades = data.get("trades", []) if isinstance(data, dict) else []
            return trades if isinstance(trades, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []


def _save(trades: List[Dict[str, Any]]) -> None:
    with _STORE_LOCK:
        os.makedirs(os.path.dirname(TRADES_PATH), exist_ok=True)
        with open(TRADES_PATH, "w") as f:
            json.dump({"trades": trades}, f, indent=2)


def open_trade(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    timeframe: str,
    confidence: int,
    rr_ratio: float,
    tp3: float = None,
    atr: float = 0.0,
    mode: str = "",
    limit_entry: float = None,
    account_id: int | str | None = None,
) -> bool:
    trades = _load()

    # Reject malformed plans before they can be broadcast or persisted.  A
    # reversed target ladder makes the TP/SL checker report the wrong milestone
    # (for example TP2 before TP1) and is never a valid trade plan.
    try:
        entry = float(entry)
        sl = float(sl)
        tp1 = float(tp1)
        tp2 = float(tp2)
        tp3 = float(tp3) if tp3 is not None else None
        limit_entry = (
            float(limit_entry)
            if limit_entry is not None and float(limit_entry) > 0
            else None
        )
    except (TypeError, ValueError):
        logger.error(f"[{timeframe}] Trade open rejected — non-numeric plan.")
        return False

    if direction == "BUY":
        valid = sl < entry < tp1 < tp2 and (tp3 is None or tp2 < tp3)
    elif direction == "SELL":
        valid = sl > entry > tp1 > tp2 and (tp3 is None or tp2 > tp3)
    else:
        valid = False
    if not valid:
        logger.error(
            f"[{timeframe}] Trade open rejected — invalid {direction} levels: "
            f"entry={entry:.2f} sl={sl:.2f} tp1={tp1:.2f} tp2={tp2:.2f} tp3={tp3}"
        )
        return False

    # One timeframe represents one trade plan per account. Never replace or
    # stack an active trade just because a later analysis moved the entry.
    # Direct callers from older maintenance/test code may omit an owner. Such
    # records are stored as unassigned and are never returned by any
    # account-scoped query. All Telegram entry paths pass a real account_id.
    account_key = _account_key(account_id)

    for t in trades:
        if (
            is_active_trade(t)
            and t.get("timeframe") == timeframe
            and _belongs_to_account(t, account_key)
        ):
            logger.info(
                f"[{timeframe}] Trade open skipped — active {t.get('direction')} "
                f"trade {t.get('id')} already owns this timeframe for account {account_key}."
            )
            return False

    trade = {
        "id":          uuid4().hex,
        "account_id":  account_key,
        "direction":   direction,
        "entry":       entry,
        # Optional pullback/limit level shown in the alert. ``entry`` remains
        # the canonical market-entry basis for tracking until a broker fill
        # price is available.
        "limit_entry": limit_entry,
        "sl":          sl,
        "tp1":         tp1,
        "tp2":         tp2,
        "tp3":         tp3,
        "timeframe":   timeframe,
        "mode":        mode or "unknown",
        "confidence":  confidence,
        "rr_ratio":    rr_ratio,
        "opened_at":   time.time(),
        "status":      "open",
        "tp1_hit":     False,
        "tp2_hit":     False,
        "tp3_hit":     False,
    }
    trades.append(trade)
    _save(trades)
    tp3_str = f"  TP3={tp3:.2f}" if tp3 else ""
    logger.info(f"Trade opened: {direction} @ {entry:.2f}  SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}{tp3_str}")
    return True


def check_trades(current_price: float, recent_high: float = None,
                  recent_low: float = None,
                  tf_extremes: Dict[str, Any] = None,
                  account_id: int | str | None = None) -> List[Dict[str, Any]]:
    """
    Evaluate all open trades against current_price.

    recent_high/recent_low (deprecated): retained for call compatibility, but
    never used as exit evidence.  A caller must provide verified candle
    extremes in tf_extremes; otherwise only the live spot price is checked.

    tf_extremes (optional): { "M15": (high, low), "H1": (high, low), ... } —
    the high/low of each timeframe's current forming candle since the last
    check. Gold can wick through a TP/SL level for a few seconds and snap
    back before the next 30s poll samples current_price — checking only the
    single spot price would miss that touch entirely (or worse, report the
    wrong exit price once price has already moved on). Using each trade's
    own timeframe candle extremes lets a fast wick still register the touch,
    which is what would have actually filled on a real broker order sitting
    at that level.

    Returns a list of event dicts:
      {trade, event: 'TP1'|'TP2'|'SL', exit_price}
    """
    try:
        current_price = float(current_price)
    except (TypeError, ValueError):
        logger.warning("Trade check skipped — current price is invalid.")
        return []
    if current_price <= 0:
        logger.warning("Trade check skipped — current price is unavailable.")
        return []

    trades  = _load()
    account_key = _account_key(account_id)
    events  = []
    changed = False
    tf_extremes = tf_extremes or {}

    for t in trades:
        if account_key is not None and not _belongs_to_account(t, account_key):
            continue
        status = t.get("status", "")
        # Track: open, TP1 waiting for TP2, TP2 waiting for TP3 (when tp3 exists)
        if status == "tp2_hit" and not t.get("tp3"):
            continue   # TP2 was final target — truly closed, nothing to watch
        if status not in ("open", "tp1_hit", "tp2_hit"):
            continue

        age = time.time() - t.get("opened_at", 0)
        max_age = _TF_MAX_AGE.get(t.get("timeframe"), _DEFAULT_MAX_TRADE_AGE)
        if age > max_age:
            t["status"] = "expired"
            changed = True
            logger.info(f"Trade {t['id']} expired after {age/3600:.1f}h")
            # Return an EXPIRED event so alerts.py can release the signal lock
            # for this timeframe — without this, the TF stays permanently locked
            # and the next genuine entry signal is silently suppressed forever.
            events.append({"trade": t, "event": "EXPIRED", "exit_price": t.get("entry", 0)})
            continue

        d     = t["direction"]
        try:
            entry = float(t["entry"])
            sl = float(t["sl"])
            tp1 = float(t["tp1"])
            tp2 = float(t["tp2"])
        except (TypeError, ValueError):
            logger.warning(f"Trade {t.get('id')} skipped — invalid numeric levels.")
            continue
        if (
            d == "BUY" and not sl < entry
        ) or (
            d == "SELL" and not sl > entry
        ) or d not in ("BUY", "SELL"):
            logger.warning(
                f"Trade {t.get('id')} skipped — invalid {d} SL geometry "
                f"(entry={entry} sl={sl})."
            )
            continue

        tf_hi, tf_lo = tf_extremes.get(t.get("timeframe"), (None, None))
        try:
            if tf_hi is not None:
                tf_hi = float(tf_hi)
            if tf_lo is not None:
                tf_lo = float(tf_lo)
        except (TypeError, ValueError):
            tf_hi = tf_lo = None
        if (
            tf_hi is not None
            and tf_lo is not None
            and (tf_hi <= 0 or tf_lo <= 0 or tf_lo > tf_hi)
        ):
            logger.warning(
                f"Trade {t.get('id')} skipped candle extremes — "
                f"invalid range hi={tf_hi} lo={tf_lo}."
            )
            tf_hi = tf_lo = None

        # SL detection uses verified candle extremes when supplied (catching
        # brief wicks through the stop that retraced before the next spot
        # poll).  Do not fall back to caller-provided historical highs/lows:
        # those values have no freshness or post-entry guarantee and can
        # falsely close a still-live trade.  With no verified candle, use the
        # current spot snapshot only.
        if tf_hi is not None and tf_lo is not None:
            sl_hi = max(tf_hi, current_price)
            sl_lo = min(tf_lo, current_price)
        else:
            sl_hi = current_price
            sl_lo = current_price

        # TP detection uses post-entry candle extremes (same tf_extremes dict
        # that SL uses). tf_extremes is pre-filtered in alerts.py to include
        # only candles that opened AFTER the trade was placed, so there is no
        # risk of a pre-entry wick triggering a false win.  Using candle
        # extremes symmetrically with SL means a genuine wick to TP between
        # two 15-second polls is caught rather than silently missed.
        # When no post-entry candle exists yet (new trade, fallback path in
        # alerts.py sets tf_extremes[tf] = (current_price, current_price)),
        # this collapses back to current_price — safe.
        if tf_hi is not None:
            tp_hi = max(tf_hi, current_price)
            tp_lo = min(tf_lo, current_price)
        else:
            tp_hi = current_price
            tp_lo = current_price

        tp3_val = t.get("tp3") or 0.0

        if d == "BUY":
            sl_hit    = sl_lo <= sl
            tp1_hit   = tp_hi >= tp1
            tp2_hit   = tp_hi >= tp2
            tp3_hit   = bool(tp3_val) and tp_hi >= tp3_val
            # Exit price = the level itself (what would actually have filled),
            # not current_price, since a wick may have already retraced.
            sl_exit   = sl
            tp1_exit  = tp1
            tp2_exit  = tp2
            tp3_exit  = tp3_val
        else:  # SELL
            sl_hit    = sl_hi >= sl
            tp1_hit   = tp_lo <= tp1
            tp2_hit   = tp_lo <= tp2
            tp3_hit   = bool(tp3_val) and tp_lo <= tp3_val
            sl_exit   = sl
            tp1_exit  = tp1
            tp2_exit  = tp2
            tp3_exit  = tp3_val

        # ── Break-even SL after TP1 ───────────────────────────────────────────
        # Standard risk management: once TP1 is captured, entry becomes the
        # effective SL. If price retraces back through entry before TP2 is
        # hit, close at break-even rather than riding to the original SL.
        # This prevents the bot from holding a losing trade and sending
        # optimistic TP2/TP3 updates while the position is underwater.
        if t.get("tp1_hit") and not t.get("tp2_hit") and not sl_hit:
            if d == "BUY":
                be_hit = sl_lo <= entry
            else:
                be_hit = sl_hi >= entry
            if be_hit:
                _mark_terminal(t, "tp1_sl_hit", "break_even_stop")
                changed = True
                events.append({"trade": t, "event": "TP1_SL", "exit_price": entry})
                logger.info(f"Trade {t['id']} break-even SL triggered after TP1 @ {entry:.2f}")
                continue

        if sl_hit:
            # If TP1 was already captured, mark distinctly so history shows TP1→SL
            if t.get("tp1_hit"):
                _mark_terminal(t, "tp1_sl_hit", "stop_loss")
                changed = True
                events.append({"trade": t, "event": "TP1_SL", "exit_price": sl_exit})
                logger.info(f"Trade {t['id']} SL hit after TP1 partial @ {sl_exit:.2f}")
            else:
                _mark_terminal(t, "sl_hit", "stop_loss")
                changed = True
                events.append({"trade": t, "event": "SL", "exit_price": sl_exit})
                logger.info(f"Trade {t['id']} SL hit @ {sl_exit:.2f}")

        elif tp3_hit and tp3_val and not t.get("tp3_hit"):
            t["tp3_hit"] = True
            t["tp2_hit"] = True
            t["tp1_hit"] = True
            _mark_terminal(t, "tp3_hit", "take_profit")
            changed = True
            events.append({"trade": t, "event": "TP3", "exit_price": tp3_exit})
            logger.info(f"Trade {t['id']} TP3 hit @ {tp3_exit:.2f}")

        elif tp2_hit and not t.get("tp2_hit"):
            t["tp2_hit"] = True
            t["tp1_hit"] = True   # TP1 implicitly cleared if TP2 is reached
            t["status"]  = "tp2_hit"
            changed = True
            events.append({"trade": t, "event": "TP2", "exit_price": tp2_exit})
            logger.info(f"Trade {t['id']} TP2 hit @ {tp2_exit:.2f}")

        elif tp1_hit and not t.get("tp1_hit"):
            t["tp1_hit"] = True
            t["status"]  = "tp1_hit"   # record partial win; trade stays tracked for TP2/TP3
            changed = True
            events.append({"trade": t, "event": "TP1", "exit_price": tp1_exit})
            logger.info(f"Trade {t['id']} TP1 hit @ {tp1_exit:.2f}")

    if changed:
        _save(trades)

    return events


def open_trade_count(account_id: int | str | None = None) -> int:
    trades = _load()
    count = 0
    for t in trades:
        if _belongs_to_account(t, account_id) and is_active_trade(t):
            count += 1
    return count


def get_all_trades(account_id: int | str | None = None) -> List[Dict[str, Any]]:
    """Return account-owned trades, newest first.

    Omitting account_id is reserved for process-level maintenance and migration
    code. Telegram-facing code must always pass the current chat ID.
    """
    trades = [t for t in _load() if _belongs_to_account(t, account_id)]
    return sorted(trades, key=lambda t: t.get("opened_at", 0), reverse=True)


def get_trade_by_id(
    trade_id: str, account_id: int | str | None = None
) -> Dict[str, Any] | None:
    """Return the current persisted record for one trade."""
    trade_id = str(trade_id or "")
    if not trade_id:
        return None
    return next(
        (
            trade for trade in _load()
            if str(trade.get("id") or "") == trade_id
            and _belongs_to_account(trade, account_id)
        ),
        None,
    )


def mark_result_notification_sent(
    trade_id: str, account_id: int | str | None = None
) -> bool:
    """Consume a terminal result notification only after delivery succeeds."""
    trades = _load()
    trade_id = str(trade_id or "")
    account_key = _account_key(account_id)
    for trade in trades:
        if (
            str(trade.get("id") or "") != trade_id
            or (account_key is not None and not _belongs_to_account(trade, account_key))
        ):
            continue
        if is_active_trade(trade) or trade.get("status") not in _TERMINAL_STATUSES:
            return False
        if not trade.get("result_notification_pending"):
            return False
        trade["result_notification_pending"] = False
        trade["result_notification_sent_at"] = time.time()
        _save(trades)
        return True
    return False


def mark_cooldown_notification_pending(
    trade_id: str,
    cooldown_until: float,
    cooldown_duration: float,
    account_id: int | str | None = None,
) -> bool:
    """Record that a confirmed SL now needs a cooldown notification."""
    trades = _load()
    trade_id = str(trade_id or "")
    account_key = _account_key(account_id)
    try:
        cooldown_until = float(cooldown_until)
        cooldown_duration = float(cooldown_duration)
    except (TypeError, ValueError):
        return False
    for trade in trades:
        if (
            str(trade.get("id") or "") != trade_id
            or (account_key is not None and not _belongs_to_account(trade, account_key))
        ):
            continue
        if (
            is_active_trade(trade)
            or trade.get("status") not in {"sl_hit", "tp1_sl_hit"}
            or trade.get("cooldown_notification_sent_at")
        ):
            return False
        trade["cooldown_notification_pending"] = True
        trade["cooldown_until"] = cooldown_until
        trade["cooldown_duration_seconds"] = cooldown_duration
        _save(trades)
        return True
    return False


def mark_cooldown_notification_sent(
    trade_id: str, account_id: int | str | None = None
) -> bool:
    """Consume a cooldown notification only after Telegram delivery succeeds."""
    trades = _load()
    trade_id = str(trade_id or "")
    account_key = _account_key(account_id)
    for trade in trades:
        if (
            str(trade.get("id") or "") != trade_id
            or (account_key is not None and not _belongs_to_account(trade, account_key))
        ):
            continue
        if (
            is_active_trade(trade)
            or trade.get("status") not in {"sl_hit", "tp1_sl_hit"}
            or not trade.get("cooldown_notification_pending")
        ):
            return False
        trade["cooldown_notification_pending"] = False
        trade["cooldown_notification_sent_at"] = time.time()
        _save(trades)
        return True
    return False


def get_pending_cooldown_notifications(
    account_id: int | str | None = None,
) -> List[Dict[str, Any]]:
    """Return confirmed SL closures whose cooldown notice needs delivery."""
    return [
        trade for trade in get_all_trades(account_id)
        if trade.get("cooldown_notification_pending")
        and trade.get("status") in {"sl_hit", "tp1_sl_hit"}
        and not is_active_trade(trade)
    ]


def get_pending_result_notifications(
    account_id: int | str | None = None,
) -> List[Dict[str, Any]]:
    """Return newly closed trades whose result card still needs delivery."""
    return [
        trade for trade in get_all_trades(account_id)
        if trade.get("result_notification_pending")
        and trade.get("status") in _TERMINAL_STATUSES
        and not is_active_trade(trade)
    ]


def get_active_trades(account_id: int | str | None = None) -> List[Dict[str, Any]]:
    """Return every trade that still owns its timeframe.

    Keep this query next to ``is_active_trade`` so the scanner, /active panel,
    reminders, and chart context cannot disagree about whether TP2 is terminal
    or still being managed toward TP3.
    """
    return [t for t in get_all_trades(account_id) if is_active_trade(t)]


def get_active_trades_for_account(
    account_id: int,
    mode: str = None,
    timeframe: str = None,
) -> List[Dict[str, Any]]:
    """Return active plans for one Telegram account.

    Records created before account isolation have no owner, so those legacy
    records are included only when their frozen mode and timeframe match the
    requesting account's profile.
    """
    # Legacy records without an owner are deliberately excluded. Matching
    # mode/timeframe is not evidence of ownership and would leak positions.
    return get_active_trades(account_id)


def get_stats(account_id: int | str | None = None) -> Dict[str, Any]:
    """Return win/loss/open counts and win rate for one account."""
    trades = get_all_trades(account_id)
    wins   = sum(1 for t in trades if t.get("status") in ("tp1_hit", "tp2_hit", "tp3_hit", "tp1_sl_hit"))
    losses = sum(1 for t in trades if t.get("status") == "sl_hit")
    open_  = sum(1 for t in trades if is_active_trade(t))
    expired = sum(1 for t in trades if t.get("status") in ("expired", "replaced"))
    total_closed = wins + losses
    win_rate = round((wins / total_closed) * 100) if total_closed > 0 else 0
    return {
        "wins": wins,
        "losses": losses,
        "open": open_,
        "expired": expired,
        "total": len(trades),
        "total_closed": total_closed,
        "win_rate": win_rate,
    }
