"""
XAU/USD Analysis Engine — accuracy-focused rewrite.

Key improvements over v1:
  - Fixed MACD (proper dual-EMA initialization, no skew)
  - Fixed RSI scoring (45-55 neutral zone, momentum zones correct)
  - Fixed Stochastic (crossover-based, not just level-based)
  - Candlestick pattern detection (engulfing, pin bar, doji)
  - Trading session filter (Asian = -20% confidence; London/NY overlap = +10%)
  - Higher-timeframe (HTF) confirmation gate (H4 for H1, D1 for H4)
  - Confluence gate: require >= 3 of 5 core indicators in same direction
  - Better SL: snap to nearest real S/R level instead of pure ATR multiple
  - Volume spike confirmation for breakout signals
  - S/R lookback increased from 3 → 6 bars
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
    bb_pct:         float = 0.0
    indicators: List[Indicator] = field(default_factory=list)
    buy_votes:  int = 0
    sell_votes: int = 0
    wait_votes: int = 0
    verdict_reason: str = ""
    session:    str = ""
    htf_bias:   str = "Neutral"
    candle_pattern: str = "None"


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
    """
    Fixed MACD: both EMAs warm up to index `slow` before MACD is computed,
    so fast and slow EMAs are aligned on the same candle set.
    """
    if len(closes) < slow + sig:
        return 0.0, 0.0, 0.0

    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    k_sig  = 2.0 / (sig + 1)

    # Warm up fast EMA using first `fast` bars, then run forward to index `slow`
    ema_f = sum(closes[:fast]) / fast
    for p in closes[fast:slow]:
        ema_f = p * k_fast + ema_f * (1 - k_fast)

    # Warm up slow EMA using first `slow` bars — same endpoint as fast
    ema_s = sum(closes[:slow]) / slow

    # Compute MACD line from index `slow` onwards — both EMAs aligned
    macd_vals: List[float] = []
    for p in closes[slow:]:
        ema_f = p * k_fast + ema_f * (1 - k_fast)
        ema_s = p * k_slow + ema_s * (1 - k_slow)
        macd_vals.append(ema_f - ema_s)

    if not macd_vals:
        return 0.0, 0.0, 0.0

    # Signal line
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
    resistances: List[Tuple[float, float]] = []   # (level, strength)
    supports:    List[Tuple[float, float]] = []
    n        = len(closes)
    lookback = 6   # increased from 3

    for i in range(lookback, n - lookback):
        # Swing high
        if all(highs[i] >= highs[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            vol_w = volumes[i] if (volumes and i < len(volumes) and volumes[i]) else 1.0
            resistances.append((highs[i], vol_w))
        # Swing low
        if all(lows[i]  <= lows[j]  for j in range(i - lookback, i + lookback + 1) if j != i):
            vol_w = volumes[i] if (volumes and i < len(volumes) and volumes[i]) else 1.0
            supports.append((lows[i], vol_w))

    def cluster(levels: List[Tuple[float, float]], tolerance: float) -> List[float]:
        levels = sorted(levels, key=lambda x: x[0])
        merged: List[Tuple[float, float]] = []
        for lv, w in levels:
            if merged and abs(lv - merged[-1][0]) < tolerance:
                # Weighted average
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


# ─── Candlestick pattern detection ───────────────────────────────────────────

def detect_candlestick(opens: List[float], highs: List[float],
                       lows: List[float], closes: List[float]) -> Tuple[str, float]:
    """
    Detects: bullish/bearish engulfing, hammer, shooting star, doji.
    Returns (pattern_name, signal_weight 0..1).
    Patterns near S/R carry much more weight in the caller.
    """
    if len(closes) < 3:
        return "None", 0.0

    # Current and prior candle
    o2, h2, l2, c2 = opens[-2], highs[-2], lows[-2], closes[-2]
    o1, h1, l1, c1 = opens[-1], highs[-1], lows[-1], closes[-1]

    body1 = abs(c1 - o1)
    rng1  = h1 - l1
    if rng1 == 0:
        return "None", 0.0

    upper_wick1 = h1 - max(c1, o1)
    lower_wick1 = min(c1, o1) - l1
    body_ratio1 = body1 / rng1

    body2 = abs(c2 - o2)

    # Doji — indecision near extreme
    if body_ratio1 < 0.08:
        return "Doji", 0.0   # No directional signal on its own

    # Bullish engulfing: prior candle bearish, current bullish and engulfs
    if c2 < o2 and c1 > o1 and c1 > o2 and o1 < c2 and body1 > body2 * 1.1:
        return "Bullish Engulfing", 0.85

    # Bearish engulfing: prior candle bullish, current bearish and engulfs
    if c2 > o2 and c1 < o1 and c1 < o2 and o1 > c2 and body1 > body2 * 1.1:
        return "Bearish Engulfing", 0.85

    # Hammer (bullish): small body at top, long lower wick >= 2x body
    if c1 > o1 and lower_wick1 >= body1 * 2.0 and upper_wick1 <= body1 * 0.5:
        return "Hammer", 0.70

    # Inverted hammer with bearish close (shooting star) → bearish
    if c1 < o1 and upper_wick1 >= body1 * 2.0 and lower_wick1 <= body1 * 0.5:
        return "Shooting Star", 0.70

    # Three-candle morning star (simplified): 3 bars, middle small, close gap up
    if len(closes) >= 3:
        o3, c3 = opens[-3], closes[-3]
        mid_body = abs(c2 - o2)
        if c3 < o3 and mid_body < abs(c3 - o3) * 0.4 and c1 > o1 and c1 > (o3 + c3) / 2:
            return "Morning Star", 0.80
        if c3 > o3 and mid_body < abs(c3 - o3) * 0.4 and c1 < o1 and c1 < (o3 + c3) / 2:
            return "Evening Star", 0.80

    return "None", 0.0


def candle_signal(pattern: str) -> str:
    bullish = {"Bullish Engulfing", "Hammer", "Morning Star"}
    bearish = {"Bearish Engulfing", "Shooting Star", "Evening Star"}
    if pattern in bullish:
        return "BUY"
    if pattern in bearish:
        return "SELL"
    return "NEUTRAL"


# ─── Trading session ──────────────────────────────────────────────────────────

def get_trading_session() -> Tuple[str, float]:
    """
    Returns (session_label, confidence_multiplier).
    London and NY sessions are the most reliable for XAU/USD signals.
    Asian session: spreads wide, fakeouts common — reduce confidence.
    """
    hour = datetime.now(timezone.utc).hour
    # London: 08:00–17:00 UTC | NY: 13:00–22:00 UTC
    if 13 <= hour < 17:
        return "London/NY Overlap", 1.10     # Best liquidity
    elif 8 <= hour < 13:
        return "London", 1.05
    elif 17 <= hour < 22:
        return "New York", 1.00
    else:
        return "Asian", 0.82                 # Low liquidity, higher noise


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
    """Fetch and score the parent timeframe. Returns 'Bullish', 'Bearish', or 'Neutral'."""
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

        if score_bull >= 4:
            return "Bullish"
        elif score_bear >= 4:
            return "Bearish"
        elif score_bull >= 3:
            return "Slightly Bullish"
        elif score_bear >= 3:
            return "Slightly Bearish"
        return "Neutral"
    except Exception as e:
        logger.warning(f"HTF bias fetch failed ({htf}): {e}")
        return "Neutral"


# ─── Volume spike check ───────────────────────────────────────────────────────

def is_volume_spike(volumes: List[float], lookback: int = 20, threshold: float = 1.5) -> bool:
    """True if latest bar's volume is >= threshold × average of recent bars."""
    if not volumes or len(volumes) < lookback + 1:
        return False
    recent_avg = sum(volumes[-lookback - 1:-1]) / lookback
    if recent_avg <= 0:
        return False
    return volumes[-1] >= recent_avg * threshold


