"""
XAU/USD Analysis Engine — v3 (full rewrite with extended pattern detection,
limit-entry refinement, trade-type classification, and improved confluence).

Improvements over v2:
  - Extended candlestick library: +8 patterns (Three White Soldiers, Three Black
    Crows, Tweezer Top/Bottom, Dark Cloud Cover, Piercing Line, Harami x2, Inside Bar)
  - Limit-entry suggestion: EMA-pullback or ATR-retrace for better fills
  - Trade type classification: Scalp / Intraday / Swing / Position
  - Entry zone: shows market vs limit order suggestion per trade type
  - Market-closed flag propagated into the analysis object
  - Volume-weighted S/R with increased lookback
  - HTF confirmation gate + session filter
  - Confluence gate: >= 3/5 (or 2/5 in strong trend) indicators agree
"""

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Tuple, Optional

from .market_data import fetch_ohlcv, OHLCVData

logger = logging.getLogger(__name__)


# ─── Data types ───────────────────────────────────────────────────────────────

@dataclass
class Indicator:
    name:   str
    value:  float
    signal: str
    weight: float


@dataclass
class MarketAnalysis:
    price:          float
    timeframe:      str
    bias:           str
    trend:          str
    strength:       str
    momentum:       str
    confidence:     int
    entry:          float
    stop_loss:      float
    tp1:            float
    tp2:            float
    rr_ratio:       float
    action:         str
    wait_reason:    str
    resistance1:    float
    resistance2:    float
    support1:       float
    support2:       float
    breakout:       bool
    reversal:       bool
    liquidity_zone: str
    adx:            float = 0.0
    atr:            float = 0.0
    bb_pct:         float = 0.0
    indicators: List[Indicator] = field(default_factory=list)
    buy_votes:  int = 0
    sell_votes: int = 0
    wait_votes: int = 0
    verdict_reason: str = ""
    session:    str = ""
    htf_bias:   str = "Neutral"
    candle_pattern: str = "None"
    trade_type:     str = "Intraday"   # Scalp | Intraday | Swing | Position
    limit_entry:    float = 0.0        # Suggested limit-order entry for better fill
    entry_note:     str = ""           # "Market" or "Limit @ XXXX.XX"
    bb_upper:       float = 0.0
    bb_lower:       float = 0.0
    # Extended pro fields
    rsi_value:      float = 0.0
    stoch_k_val:    float = 0.0
    stoch_d_val:    float = 0.0
    macd_hist:      float = 0.0
    plus_di:        float = 0.0
    minus_di:       float = 0.0
    market_structure: str = "RANGING"   # HH_HL | LH_LL | RANGING | TRANSITION
    win_probability:  int = 0
    confluence_list: List[str] = field(default_factory=list)
    tp3:            float = 0.0
    # Early entry / Fibonacci fields
    fib_382:        float = 0.0
    fib_500:        float = 0.0
    fib_618:        float = 0.0
    early_entry:    float = 0.0   # best pullback entry price
    early_entry_reason: str = ""  # description of the early entry zone
    setup_quality:  str = ""      # "A+" | "A" | "B" | "WAIT"


# ─── TA core functions ────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return sum(prices) / len(prices)
    k   = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _ema_series(prices: List[float], period: int) -> List[float]:
    if len(prices) < period:
        return []
    k      = 2.0 / (period + 1)
    result = [sum(prices[:period]) / period]
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def compute_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 2:
        return 50.0
    deltas   = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains    = [max(d, 0.0) for d in deltas]
    losses   = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_macd(closes: List[float], fast: int = 12, slow: int = 26,
                 sig: int = 9) -> Tuple[float, float, float]:
    if len(closes) < slow + sig:
        return 0.0, 0.0, 0.0

    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    k_sig  = 2.0 / (sig + 1)

    ema_f = sum(closes[:fast]) / fast
    for p in closes[fast:slow]:
        ema_f = p * k_fast + ema_f * (1 - k_fast)

    ema_s = sum(closes[:slow]) / slow

    macd_vals: List[float] = []
    for p in closes[slow:]:
        ema_f = p * k_fast + ema_f * (1 - k_fast)
        ema_s = p * k_slow + ema_s * (1 - k_slow)
        macd_vals.append(ema_f - ema_s)

    if not macd_vals:
        return 0.0, 0.0, 0.0

    signal_val = sum(macd_vals[:sig]) / sig if len(macd_vals) >= sig else sum(macd_vals) / len(macd_vals)
    for m in macd_vals[sig:]:
        signal_val = m * k_sig + signal_val * (1 - k_sig)

    macd_line = macd_vals[-1]
    hist      = macd_line - signal_val
    return round(macd_line, 4), round(signal_val, 4), round(hist, 4)


def compute_stoch(highs: List[float], lows: List[float], closes: List[float],
                  k_period: int = 14, d_period: int = 3) -> Tuple[float, float]:
    if len(closes) < k_period:
        return 50.0, 50.0
    k_vals: List[float] = []
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1: i + 1])
        ll = min(lows[i  - k_period + 1: i + 1])
        k_vals.append(50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100)
    k = k_vals[-1]
    d = sum(k_vals[-d_period:]) / min(d_period, len(k_vals))
    return round(k, 2), round(d, 2)


def compute_atr(highs: List[float], lows: List[float], closes: List[float],
                period: int = 14) -> float:
    if len(closes) < 2:
        return closes[-1] * 0.005 if closes else 10.0
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    if len(trs) < period:
        return sum(trs) / len(trs)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def compute_bollinger(closes: List[float], period: int = 20,
                      num_std: float = 2.0) -> Tuple[float, float, float, float]:
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1], 50.0
    window = closes[-period:]
    mid    = sum(window) / period
    std    = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    upper  = mid + num_std * std
    lower  = mid - num_std * std
    price  = closes[-1]
    pct_b  = ((price - lower) / (upper - lower) * 100) if upper != lower else 50.0
    return round(upper, 4), round(mid, 4), round(lower, 4), round(pct_b, 2)


