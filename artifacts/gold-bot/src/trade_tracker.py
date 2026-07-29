"""
Tracks open trades and detects TP / SL hits.
Persists state to data/trades.json.
Fires WIN or LOSS result images via Telegram when a level is hit.
"""
import json
import logging
import os
import time
from typing import List, Dict, Any, Set

logger = logging.getLogger(__name__)

TRADES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trades.json")

# Per-timeframe expiry — higher TFs need more time to reach their targets.
# A flat 48h was too short for H4 trades, which routinely take 3–7 days.
_TF_MAX_AGE = {
    "M5":  24 * 3600,
    "M15": 48 * 3600,
    "M30": 72 * 3600,
    "H1":  5 * 24 * 3600,   # 5 days
    "H4":  10 * 24 * 3600,  # 10 days
    "D1":  20 * 24 * 3600,  # 20 days
}
_DEFAULT_MAX_TRADE_AGE = 5 * 24 * 3600  # 5 days fallback


def _load() -> List[Dict[str, Any]]:
    try:
        with open(TRADES_PATH, "r") as f:
            return json.load(f).get("trades", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(trades: List[Dict[str, Any]]) -> None:
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
) -> None:
    trades = _load()

    # ── Duplicate-entry guard ──────────────────────────────────────────────────
    # If there is already an open trade on this TF in the SAME direction,
    # only replace it when the new entry is meaningfully far from the original.
    # • When ATR is available (> 0): gap must exceed 0.5 × ATR.
    # • When ATR is 0 (fallback): block any re-entry opened within 2 candle
    #   periods — this prevents the 15-second scanner from spamming trades when
    #   the analysis object doesn't expose an ATR attribute.
    # A direction FLIP always replaces regardless (handled implicitly: if the
    # existing trade is the opposite direction the filter below doesn't match).
    _TF_PERIOD_SECS = {
        "M5": 300, "M15": 900, "M30": 1800,
        "H1": 3600, "H4": 14400, "D1": 86400,
    }
    _min_gap_secs = 2 * _TF_PERIOD_SECS.get(timeframe, 3600)
    for t in trades:
        if (
            t.get("status") in ("open", "tp1_hit", "tp2_hit")
            and t.get("timeframe") == timeframe
            and t.get("direction") == direction
        ):
            existing_entry = t.get("entry", 0.0)
            gap = abs(entry - existing_entry)
            if atr > 0:
                # ATR available — use price-distance threshold
                if gap < 0.5 * atr:
                    logger.info(
                        f"[{timeframe}] Trade replacement skipped — new entry {entry:.2f} is "
                        f"only {gap:.2f} pts from existing {existing_entry:.2f} "
                        f"(<0.5×ATR={0.5 * atr:.2f}). Keeping original trade."
                    )
                    return
            else:
                # ATR unavailable — fall back to time-based guard
                age = time.time() - t.get("opened_at", 0)
                if age < _min_gap_secs:
                    logger.info(
                        f"[{timeframe}] Trade replacement skipped — existing {direction} "
                        f"@ {existing_entry:.2f} opened only {age:.0f}s ago "
                        f"(min gap {_min_gap_secs}s). ATR unavailable."
                    )
                    return

    # Close any existing open trade on this timeframe by marking it "replaced"
    # rather than deleting it, so it still appears in /history.
    for t in trades:
        if t.get("status") in ("open", "tp1_hit", "tp2_hit") and t.get("timeframe") == timeframe:
            t["status"] = "replaced"
            logger.info(
                f"Trade {t['id']} ({t.get('direction')} @ {t.get('entry', 0):.2f}) "
                f"marked REPLACED — new {direction} setup on {timeframe}."
            )
    trade = {
        "id":          str(int(time.time() * 1000)),  # millisecond precision avoids duplicate IDs
        "direction":   direction,
        "entry":       entry,
        "sl":          sl,
        "tp1":         tp1,
        "tp2":         tp2,
        "tp3":         tp3,
        "timeframe":   timeframe,
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


def check_trades(current_price: float, recent_high: float = None,
                  recent_low: float = None,
                  tf_extremes: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Evaluate all open trades against current_price.

    recent_high/recent_low (optional): fallback high/low to use for any
    trade whose own timeframe isn't present in tf_extremes.

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
    trades  = _load()
    events  = []
    changed = False
    tf_extremes = tf_extremes or {}

    for t in trades:
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

        d   = t["direction"]
        sl  = t["sl"]
        tp1 = t["tp1"]
        tp2 = t["tp2"]

        tf_hi, tf_lo = tf_extremes.get(t.get("timeframe"), (None, None))

        # SL detection uses candle extremes (catches brief wicks through the
        # stop that already retraced before the next spot-price poll).
        sl_hi = tf_hi if tf_hi is not None else (recent_high if recent_high is not None else current_price)
        sl_lo = tf_lo if tf_lo is not None else (recent_low  if recent_low  is not None else current_price)
        sl_hi = max(sl_hi, current_price)
        sl_lo = min(sl_lo, current_price)

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
                t["status"] = "tp1_sl_hit"
                changed = True
                events.append({"trade": t, "event": "TP1_SL", "exit_price": entry})
                logger.info(f"Trade {t['id']} break-even SL triggered after TP1 @ {entry:.2f}")
                continue

        if sl_hit:
            # If TP1 was already captured, mark distinctly so history shows TP1→SL
            if t.get("tp1_hit"):
                t["status"] = "tp1_sl_hit"
                changed = True
                events.append({"trade": t, "event": "TP1_SL", "exit_price": sl_exit})
                logger.info(f"Trade {t['id']} SL hit after TP1 partial @ {sl_exit:.2f}")
            else:
                t["status"] = "sl_hit"
                changed = True
                events.append({"trade": t, "event": "SL", "exit_price": sl_exit})
                logger.info(f"Trade {t['id']} SL hit @ {sl_exit:.2f}")

        elif tp3_hit and tp3_val and not t.get("tp3_hit"):
            t["tp3_hit"] = True
            t["tp2_hit"] = True
            t["tp1_hit"] = True
            t["status"]  = "tp3_hit"
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


def open_trade_count() -> int:
    trades = _load()
    count = 0
    for t in trades:
        s = t.get("status")
        if s in ("open", "tp1_hit"):
            count += 1
        elif s == "tp2_hit" and t.get("tp3") and not t.get("tp3_hit"):
            count += 1   # TP2 hit but still watching for TP3
    return count


def get_all_trades() -> List[Dict[str, Any]]:
    """Return all trades, newest first."""
    trades = _load()
    return sorted(trades, key=lambda t: t.get("opened_at", 0), reverse=True)


def get_stats() -> Dict[str, Any]:
    """Return win/loss/open counts and win rate across all closed trades."""
    trades = _load()
    wins   = sum(1 for t in trades if t.get("status") in ("tp1_hit", "tp2_hit", "tp3_hit", "tp1_sl_hit"))
    losses = sum(1 for t in trades if t.get("status") == "sl_hit")
    open_  = sum(1 for t in trades if t.get("status") == "open")
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