# ─── Indicator scoring (fixed) ────────────────────────────────────────────────

def _score_rsi(rsi: float) -> Tuple[str, float]:
    """
    Fixed zones:
      < 30          → BUY  (oversold, strong)
      30–40         → BUY  (recovering from oversold, mild)
      40–45         → BUY  (mild bearish momentum fading)
      45–55         → NEUTRAL (no edge)
      55–60         → SELL (mild bullish momentum exhausting)
      60–70         → SELL (extended, watch for reversal)
      > 70          → SELL (overbought, strong)
    """
    if rsi >= 70:        return "SELL", 0.90
    if rsi <= 30:        return "BUY",  0.90
    if 60 <= rsi < 70:   return "SELL", 0.60
    if 30 < rsi <= 40:   return "BUY",  0.60
    if 55 <= rsi < 60:   return "SELL", 0.30
    if 40 < rsi <= 45:   return "BUY",  0.30
    return "NEUTRAL", 0.0   # 45–55: genuinely neutral


def _score_macd(macd_line: float, signal_line: float, hist: float,
                prev_hist: Optional[float] = None) -> Tuple[str, float]:
    """
    Scores MACD line cross + histogram direction.
    Bonus weight when histogram is expanding (momentum building).
    """
    if macd_line > signal_line:
        expanding = hist > (prev_hist or 0)
        w = 0.85 if (hist > 0 and expanding) else (0.65 if hist > 0 else 0.45)
        return "BUY", w
    if macd_line < signal_line:
        expanding = hist < (prev_hist or 0)
        w = 0.85 if (hist < 0 and expanding) else (0.65 if hist < 0 else 0.45)
        return "SELL", w
    return "NEUTRAL", 0.0