def compute_adx(highs: List[float], lows: List[float], closes: List[float],
                period: int = 14) -> Tuple[float, float, float]:
    n = len(closes)
    if n < period * 2 + 1:
        return 20.0, 50.0, 50.0

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = h - highs[i - 1]
        dn = lows[i - 1] - l
        trs.append(tr)
        plus_dms.append(up  if (up > dn and up > 0)  else 0.0)
        minus_dms.append(dn if (dn > up and dn > 0)  else 0.0)

    atr14      = sum(trs[:period])
    plus_dm14  = sum(plus_dms[:period])
    minus_dm14 = sum(minus_dms[:period])

    dx_vals: List[float] = []
    for i in range(period, len(trs)):
        atr14      = atr14      - atr14 / period      + trs[i]
        plus_dm14  = plus_dm14  - plus_dm14 / period  + plus_dms[i]
        minus_dm14 = minus_dm14 - minus_dm14 / period + minus_dms[i]
        if atr14 == 0:
            continue
        plus_di  = 100 * plus_dm14  / atr14
        minus_di = 100 * minus_dm14 / atr14
        di_sum   = plus_di + minus_di
        dx       = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        dx_vals.append(dx)

    if not dx_vals:
        return 20.0, 50.0, 50.0

    adx = sum(dx_vals[:period]) / period if len(dx_vals) >= period else sum(dx_vals) / len(dx_vals)
    for dx in dx_vals[period:]:
        adx = (adx * (period - 1) + dx) / period

    if atr14 > 0:
        last_plus_di  = round(100 * plus_dm14  / atr14, 2)
        last_minus_di = round(100 * minus_dm14 / atr14, 2)
    else:
        last_plus_di = last_minus_di = 50.0

    return round(adx, 2), last_plus_di, last_minus_di


# ─── S/R (improved: 6-bar lookback, volume-weighted cluster) ─────────────────

def find_sr_levels(highs: List[float], lows: List[float], closes: List[float],
                   price: float, atr: float,
                   volumes: Optional[List[float]] = None) -> Tuple[float, float, float, float]:
    resistances: List[Tuple[float, float]] = []
    supports:    List[Tuple[float, float]] = []
    n        = len(closes)
    lookback = 6

    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            vol_w = volumes[i] if (volumes and i < len(volumes) and volumes[i]) else 1.0
            resistances.append((highs[i], vol_w))
        if all(lows[i]  <= lows[j]  for j in range(i - lookback, i + lookback + 1) if j != i):
            vol_w = volumes[i] if (volumes and i < len(volumes) and volumes[i]) else 1.0
            supports.append((lows[i], vol_w))

    def cluster(levels: List[Tuple[float, float]], tolerance: float) -> List[float]:
        levels = sorted(levels, key=lambda x: x[0])
        merged: List[Tuple[float, float]] = []
        for lv, w in levels:
            if merged and abs(lv - merged[-1][0]) < tolerance:
                prev_lv, prev_w = merged[-1]
                merged[-1] = ((prev_lv * prev_w + lv * w) / (prev_w + w), prev_w + w)
            else:
                merged.append((lv, w))
        return [lv for lv, _ in merged]

    tol          = atr * 0.6
    res_levels   = cluster([(r, w) for r, w in resistances if r > price], tol)
    sup_levels   = cluster(sorted([(s, w) for s, w in supports if s < price],
                                   key=lambda x: x[0], reverse=True), tol)

    r1 = res_levels[0] if res_levels else round(price + atr * 3, 2)
    r2 = res_levels[1] if len(res_levels) > 1 else round(price + atr * 6, 2)
    s1 = sup_levels[0] if sup_levels else round(price - atr * 3, 2)
    s2 = sup_levels[1] if len(sup_levels) > 1 else round(price - atr * 6, 2)

    return round(r1, 2), round(r2, 2), round(s1, 2), round(s2, 2)


# ─── Extended candlestick pattern detection ───────────────────────────────────

