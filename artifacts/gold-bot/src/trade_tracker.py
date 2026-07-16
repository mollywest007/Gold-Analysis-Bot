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
MAX_TRADE_AGE = 48 * 3600   # auto-close after 48h


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
) -> None:
    trades = _load()
    # Only one open trade at a time per direction — replace if same direction exists
    trades = [t for t in trades if t.get("status") != "open" or t.get("direction") != direction]
    trade = {
        "id":          str(int(time.time())),
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
        # Continue tracking open trades AND trades where TP1 was hit but TP2 not yet reached
        if status not in ("open", "tp1_hit"):
            continue

        age = time.time() - t.get("opened_at", 0)
        if age > MAX_TRADE_AGE:
            t["status"] = "expired"
            changed = True
            logger.info(f"Trade {t['id']} expired after {age/3600:.1f}h")
            continue

        d   = t["direction"]
        sl  = t["sl"]
        tp1 = t["tp1"]
        tp2 = t["tp2"]

        tf_hi, tf_lo = tf_extremes.get(t.get("timeframe"), (None, None))
        hi = tf_hi if tf_hi is not None else (recent_high if recent_high is not None else current_price)
        lo = tf_lo if tf_lo is not None else (recent_low  if recent_low  is not None else current_price)
        # Never let the candle-derived extreme be less informative than the
        # live spot price itself (covers the gap between candle close and now).
        hi = max(hi, current_price)
        lo = min(lo, current_price)

        if d == "BUY":
            sl_hit    = lo <= sl
            tp1_hit   = hi >= tp1
            tp2_hit   = hi >= tp2
            # Exit price = the level itself (what would actually have filled),
            # not current_price, since a wick may have already retraced.
            sl_exit   = sl
            tp1_exit  = tp1
            tp2_exit  = tp2
        else:  # SELL
            sl_hit    = hi >= sl
            tp1_hit   = lo <= tp1
            tp2_hit   = lo <= tp2
            sl_exit   = sl
            tp1_exit  = tp1
            tp2_exit  = tp2

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

        elif tp2_hit and not t.get("tp2_hit"):
            t["tp2_hit"] = True
            t["tp1_hit"] = True   # TP1 implicitly cleared if TP2 is reached
            t["status"]  = "tp2_hit"
            changed = True
            events.append({"trade": t, "event": "TP2", "exit_price": tp2_exit})
            logger.info(f"Trade {t['id']} TP2 hit @ {tp2_exit:.2f}")

        elif tp1_hit and not t.get("tp1_hit"):
            t["tp1_hit"] = True
            t["status"]  = "tp1_hit"   # record partial win; trade stays tracked for TP2
            changed = True
            events.append({"trade": t, "event": "TP1", "exit_price": tp1_exit})
            logger.info(f"Trade {t['id']} TP1 hit @ {tp1_exit:.2f}")

    if changed:
        _save(trades)

    return events


def open_trade_count() -> int:
    return sum(1 for t in _load() if t.get("status") == "open")


def get_all_trades() -> List[Dict[str, Any]]:
    """Return all trades, newest first."""
    trades = _load()
    return sorted(trades, key=lambda t: t.get("opened_at", 0), reverse=True)


def get_stats() -> Dict[str, Any]:
    """Return win/loss/open counts and win rate across all closed trades."""
    trades = _load()
    wins   = sum(1 for t in trades if t.get("status") in ("tp1_hit", "tp2_hit", "tp1_sl_hit"))
    losses = sum(1 for t in trades if t.get("status") == "sl_hit")
    open_  = sum(1 for t in trades if t.get("status") == "open")
    expired = sum(1 for t in trades if t.get("status") == "expired")
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