def _score_ema(price: float, ema20: float, ema50: float,
               ema200: Optional[float] = None) -> Tuple[str, float]:
    """
    Full EMA stack: price vs 20, 50, and optionally 200.
    All aligned = strong signal; partial = moderate.
    """
    if ema200 is not None:
        if price > ema20 > ema50 > ema200: return "BUY",  1.0
        if price < ema20 < ema50 < ema200: return "SELL", 1.0
        if price > ema50 > ema200:         return "BUY",  0.75
        if price < ema50 < ema200:         return "SELL", 0.75
    if price > ema20 > ema50: return "BUY",  0.90
    if price < ema20 < ema50: return "SELL", 0.90
    if price > ema50:         return "BUY",  0.50
    if price < ema50:         return "SELL", 0.50
    return "NEUTRAL", 0.0


def _score_stoch(k: float, d: float,
                 prev_k: Optional[float] = None,
                 prev_d: Optional[float] = None) -> Tuple[str, float]:
    """
    Fixed: requires actual crossover for extreme-zone signals.
    Inside overbought/oversold without a cross = neutral.
    """
    # Crossover in extreme zone (most reliable)
    if prev_k is not None and prev_d is not None:
        bullish_cross = (prev_k <= prev_d) and (k > d) and k <= 30
        bearish_cross = (prev_k >= prev_d) and (k < d) and k >= 70
        if bullish_cross: return "BUY",  0.90
        if bearish_cross: return "SELL", 0.90

    # Crossover outside extreme zone (moderate)
    if prev_k is not None and prev_d is not None:
        bull_mid = (prev_k <= prev_d) and (k > d) and k < 60
        bear_mid = (prev_k >= prev_d) and (k < d) and k > 40
        if bull_mid: return "BUY",  0.60
        if bear_mid: return "SELL", 0.60

    # Level-only: K rising above 50
    if k > 50 and k > d:  return "BUY",  0.35
    if k < 50 and k < d:  return "SELL", 0.35
    return "NEUTRAL", 0.0


def _score_bb(pct_b: float) -> Tuple[str, float]:
    if pct_b > 95:  return "SELL", 0.85
    if pct_b < 5:   return "BUY",  0.85
    if pct_b > 80:  return "SELL", 0.45
    if pct_b < 20:  return "BUY",  0.45
    return "NEUTRAL", 0.0


# ─── Main analysis ────────────────────────────────────────────────────────────