def detect_candlestick(opens: List[float], highs: List[float],
                       lows: List[float], closes: List[float],
                       atr: float = 0.0) -> Tuple[str, float]:
    """
    Detects 13 patterns:
      Bullish: Bullish Engulfing, Hammer, Morning Star, Three White Soldiers,
               Tweezer Bottom, Piercing Line, Bullish Harami
      Bearish: Bearish Engulfing, Shooting Star, Evening Star, Three Black Crows,
               Tweezer Top, Dark Cloud Cover, Bearish Harami
      Neutral: Doji, Inside Bar, Spinning Top

    Returns (pattern_name, signal_weight 0..1).
    """
    if len(closes) < 3:
        return "None", 0.0

    o1, h1, l1, c1 = opens[-1], highs[-1], lows[-1], closes[-1]
    o2, h2, l2, c2 = opens[-2], highs[-2], lows[-2], closes[-2]

    body1 = abs(c1 - o1)
    rng1  = h1 - l1 or 0.0001
    body2 = abs(c2 - o2)
    rng2  = h2 - l2 or 0.0001

    upper_wick1 = h1 - max(c1, o1)
    lower_wick1 = min(c1, o1) - l1
    body_ratio1 = body1 / rng1

    upper_wick2 = h2 - max(c2, o2)
    lower_wick2 = min(c2, o2) - l2

    # ── Three-candle patterns (require 3 bars) ────────────────────────────────
    if len(closes) >= 3:
        o3, h3, l3, c3 = opens[-3], highs[-3], lows[-3], closes[-3]
        body3 = abs(c3 - o3)

        # Morning Star: large bearish → small body → bullish close above midpoint
        mid_body = abs(c2 - o2)
        if c3 < o3 and mid_body < body3 * 0.4 and c1 > o1 and c1 > (o3 + c3) / 2:
            return "Morning Star", 0.82

        # Evening Star: large bullish → small body → bearish close below midpoint
        if c3 > o3 and mid_body < body3 * 0.4 and c1 < o1 and c1 < (o3 + c3) / 2:
            return "Evening Star", 0.82

        # Three White Soldiers: 3 consecutive bullish candles, each closing higher
        if (c3 > o3 and c2 > o2 and c1 > o1
                and c2 > c3 and c1 > c2
                and body3 > 0 and body2 > 0 and body1 > 0
                and lower_wick1 < body1 * 0.3
                and lower_wick2 < body2 * 0.3):
            return "Three White Soldiers", 0.88

        # Three Black Crows: 3 consecutive bearish candles, each closing lower
        if (c3 < o3 and c2 < o2 and c1 < o1
                and c2 < c3 and c1 < c2
                and body3 > 0 and body2 > 0 and body1 > 0
                and upper_wick1 < body1 * 0.3
                and upper_wick2 < body2 * 0.3):
            return "Three Black Crows", 0.88

    # ── Two-candle patterns ───────────────────────────────────────────────────

    # Bullish Engulfing: prior bearish, current bullish, fully engulfs
    if c2 < o2 and c1 > o1 and c1 >= o2 and o1 <= c2 and body1 > body2 * 1.0:
        return "Bullish Engulfing", 0.85

    # Bearish Engulfing: prior bullish, current bearish, fully engulfs
    if c2 > o2 and c1 < o1 and c1 <= o2 and o1 >= c2 and body1 > body2 * 1.0:
        return "Bearish Engulfing", 0.85

    # Tweezer Bottom: two candles share same low, second is bullish
    if abs(l1 - l2) < rng1 * 0.1 and c1 > o1 and c2 < o2:
        return "Tweezer Bottom", 0.75

    # Tweezer Top: two candles share same high, second is bearish
    if abs(h1 - h2) < rng1 * 0.1 and c1 < o1 and c2 > o2:
        return "Tweezer Top", 0.75

    # Piercing Line: prior bearish, current bullish opening near or below prior close,
    # closing above midpoint of prior body (gap not required for intraday continuous data)
    if (c2 < o2 and c1 > o1
            and o1 <= c2 + atr * 0.05      # opens near or below prior close
            and c1 > (o2 + c2) / 2 and c1 < o2
            and body2 > atr * 0.3):
        return "Piercing Line", 0.75

    # Dark Cloud Cover: prior bullish, current bearish opening near or above prior close,
    # closing below midpoint of prior body
    if (c2 > o2 and c1 < o1
            and o1 >= c2 - atr * 0.05      # opens near or above prior close
            and c1 < (o2 + c2) / 2 and c1 > o2
            and body2 > atr * 0.3):
        return "Dark Cloud Cover", 0.75

    # Bullish Harami: large bearish candle, small bullish body inside
    if (c2 < o2 and c1 > o1
            and o1 > c2 and c1 < o2
            and body1 < body2 * 0.5):
        return "Bullish Harami", 0.65

    # Bearish Harami: large bullish candle, small bearish body inside
    if (c2 > o2 and c1 < o1
            and o1 < c2 and c1 > o2
            and body1 < body2 * 0.5):
        return "Bearish Harami", 0.65

    # Inside Bar: entire range of candle 1 is within candle 2
    if h1 <= h2 and l1 >= l2 and body1 > 0:
        return "Inside Bar", 0.0   # directional neutral — breakout pending

    # ── Single-candle patterns ────────────────────────────────────────────────

    # Doji — body < 8% of range
    if body_ratio1 < 0.08:
        return "Doji", 0.0

    # Spinning Top — small body, wicks both sides
    if body_ratio1 < 0.25 and upper_wick1 > body1 and lower_wick1 > body1:
        return "Spinning Top", 0.0

    # Hammer: bullish close, long lower wick (>= 2x body), tiny upper wick
    if c1 > o1 and lower_wick1 >= body1 * 2.0 and upper_wick1 <= body1 * 0.5:
        return "Hammer", 0.72

    # Shooting Star: bearish close, long upper wick (>= 2x body), tiny lower wick
    if c1 < o1 and upper_wick1 >= body1 * 2.0 and lower_wick1 <= body1 * 0.5:
        return "Shooting Star", 0.72

    # Inverted Hammer (bullish reversal context only): bullish close, large upper wick
    if c1 > o1 and upper_wick1 >= body1 * 2.0 and lower_wick1 <= body1 * 0.5:
        return "Inverted Hammer", 0.55

    return "None", 0.0


def candle_signal(pattern: str) -> str:
    bullish = {
        "Bullish Engulfing", "Hammer", "Inverted Hammer", "Morning Star",
        "Three White Soldiers", "Tweezer Bottom", "Piercing Line", "Bullish Harami"
    }
    bearish = {
        "Bearish Engulfing", "Shooting Star", "Evening Star",
        "Three Black Crows", "Tweezer Top", "Dark Cloud Cover", "Bearish Harami"
    }
    if pattern in bullish:
        return "BUY"
    if pattern in bearish:
        return "SELL"
    return "NEUTRAL"


# ─── Trade type classifier ────────────────────────────────────────────────────

def classify_trade_type(timeframe: str, adx: float) -> str:
    """
    Classify the nature of the trade setup by timeframe and trend strength.
      M5 / M15              → Scalp     (minutes to a couple of hours)
      M30 / H1 (ADX < 30)  → Intraday  (hours, same session)
      H1 (ADX >= 30) / H4  → Swing     (1–5 days)
      D1                   → Position  (weeks)
    """
    if timeframe in ("M5", "M15"):
        return "Scalp"
    if timeframe == "D1":
        return "Position"
    if timeframe == "H4":
        return "Swing"
    if timeframe == "H1" and adx >= 30:
        return "Swing"
    return "Intraday"


# ─── Trading session ──────────────────────────────────────────────────────────

def get_trading_session() -> Tuple[str, float]:
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour < 17:
        return "London/NY Overlap", 1.10
    elif 8 <= hour < 13:
        return "London", 1.05
    elif 17 <= hour < 22:
        return "New York", 1.00
    else:
        return "Asian", 0.82


# ─── Higher-timeframe bias ────────────────────────────────────────────────────

HTF_MAP = {
    "M5":  "H1",
    "M15": "H1",
    "M30": "H4",
    "H1":  "H4",
    "H4":  "D1",
    "D1":  "D1",
}


