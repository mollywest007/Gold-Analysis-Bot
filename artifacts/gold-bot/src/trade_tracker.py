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


def check_trades(current_price: float) -> List[Dict[str, Any]]:
    """
    Evaluate all open trades against current_price.
    Returns a list of event dicts:
      {trade, event: 'TP1'|'TP2'|'SL', exit_price}
    """
    trades  = _load()
    events  = []
    changed = False

    for t in trades:
        if t.get("status") != "open":
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

        if d == "BUY":
            sl_hit  = current_price <= sl
            tp1_hit = current_price >= tp1
            tp2_hit = current_price >= tp2
        else:  # SELL
            sl_hit  = current_price >= sl
            tp1_hit = current_price <= tp1
            tp2_hit = current_price <= tp2

        if sl_hit:
            t["status"] = "sl_hit"
            changed = True
            events.append({"trade": t, "event": "SL", "exit_price": current_price})
            logger.info(f"Trade {t['id']} SL hit @ {current_price:.2f}")

        elif tp2_hit and not t.get("tp2_hit"):
            t["tp2_hit"] = True
            t["status"]  = "tp2_hit"
            changed = True
            events.append({"trade": t, "event": "TP2", "exit_price": current_price})
            logger.info(f"Trade {t['id']} TP2 hit @ {current_price:.2f}")

        elif tp1_hit and not t.get("tp1_hit"):
            t["tp1_hit"] = True
            changed = True
            events.append({"trade": t, "event": "TP1", "exit_price": current_price})
            logger.info(f"Trade {t['id']} TP1 hit @ {current_price:.2f}")

    if changed:
        _save(trades)

    return events


def open_trade_count() -> int:
    return sum(1 for t in _load() if t.get("status") == "open")