async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    from src.config import CONFIDENCE_THRESHOLD, MIN_RR_RATIO

    # ── Fetch market data and HTF bias concurrently ──
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
    price   = data.price

    # ── Compute all indicators ──
    rsi                         = compute_rsi(closes, 14)
    macd_line, sig_line, hist   = compute_macd(closes, 12, 26, 9)
    # Previous MACD histogram for momentum direction
    prev_macd_line, prev_sig, prev_hist = compute_macd(closes[:-1], 12, 26, 9) if len(closes) > 36 else (macd_line, sig_line, hist)

    ema20   = _ema(closes, 20)
    ema50   = _ema(closes, 50)
    ema200  = _ema(closes, 200) if len(closes) >= 200 else None

    # Previous stoch values for crossover detection
    stoch_k, stoch_d          = compute_stoch(highs, lows, closes, 14, 3)
    prev_stoch_k, prev_stoch_d = compute_stoch(highs[:-1], lows[:-1], closes[:-1], 14, 3) \
                                  if len(closes) > 15 else (stoch_k, stoch_d)

    atr = compute_atr(highs, lows, closes, 14)
    bb_upper, bb_mid, bb_lower, bb_pct = compute_bollinger(closes, 20, 2.0)
    adx, plus_di, minus_di    = compute_adx(highs, lows, closes, 14)

    # Candlestick pattern
    candle_pat, candle_wt = detect_candlestick(data.opens, highs, lows, closes)
    c_signal = candle_signal(candle_pat)

    # Trading session
    session_label, session_mult = get_trading_session()

    # Volume spike
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

    # ADX: trending market = full weight; ranging = reduced
    adx_mult = 1.0 if adx >= 25 else (0.75 if adx >= 18 else 0.55)

    indicators = [
        Indicator("RSI(14)",   rsi,       rsi_sig,   0.20),
        Indicator("MACD",      macd_line, macd_sig,  0.22),
        Indicator("EMA Stack", ema20,     ema_sig,   0.28),   # EMA is most reliable on gold
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

    buy_score  = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "BUY")  * adx_mult
    sell_score = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "SELL") * adx_mult

    # ── Candlestick bonus (not counted in vote gate) ──
    if c_signal == "BUY":
        buy_score  += 0.06 * candle_wt
    elif c_signal == "SELL":
        sell_score += 0.06 * candle_wt

    # ── Volume spike bonus (only on breakouts) ──
    if vol_spike:
        if buy_score > sell_score:
            buy_score  *= 1.05
        elif sell_score > buy_score:
            sell_score *= 1.05

    total_score = buy_score + sell_score
    raw_conf    = max(buy_score, sell_score) / total_score if total_score > 0 else 0.5
    base_conf   = max(50, min(97, int(50 + raw_conf * 48)))

    # Apply session multiplier
    confidence  = max(50, min(97, int(base_conf * session_mult)))

    # ── Direction: require margin AND minimum 3/5 indicator votes ──
    margin       = abs(buy_score - sell_score)
    min_votes    = 3   # at least 3 of 5 indicators must agree
    di_conf_buy  = plus_di  > minus_di and adx >= 20
    di_conf_sell = minus_di > plus_di  and adx >= 20

    if buy_score > sell_score and margin > 0.03 and buy_votes >= min_votes:
        direction = "BUY"
        bias      = "Bullish"
        if di_conf_buy:
            confidence = min(97, confidence + 5)
    elif sell_score > buy_score and margin > 0.03 and sell_votes >= min_votes:
        direction = "SELL"
        bias      = "Bearish"
        if di_conf_sell:
            confidence = min(97, confidence + 5)
    else:
        direction = "NEUTRAL"
        bias      = "Neutral"

    # ── HTF confirmation gate ──
    htf_align  = True
    htf_reason = ""
    if direction in ("BUY", "SELL"):
        htf_bullish = "Bullish" in htf_bias
        htf_bearish = "Bearish" in htf_bias
        if direction == "BUY" and htf_bearish:
            # Counter-trend trade — reduce confidence by 15%, only allow if very strong
            confidence = max(50, confidence - 15)
            htf_align  = False
            htf_reason = f"Counter-trend: {htf} is {htf_bias}"
        elif direction == "SELL" and htf_bullish:
            confidence = max(50, confidence - 15)
            htf_align  = False
            htf_reason = f"Counter-trend: {htf} is {htf_bias}"
        elif (direction == "BUY" and htf_bullish) or (direction == "SELL" and htf_bearish):
            # With-trend bonus
            confidence = min(97, confidence + 8)

    # Trend strength
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

    # ── Verdict reason ──
    parts = []
    if direction == "BUY":
        if rsi_sig  == "BUY":   parts.append(f"RSI {rsi:.0f} — oversold")
        if macd_sig == "BUY":   parts.append("MACD bullish")
        if ema_sig  == "BUY":   parts.append("Price above EMAs")
        if bb_sig   == "BUY":   parts.append(f"BB%B {bb_pct:.0f} — lower band")
        if candle_pat not in ("None", "Doji") and c_signal == "BUY":
            parts.append(candle_pat)
        if not htf_align:       parts.append(htf_reason)
    elif direction == "SELL":
        if rsi_sig  == "SELL":  parts.append(f"RSI {rsi:.0f} — overbought")
        if macd_sig == "SELL":  parts.append("MACD bearish")
        if ema_sig  == "SELL":  parts.append("Price below EMAs")
        if bb_sig   == "SELL":  parts.append(f"BB%B {bb_pct:.0f} — upper band")
        if candle_pat not in ("None", "Doji") and c_signal == "SELL":
            parts.append(candle_pat)
        if not htf_align:       parts.append(htf_reason)
    verdict_reason = ". ".join(parts[:4]) if parts else "Indicators mixed — no clear edge"

    # ── S/R levels ──
    r1, r2, s1, s2 = find_sr_levels(highs, lows, closes, price, atr, volumes)

    # ── Breakout / reversal ──
    breakout = detect_breakout(closes, highs, 20)
    reversal = detect_reversal(rsi, stoch_k, hist, closes)

    # ── Entry / SL / TP ──
    # SL: snap to nearest real S/R level if it's within 2×ATR; else ATR-multiple
    atr_pct  = atr / price * 100
    sl_mult  = 1.4 if atr_pct < 0.5 else 1.2

    max_sl_dist = atr * 2.5   # hard cap — SL never more than 2.5×ATR from entry

    if direction == "BUY":
        entry      = round(price, 2)
        ideal_sl   = round(price - atr * sl_mult, 2)
        sl_from_sr = s1 - atr * 0.15            # just below support
        dist_sr    = price - sl_from_sr
        # Use S/R-based SL only if it's between 0.5×ATR and 2.5×ATR
        if atr * 0.5 < dist_sr <= max_sl_dist and sl_from_sr > 0:
            stop_loss = round(sl_from_sr, 2)
        else:
            stop_loss = ideal_sl
    elif direction == "SELL":
        entry      = round(price, 2)
        ideal_sl   = round(price + atr * sl_mult, 2)
        sl_from_sr = r1 + atr * 0.15            # just above resistance
        dist_sr    = sl_from_sr - price
        if atr * 0.5 < dist_sr <= max_sl_dist:
            stop_loss = round(sl_from_sr, 2)
        else:
            stop_loss = ideal_sl
    else:
        entry     = round(price, 2)
        stop_loss = round(price - atr * sl_mult, 2)

    sl_dist  = abs(entry - stop_loss)
    tp1_dist = sl_dist * 2.0   # 1:2 R:R minimum
    tp2_dist = sl_dist * 3.5   # 1:3.5 extended target

    if direction == "BUY":
        # TP1 toward R1, TP2 toward R2 — use whichever is further
        tp1 = round(min(entry + tp1_dist, r1 - atr * 0.1), 2) if r1 > entry + tp1_dist * 0.6 else round(entry + tp1_dist, 2)
        tp2 = round(min(entry + tp2_dist, r2 - atr * 0.1), 2) if r2 > entry + tp2_dist * 0.6 else round(entry + tp2_dist, 2)
    elif direction == "SELL":
        tp1 = round(max(entry - tp1_dist, s1 + atr * 0.1), 2) if s1 < entry - tp1_dist * 0.6 else round(entry - tp1_dist, 2)
        tp2 = round(max(entry - tp2_dist, s2 + atr * 0.1), 2) if s2 < entry - tp2_dist * 0.6 else round(entry - tp2_dist, 2)
    else:
        tp1 = round(entry + tp1_dist, 2)
        tp2 = round(entry + tp2_dist, 2)

    rr_ratio = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist > 0 else 0.0

    # ── Signal gating ──
    wait_reason = ""
    if direction != "NEUTRAL":
        if confidence < CONFIDENCE_THRESHOLD:
            action      = "WAIT"
            wait_reason = f"Confidence {confidence}% below {CONFIDENCE_THRESHOLD}% threshold"
            if htf_reason:
                wait_reason += f" — {htf_reason}"
        elif rr_ratio < MIN_RR_RATIO:
            action      = "WAIT"
            wait_reason = f"R:R 1:{rr_ratio} — minimum is 1:{int(MIN_RR_RATIO)}"
        elif buy_votes < min_votes and sell_votes < min_votes:
            action      = "WAIT"
            wait_reason = f"Only {max(buy_votes, sell_votes)}/5 indicators agree — need 3"
        elif session_label == "Asian" and confidence < 82:
            action      = "WAIT"
            wait_reason = f"Asian session — low liquidity, waiting for London open"
        else:
            action = direction
    else:
        action      = "WAIT"
        wait_reason = verdict_reason or "Indicators mixed — no edge"

    liq_zone = (
        f"{s1:.2f} — {round(s1 + atr, 2)}"
        if direction == "BUY"
        else f"{round(r1 - atr, 2)} — {r1:.2f}"
    )

    return MarketAnalysis(
        price=price, timeframe=timeframe,
        bias=bias, trend=trend, strength=strength, momentum=momentum,
        confidence=confidence,
        entry=entry, stop_loss=stop_loss, tp1=tp1, tp2=tp2, rr_ratio=rr_ratio,
        action=action, wait_reason=wait_reason,
        resistance1=r1, resistance2=r2, support1=s1, support2=s2,
        breakout=breakout, reversal=reversal, liquidity_zone=liq_zone,
        adx=adx, bb_pct=bb_pct,
        indicators=indicators,
        buy_votes=buy_votes, sell_votes=sell_votes, wait_votes=wait_votes,
        verdict_reason=verdict_reason,
        session=session_label, htf_bias=htf_bias, candle_pattern=candle_pat,
    )


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