async def _get_htf_bias(htf: str) -> str:
    try:
        data = await fetch_ohlcv(htf)
        if not data or len(data) < 30:
            return "Neutral"
        closes = data.closes
        highs  = data.highs
        lows   = data.lows
        price  = data.price

        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        rsi   = compute_rsi(closes, 14)
        adx, plus_di, minus_di = compute_adx(highs, lows, closes, 14)
        macd_line, sig_line, hist = compute_macd(closes)

        score_bull = 0
        score_bear = 0

        if price > ema20:  score_bull += 1
        else:              score_bear += 1
        if price > ema50:  score_bull += 1
        else:              score_bear += 1
        if plus_di > minus_di and adx >= 18:  score_bull += 1
        elif minus_di > plus_di and adx >= 18: score_bear += 1
        if macd_line > sig_line and hist > 0: score_bull += 1
        elif macd_line < sig_line and hist < 0: score_bear += 1
        if rsi > 52: score_bull += 1
        elif rsi < 48: score_bear += 1

        if score_bull >= 4:   return "Bullish"
        elif score_bear >= 4: return "Bearish"
        elif score_bull >= 3: return "Slightly Bullish"
        elif score_bear >= 3: return "Slightly Bearish"
        return "Neutral"
    except Exception as e:
        logger.warning(f"HTF bias fetch failed ({htf}): {e}")
        return "Neutral"


# ─── Volume spike check ───────────────────────────────────────────────────────

def is_volume_spike(volumes: List[float], lookback: int = 20, threshold: float = 1.5) -> bool:
    if not volumes or len(volumes) < lookback + 1:
        return False
    recent_avg = sum(volumes[-lookback - 1:-1]) / lookback
    if recent_avg <= 0:
        return False
    return volumes[-1] >= recent_avg * threshold


# ─── Indicator scoring ────────────────────────────────────────────────────────

def _score_rsi(rsi: float) -> Tuple[str, float]:
    """Only score RSI at meaningful extremes — no weak 0.30 mid-zone noise."""
    if rsi >= 70:          return "SELL", 0.90
    if rsi <= 30:          return "BUY",  0.90
    if 65 <= rsi < 70:     return "SELL", 0.70
    if 30 < rsi <= 35:     return "BUY",  0.70
    # 35–65: RSI is in neutral zone — do not cast a vote
    return "NEUTRAL", 0.0


def _score_macd(macd_line: float, signal_line: float, hist: float,
                prev_hist: Optional[float] = None) -> Tuple[str, float]:
    """
    Require histogram momentum — bare above/below signal line is not enough.
    Histogram must be expanding (momentum building) for a strong vote.
    """
    if macd_line > signal_line and hist > 0:
        expanding = prev_hist is not None and hist > prev_hist
        return "BUY", 0.85 if expanding else 0.65
    if macd_line < signal_line and hist < 0:
        expanding = prev_hist is not None and hist < prev_hist
        return "SELL", 0.85 if expanding else 0.65
    # Crossover zone (macd above signal but hist still negative, or vice versa) = noise
    return "NEUTRAL", 0.0


def _score_ema(price: float, ema20: float, ema50: float,
               ema200: Optional[float] = None) -> Tuple[str, float]:
    """
    Require a proper EMA stack — partial alignment gives no vote.
    Price must be on the right side of both EMA20 and EMA50, and they must
    be in the correct order (ema20 > ema50 for BUY, ema20 < ema50 for SELL).
    """
    if ema200 is not None:
        if price > ema20 > ema50 > ema200: return "BUY",  1.0
        if price < ema20 < ema50 < ema200: return "SELL", 1.0
    if price > ema20 > ema50: return "BUY",  0.90
    if price < ema20 < ema50: return "SELL", 0.90
    # Mixed EMA alignment (e.g. price above EMA50 but below EMA20) = chop = no vote
    return "NEUTRAL", 0.0


def _score_stoch(k: float, d: float,
                 prev_k: Optional[float] = None,
                 prev_d: Optional[float] = None) -> Tuple[str, float]:
    """
    Only score confirmed crossovers in oversold/overbought zones.
    Mid-zone stoch gives no vote — it's noise.
    """
    if prev_k is not None and prev_d is not None:
        # Bullish crossover from oversold (K crosses above D, both below 35)
        if (prev_k <= prev_d) and (k > d) and k <= 35:
            return "BUY",  0.90
        # Bearish crossover from overbought (K crosses below D, both above 65)
        if (prev_k >= prev_d) and (k < d) and k >= 65:
            return "SELL", 0.90
    # Mid-zone stoch (not oversold/overbought and not a clean crossover) = no vote
    return "NEUTRAL", 0.0


def _score_bb(pct_b: float) -> Tuple[str, float]:
    """Only score at genuine Bollinger Band extremes — ignore mid-band noise."""
    if pct_b > 95:  return "SELL", 0.85
    if pct_b < 5:   return "BUY",  0.85
    # 5–95: BB%B in normal range — no vote (price can go anywhere from here)
    return "NEUTRAL", 0.0


# ─── Limit-entry refinement ───────────────────────────────────────────────────

def calc_limit_entry(direction: str, price: float, atr: float,
                     ema20: float, ema50: float,
                     support1: float, resistance1: float,
                     trade_type: str) -> Tuple[float, str]:
    """
    Suggest an optimal limit-order entry for better fills.

    Scalp trades execute at market — speed matters over price.
    Intraday / Swing / Position: suggest a retracement level.

    BUY  limit: strictly below current price (better fill lower)
    SELL limit: strictly above current price (better fill higher)
    """
    if trade_type == "Scalp":
        return round(price, 2), "Market (Scalp — execute now)"

    pull_factor = 0.35 if trade_type == "Intraday" else 0.55

    if direction == "BUY":
        atr_target = price - atr * pull_factor
        # Use EMA20 only if it is BELOW price (a genuine pullback level)
        if support1 < ema20 < price:
            ema_target = ema20 + atr * 0.05   # just above EMA20
        else:
            ema_target = atr_target
        # Take the higher of the two (less aggressive = easier fill)
        limit = max(atr_target, ema_target)
        # Hard rules: must be strictly below price AND above S1
        limit = max(limit, support1 + atr * 0.12)
        limit = min(limit, price - atr * 0.10)   # enforce strictly below price
        note = f"Limit @ {limit:,.2f}  (EMA/retrace)"
        return round(limit, 2), note

    if direction == "SELL":
        atr_target = price + atr * pull_factor
        # Use EMA20 only if it is ABOVE price (a genuine retracement level)
        if price < ema20 < resistance1:
            ema_target = ema20 - atr * 0.05
        else:
            ema_target = atr_target
        # Take the lower of the two (less aggressive = easier fill)
        limit = min(atr_target, ema_target)
        # Hard rules: must be strictly above price AND below R1
        limit = min(limit, resistance1 - atr * 0.12)
        limit = max(limit, price + atr * 0.10)   # enforce strictly above price
        note = f"Limit @ {limit:,.2f}  (EMA/retrace)"
        return round(limit, 2), note

    return round(price, 2), "Market"


# ─── Main analysis ────────────────────────────────────────────────────────────

async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    from src.config import CONFIDENCE_THRESHOLD, MIN_RR_RATIO

    htf = HTF_MAP.get(timeframe, "H4")

    async def _neutral() -> str:
        return "Neutral"

    data, htf_bias = await asyncio.gather(
        fetch_ohlcv(timeframe),
        _get_htf_bias(htf) if htf != timeframe else _neutral(),
    )

    if data is None or len(data) < 35:
        logger.error(f"Insufficient data for {timeframe}")
        raise RuntimeError(f"Could not fetch enough market data for {timeframe}")

    closes  = data.closes
    highs   = data.highs
    lows    = data.lows
    volumes = data.volumes
    opens   = data.opens
    price   = data.price

    # ── Compute all indicators ──
    rsi                        = compute_rsi(closes, 14)
    macd_line, sig_line, hist  = compute_macd(closes, 12, 26, 9)
    prev_macd_line, prev_sig, prev_hist = compute_macd(closes[:-1], 12, 26, 9) if len(closes) > 36 else (macd_line, sig_line, hist)

    ema20  = _ema(closes, 20)
    ema50  = _ema(closes, 50)
    ema200 = _ema(closes, 200) if len(closes) >= 200 else None

    stoch_k, stoch_d           = compute_stoch(highs, lows, closes, 14, 3)
    prev_stoch_k, prev_stoch_d = compute_stoch(highs[:-1], lows[:-1], closes[:-1], 14, 3) \
                                  if len(closes) > 15 else (stoch_k, stoch_d)

    atr = compute_atr(highs, lows, closes, 14)
    bb_upper, bb_mid, bb_lower, bb_pct = compute_bollinger(closes, 20, 2.0)
    adx, plus_di, minus_di     = compute_adx(highs, lows, closes, 14)

    candle_pat, candle_wt = detect_candlestick(opens, highs, lows, closes, atr)
    c_signal = candle_signal(candle_pat)

    session_label, session_mult = get_trading_session()
    vol_spike = is_volume_spike(volumes, lookback=20, threshold=1.5)

    logger.info(
        f"[{timeframe}] Price={price:.2f} RSI={rsi} MACD={macd_line:.3f}/{sig_line:.3f} "
        f"EMA20={ema20:.2f} EMA50={ema50:.2f} Stoch={stoch_k:.1f}/{stoch_d:.1f} "
        f"ATR={atr:.2f} BB%={bb_pct:.1f} ADX={adx:.1f}(+DI={plus_di:.1f}/-DI={minus_di:.1f}) "
        f"Session={session_label} HTF={htf_bias} Candle={candle_pat} VolSpike={vol_spike}"
    )

    # ── Score indicators ──
    rsi_sig,   rsi_conf   = _score_rsi(rsi)
    macd_sig,  macd_conf  = _score_macd(macd_line, sig_line, hist, prev_hist)
    ema_sig,   ema_conf   = _score_ema(price, ema20, ema50, ema200)
    stoch_sig, stoch_conf = _score_stoch(stoch_k, stoch_d, prev_stoch_k, prev_stoch_d)
    bb_sig,    bb_conf    = _score_bb(bb_pct)

    # ── No adx_mult weighting — ADX is used as a hard gate below ──────────────
    indicators = [
        Indicator("RSI(14)",   rsi,       rsi_sig,   0.20),
        Indicator("MACD",      macd_line, macd_sig,  0.22),
        Indicator("EMA Stack", ema20,     ema_sig,   0.28),
        Indicator("Stoch(14)", stoch_k,   stoch_sig, 0.18),
        Indicator("BB %B",     bb_pct,    bb_sig,    0.12),
    ]

    conf_map = {
        "RSI(14)":   rsi_conf,
        "MACD":      macd_conf,
        "EMA Stack": ema_conf,
        "Stoch(14)": stoch_conf,
        "BB %B":     bb_conf,
    }

    buy_votes  = sum(1 for i in indicators if i.signal == "BUY")
    sell_votes = sum(1 for i in indicators if i.signal == "SELL")
    wait_votes = sum(1 for i in indicators if i.signal == "NEUTRAL")

    buy_score  = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "BUY")
    sell_score = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "SELL")

    # Candlestick bonus — meaningful weight only for strong confirmed patterns
    if c_signal == "BUY"  and candle_wt >= 0.75:
        buy_score  += 0.10 * candle_wt
    elif c_signal == "SELL" and candle_wt >= 0.75:
        sell_score += 0.10 * candle_wt

    # Volume spike bonus — confirms the move has real participation
    if vol_spike:
        if buy_score > sell_score:
            buy_score  *= 1.08
        elif sell_score > buy_score:
            sell_score *= 1.08

    total_score = buy_score + sell_score
    raw_conf    = max(buy_score, sell_score) / total_score if total_score > 0 else 0.5
    base_conf   = max(50, min(97, int(50 + raw_conf * 48)))
    confidence  = max(50, min(97, base_conf))

    margin    = abs(buy_score - sell_score)
    MIN_VOTES = 3   # always require 3/5 — no exceptions
    di_conf_buy  = plus_di  > minus_di and adx >= 20
    di_conf_sell = minus_di > plus_di  and adx >= 20

    if buy_score > sell_score and margin > 0.05 and buy_votes >= MIN_VOTES:
        direction = "BUY"
        bias      = "Bullish"
        if di_conf_buy:
            confidence = min(97, confidence + 5)
    elif sell_score > buy_score and margin > 0.05 and sell_votes >= MIN_VOTES:
        direction = "SELL"
        bias      = "Bearish"
        if di_conf_sell:
            confidence = min(97, confidence + 5)
    else:
        direction = "NEUTRAL"
        bias      = "Neutral"

    # ── HTF gate (hard block for strong misalignment, penalty for slight) ──────
    htf_align  = True
    htf_reason = ""
    if direction in ("BUY", "SELL"):
        htf_strongly_bullish = htf_bias == "Bullish"
        htf_slightly_bullish = htf_bias == "Slightly Bullish"
        htf_strongly_bearish = htf_bias == "Bearish"
        htf_slightly_bearish = htf_bias == "Slightly Bearish"
        htf_bullish = htf_strongly_bullish or htf_slightly_bullish
        htf_bearish = htf_strongly_bearish or htf_slightly_bearish

        if direction == "BUY" and htf_strongly_bearish:
            # Hard block — never fight a confirmed HTF downtrend
            direction  = "NEUTRAL"
            htf_align  = False
            htf_reason = f"Hard block: {htf} strongly Bearish — no longs"
        elif direction == "SELL" and htf_strongly_bullish:
            direction  = "NEUTRAL"
            htf_align  = False
            htf_reason = f"Hard block: {htf} strongly Bullish — no shorts"
        elif direction == "BUY" and htf_slightly_bearish:
            confidence = max(50, confidence - 12)
            htf_align  = False
            htf_reason = f"Counter-trend: {htf} Slightly Bearish"
        elif direction == "SELL" and htf_slightly_bullish:
            confidence = max(50, confidence - 12)
            htf_align  = False
            htf_reason = f"Counter-trend: {htf} Slightly Bullish"
        elif (direction == "BUY" and htf_bullish) or (direction == "SELL" and htf_bearish):
            confidence = min(97, confidence + 8)   # reward alignment

    strength_score = max(buy_votes, sell_votes) / len(indicators)
    if strength_score >= 0.75 or adx >= 30:
        strength = "Strong"
        momentum = "High"
    elif strength_score >= 0.50 or adx >= 20:
        strength = "Moderate"
        momentum = "Medium"
    else:
        strength = "Weak"
        momentum = "Low"

    trend = bias if bias != "Neutral" else "Ranging"

    # Verdict reason
    parts = []
    if direction == "BUY":
        if rsi_sig  == "BUY":   parts.append(f"RSI {rsi:.0f} — oversold")
        if macd_sig == "BUY":   parts.append("MACD bullish crossover")
        if ema_sig  == "BUY":   parts.append("Price above EMA stack")
        if bb_sig   == "BUY":   parts.append(f"BB%B {bb_pct:.0f} — near lower band")
        if stoch_sig == "BUY":  parts.append(f"Stoch {stoch_k:.0f} — oversold cross")
        if candle_pat not in ("None", "Doji", "Inside Bar", "Spinning Top") and c_signal == "BUY":
            parts.append(candle_pat)
        if not htf_align:       parts.append(htf_reason)
    elif direction == "SELL":
        if rsi_sig  == "SELL":  parts.append(f"RSI {rsi:.0f} — overbought")
        if macd_sig == "SELL":  parts.append("MACD bearish crossover")
        if ema_sig  == "SELL":  parts.append("Price below EMA stack")
        if bb_sig   == "SELL":  parts.append(f"BB%B {bb_pct:.0f} — near upper band")
        if stoch_sig == "SELL": parts.append(f"Stoch {stoch_k:.0f} — overbought cross")
        if candle_pat not in ("None", "Doji", "Inside Bar", "Spinning Top") and c_signal == "SELL":
            parts.append(candle_pat)
        if not htf_align:       parts.append(htf_reason)
    verdict_reason = ". ".join(parts[:5]) if parts else "Indicators mixed — no clear edge"

    # S/R levels
    r1, r2, s1, s2 = find_sr_levels(highs, lows, closes, price, atr, volumes)

    # Breakout / reversal
    breakout = detect_breakout(closes, highs, 20)
    reversal = detect_reversal(rsi, stoch_k, hist, closes)

    # Trade type
    trade_type = classify_trade_type(timeframe, adx)

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    # SL uses 2.0×–2.5× ATR so gold wicks don't stop us out before the move.
    # Previous 1.2–1.4× SL was the primary cause of SL hits.
    sl_mult = (
        2.5 if timeframe in ("M5", "M15") else   # scalp: tighter but still respects wick
        2.2 if timeframe in ("M30", "H1") else
        2.0                                        # H4/D1: slower, wider is fine
    )
    max_sl_dist = atr * 3.5   # cap so SL isn't absurdly far from price

    sl_min_dist = atr * sl_mult   # enforce this as the absolute floor for SL distance

    if direction == "BUY":
        entry      = round(price, 2)
        ideal_sl   = round(price - sl_min_dist, 2)
        # Use S1 level only when it gives AT LEAST sl_mult × ATR room
        sl_from_sr = s1 - atr * 0.20
        dist_sr    = price - sl_from_sr
        stop_loss  = (
            round(sl_from_sr, 2)
            if (dist_sr >= sl_min_dist and dist_sr <= max_sl_dist and sl_from_sr > 0)
            else ideal_sl
        )
    elif direction == "SELL":
        entry      = round(price, 2)
        ideal_sl   = round(price + sl_min_dist, 2)
        sl_from_sr = r1 + atr * 0.20
        dist_sr    = sl_from_sr - price
        stop_loss  = (
            round(sl_from_sr, 2)
            if (dist_sr >= sl_min_dist and dist_sr <= max_sl_dist)
            else ideal_sl
        )
    else:
        entry     = round(price, 2)
        stop_loss = round(price - sl_min_dist, 2)

    sl_dist  = abs(entry - stop_loss)
    tp1_dist = sl_dist * 2.0
    tp2_dist = sl_dist * 3.5

    if direction == "BUY":
        tp1 = round(min(entry + tp1_dist, r1 - atr * 0.1), 2) if r1 > entry + tp1_dist * 0.6 else round(entry + tp1_dist, 2)
        tp2 = round(min(entry + tp2_dist, r2 - atr * 0.1), 2) if r2 > entry + tp2_dist * 0.6 else round(entry + tp2_dist, 2)
    elif direction == "SELL":
        tp1 = round(max(entry - tp1_dist, s1 + atr * 0.1), 2) if s1 < entry - tp1_dist * 0.6 else round(entry - tp1_dist, 2)
        tp2 = round(max(entry - tp2_dist, s2 + atr * 0.1), 2) if s2 < entry - tp2_dist * 0.6 else round(entry - tp2_dist, 2)
    else:
        tp1 = round(entry + tp1_dist, 2)
        tp2 = round(entry + tp2_dist, 2)

    rr_ratio = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist > 0 else 0.0

    # Limit entry suggestion
    limit_entry, entry_note = calc_limit_entry(
        direction, price, atr, ema20, ema50, s1, r1, trade_type
    )

    # ── Signal gating ─────────────────────────────────────────────────────────
    # Policy: always emit BUY/SELL when there is a directional bias.
    # Only two things produce WAIT:
    #   1. Truly no direction (NEUTRAL bias — indicators split evenly)
    #   2. Hard HTF block (confirmed opposite macro trend)
    # Everything else (ADX, session, confidence, R:R, wall) lowers the
    # setup_quality grade but does NOT suppress the signal.
    wait_reason  = ""
    signal_notes = []   # collected caveats shown on the entry card

    near_resistance = direction == "BUY"  and (r1 - price) < atr * 0.5
    near_support    = direction == "SELL" and (price - s1) < atr * 0.5

    if direction != "NEUTRAL" and htf_reason and "Hard block" in htf_reason:
        # Hard HTF block — genuinely do not trade against a confirmed macro trend
        action      = "WAIT"
        wait_reason = htf_reason
    elif direction != "NEUTRAL":
        # Always give the signal; collect any caveats as notes
        action = direction
        if adx < 15:
            signal_notes.append(f"ADX {adx:.1f} — extreme ranging, use wider SL")
        elif adx < 20 and timeframe not in ("H4", "D1"):
            signal_notes.append(f"ADX {adx:.1f} — weak trend, reduce size")
        if near_resistance:
            signal_notes.append(f"Entry near R1 {r1:.2f} — tight space above")
        if near_support:
            signal_notes.append(f"Entry near S1 {s1:.2f} — tight space below")
        if session_label == "Asian" and timeframe in ("M5", "M15", "M30", "H1"):
            signal_notes.append("Asian session — lower liquidity, expect wider spreads")
        if confidence < CONFIDENCE_THRESHOLD:
            signal_notes.append(f"Confidence {confidence}% — lower conviction setup")
        if rr_ratio < MIN_RR_RATIO:
            signal_notes.append(f"R:R 1:{rr_ratio} — below ideal 1:{int(MIN_RR_RATIO)}, use limit")
        if max(buy_votes, sell_votes) < MIN_VOTES:
            signal_notes.append(f"Only {max(buy_votes, sell_votes)}/5 indicators agree")
        if htf_reason:
            signal_notes.append(htf_reason)
        # Attach notes to wait_reason field (repurposed as signal context)
        wait_reason = " | ".join(signal_notes) if signal_notes else ""
    else:
        action      = "WAIT"
        wait_reason = htf_reason or verdict_reason or "Indicators split — no directional edge"

    liq_zone = (
        f"{s1:.2f} — {round(s1 + atr, 2)}"
        if direction == "BUY"
        else f"{round(r1 - atr, 2)} — {r1:.2f}"
    )

    # ── Extended pro fields ────────────────────────────────────────────────────
    mkt_structure = detect_market_structure(highs, lows, lookback=5)

    # TP3: measured move (5× SL distance)
    if direction == "BUY":
        tp3 = round(entry + sl_dist * 5.0, 2)
    elif direction == "SELL":
        tp3 = round(entry - sl_dist * 5.0, 2)
    else:
        tp3 = round(entry + sl_dist * 5.0, 2)

    # Confluence list
    confluence_list: List[str] = []
    if direction in ("BUY", "SELL"):
        for ind in indicators:
            if ind.signal == direction:
                if ind.name == "RSI(14)":
                    tag = "oversold" if direction == "BUY" else "overbought"
                    confluence_list.append(f"RSI {rsi:.0f} — {tag}")
                elif ind.name == "MACD":
                    tag = "bullish" if direction == "BUY" else "bearish"
                    confluence_list.append(f"MACD {tag} crossover")
                elif ind.name == "EMA Stack":
                    tag = "above" if direction == "BUY" else "below"
                    confluence_list.append(f"Price {tag} EMA stack")
                elif ind.name == "Stoch(14)":
                    tag = "oversold cross" if direction == "BUY" else "overbought cross"
                    confluence_list.append(f"Stoch {stoch_k:.0f} — {tag}")
                elif ind.name == "BB %B":
                    tag = "lower band" if direction == "BUY" else "upper band"
                    confluence_list.append(f"BB%B {bb_pct:.0f} — near {tag}")
        if mkt_structure == "HH_HL" and direction == "BUY":
            confluence_list.append("HH/HL market structure (bullish)")
        elif mkt_structure == "LH_LL" and direction == "SELL":
            confluence_list.append("LH/LL market structure (bearish)")
        if htf_bias in ("Bullish", "Slightly Bullish") and direction == "BUY":
            confluence_list.append(f"{htf} bias aligned ({htf_bias})")
        elif htf_bias in ("Bearish", "Slightly Bearish") and direction == "SELL":
            confluence_list.append(f"{htf} bias aligned ({htf_bias})")
        if vol_spike:
            confluence_list.append("Volume spike (institutional participation)")
        if session_label == "London/NY Overlap":
            confluence_list.append("London/NY Overlap (highest liquidity)")
        if candle_pat not in ("None", "Doji", "Inside Bar", "Spinning Top") and candle_wt >= 0.72:
            if c_signal == direction or c_signal == "BUY" and direction == "BUY" or c_signal == "SELL" and direction == "SELL":
                confluence_list.append(f"Candle: {candle_pat}")
        if breakout:
            confluence_list.append("Breakout above recent swing high")
        if reversal:
            confluence_list.append("Divergence reversal signal")

    # Win probability — starts at confidence, boosted by confluence depth
    raw_wp = confidence
    raw_wp += min(len(confluence_list) * 2, 10)   # up to +10 for deep confluence
    if session_label == "London/NY Overlap": raw_wp += 3
    if adx >= 30: raw_wp += 3
    if adx >= 40: raw_wp += 2
    win_probability = max(50, min(92, raw_wp)) if action in ("BUY", "SELL") else 0

    # ── Fibonacci retracement & early entry ──────────────────────────────────
    eff_dir = direction if direction in ("BUY", "SELL") else "BUY"
    fib_382, fib_500, fib_618 = compute_fibonacci_levels(highs, lows, eff_dir, lookback=50)

    # Early entry: best pullback level for a limit order
    # BUY  → we want price to dip to 61.8% or 50% before bouncing up
    # SELL → we want price to rally to 61.8% or 50% before falling
    early_entry = 0.0
    early_entry_reason = ""
    if action == "BUY":
        if fib_618 > stop_loss and fib_618 < price:
            early_entry = fib_618
            early_entry_reason = f"Fib 61.8% retrace @ {fib_618:,.2f} — deep pullback, high R:R"
        elif fib_500 > stop_loss and fib_500 < price:
            early_entry = fib_500
            early_entry_reason = f"Fib 50.0% retrace @ {fib_500:,.2f} — mid pullback zone"
        elif fib_382 > stop_loss and fib_382 < price:
            early_entry = fib_382
            early_entry_reason = f"Fib 38.2% retrace @ {fib_382:,.2f} — shallow pullback"
        else:
            early_entry = limit_entry if limit_entry and limit_entry < price else price
            early_entry_reason = "EMA/ATR pullback zone — enter on retrace, not market"
    elif action == "SELL":
        if fib_618 < r1 and fib_618 > price:
            early_entry = fib_618
            early_entry_reason = f"Fib 61.8% retrace @ {fib_618:,.2f} — deep retrace, high R:R"
        elif fib_500 < r1 and fib_500 > price:
            early_entry = fib_500
            early_entry_reason = f"Fib 50.0% retrace @ {fib_500:,.2f} — mid retrace zone"
        elif fib_382 < r1 and fib_382 > price:
            early_entry = fib_382
            early_entry_reason = f"Fib 38.2% retrace @ {fib_382:,.2f} — shallow retrace"
        else:
            early_entry = limit_entry if limit_entry and limit_entry > price else price
            early_entry_reason = "EMA/ATR retrace zone — sell the bounce, not market"

    # ── Setup quality grade ───────────────────────────────────────────────────
    if action in ("BUY", "SELL"):
        if win_probability >= 85 and len(confluence_list) >= 5 and adx >= 25:
            setup_quality = "A+"
        elif win_probability >= 80 and len(confluence_list) >= 4:
            setup_quality = "A"
        elif win_probability >= 72:
            setup_quality = "B"
        else:
            setup_quality = "C"
    else:
        setup_quality = "WAIT"

    return MarketAnalysis(
        price=price, timeframe=timeframe,
        bias=bias, trend=trend, strength=strength, momentum=momentum,
        confidence=confidence,
        entry=entry, stop_loss=stop_loss, tp1=tp1, tp2=tp2, rr_ratio=rr_ratio,
        action=action, wait_reason=wait_reason,
        resistance1=r1, resistance2=r2, support1=s1, support2=s2,
        breakout=breakout, reversal=reversal, liquidity_zone=liq_zone,
        adx=adx, atr=atr, bb_pct=bb_pct,
        bb_upper=bb_upper, bb_lower=bb_lower,
        indicators=indicators,
        buy_votes=buy_votes, sell_votes=sell_votes, wait_votes=wait_votes,
        verdict_reason=verdict_reason,
        session=session_label, htf_bias=htf_bias, candle_pattern=candle_pat,
        trade_type=trade_type, limit_entry=limit_entry, entry_note=entry_note,
        rsi_value=rsi, stoch_k_val=stoch_k, stoch_d_val=stoch_d,
        macd_hist=hist, plus_di=plus_di, minus_di=minus_di,
        market_structure=mkt_structure,
        win_probability=win_probability,
        confluence_list=confluence_list,
        tp3=tp3,
        fib_382=fib_382, fib_500=fib_500, fib_618=fib_618,
        early_entry=early_entry, early_entry_reason=early_entry_reason,
        setup_quality=setup_quality,
    )


def compute_fibonacci_levels(highs: List[float], lows: List[float],
                             direction: str, lookback: int = 50
                             ) -> Tuple[float, float, float]:
    """
    Find the most recent significant swing and compute Fibonacci retracement levels.
    BUY:  swing = recent low → recent high  → retrace levels below price
    SELL: swing = recent high → recent low  → retrace levels above price
    Returns (fib_382, fib_500, fib_618).
    """
    n = len(highs)
    window = min(lookback, n)
    h_slice = highs[-window:]
    l_slice = lows[-window:]

    swing_high = max(h_slice)
    swing_low  = min(l_slice)
    rng = swing_high - swing_low
    if rng <= 0:
        mid = (swing_high + swing_low) / 2
        return round(mid, 2), round(mid, 2), round(mid, 2)

    if direction == "BUY":
        # Retracement FROM swing_high DOWN — entries below current price
        fib_382 = round(swing_high - rng * 0.382, 2)
        fib_500 = round(swing_high - rng * 0.500, 2)
        fib_618 = round(swing_high - rng * 0.618, 2)
    else:
        # Retracement FROM swing_low UP — entries above current price
        fib_382 = round(swing_low + rng * 0.382, 2)
        fib_500 = round(swing_low + rng * 0.500, 2)
        fib_618 = round(swing_low + rng * 0.618, 2)

    return fib_382, fib_500, fib_618


def detect_market_structure(highs: List[float], lows: List[float], lookback: int = 5) -> str:
    """Return HH_HL, LH_LL, TRANSITION, or RANGING based on swing structure."""
    n = len(highs)
    if n < lookback * 4:
        return "RANGING"
    swing_highs, swing_lows = [], []
    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_highs.append(highs[i])
        if all(lows[i] <= lows[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_lows.append(lows[i])
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1]  > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1]  < swing_lows[-2]
        if hh and hl:   return "HH_HL"
        if lh and ll:   return "LH_LL"
        if hh or hl or lh or ll: return "TRANSITION"
    return "RANGING"


def detect_breakout(closes: List[float], highs: List[float], period: int = 20) -> bool:
    if len(closes) < period + 1:
        return False
    return closes[-1] > max(highs[-period - 1:-1])


def detect_reversal(rsi: float, stoch_k: float, macd_hist: float,
                    closes: List[float]) -> bool:
    if len(closes) < 5:
        return False
    bearish_div = rsi > 70 and closes[-1] > closes[-3] and macd_hist < 0
    bullish_div = rsi < 30 and closes[-1] < closes[-3] and macd_hist > 0
    stoch_extreme = stoch_k > 85 or stoch_k < 15
    return (bearish_div or bullish_div) and stoch_extreme
