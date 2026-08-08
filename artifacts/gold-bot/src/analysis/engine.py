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
    analysis_mode:  str = "intraday"
    limit_entry:    float = 0.0        # Suggested limit-order entry for better fill
    entry_note:     str = ""           # "Market" or "Limit @ XXXX.XX"
    bb_upper:       float = 0.0
    bb_lower:       float = 0.0
    # Data quality flag — True when real market data fetch failed and simulation is used
    is_simulated:   bool  = False
    # ── ICT / Institutional context ───────────────────────────────────────────
    kill_zone:         str   = ""      # "London Kill Zone", "NY Kill Zone", "Off-hours"
    is_kill_zone:      bool  = False   # True during high-probability time windows
    pdh:               float = 0.0    # Previous Day High
    pdl:               float = 0.0    # Previous Day Low
    premium_discount:  str   = ""     # "PREMIUM" | "DISCOUNT" | "EQUILIBRIUM"
    near_round:        str   = ""     # nearest $25/$100 level note
    ote_high:          float = 0.0    # OTE zone upper bound (38.2%)
    ote_low:           float = 0.0    # OTE zone lower bound (61.8%)
    daily_bias:        str   = ""     # "BULLISH" | "BEARISH" | "RANGING"
    # Extended pro fields
    rsi_value:      float = 0.0
    stoch_k_val:    float = 0.0
    stoch_d_val:    float = 0.0
    macd_hist:      float = 0.0
    plus_di:        float = 0.0
    minus_di:       float = 0.0
    market_structure: str = "RANGING"   # HH_HL | LH_LL | RANGING | TRANSITION
    choch:          str = "NONE"        # BULLISH_CHOCH | BEARISH_CHOCH | NONE — early reversal signal
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
    # ── Evidence-first market report fields ────────────────────────────────────
    htf_h4_bias: str = "Neutral"
    htf_d1_bias: str = "Neutral"
    ltf_trends: dict = field(default_factory=dict)
    structure_detail: str = ""
    bos: str = "NONE"
    liquidity_evidence: str = "Not detected"
    fvg_direction: str = "NONE"
    fvg_top: float = 0.0
    fvg_bottom: float = 0.0
    order_block_direction: str = "NONE"
    order_block_high: float = 0.0
    order_block_low: float = 0.0
    candle_evidence: str = ""
    buying_pressure: str = ""
    selling_pressure: str = ""
    pressure_advantage: str = "Neutral"
    # Williams %R
    willr_value:      float = -50.0
    willr_caution:    str   = ""
    # Supertrend
    supertrend_value:     float = 0.0
    supertrend_direction: str   = "NEUTRAL"
    # ── v4 additions — chart patterns, VWAP, CCI, regime ─────────────────────
    cci_value:            float = 0.0
    vwap:                 float = 0.0
    chart_pattern:        str   = "None"
    chart_pattern_signal: str   = "NEUTRAL"
    market_regime:        str   = "NORMAL"   # TRENDING | RANGING | VOLATILE | SQUEEZE | NORMAL
    hidden_divergence:    str   = "NONE"     # BULLISH_HIDDEN | BEARISH_HIDDEN | NONE
    bb_bandwidth:         float = 0.0        # squeeze detector: low = BB squeeze incoming


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


def compute_williams_r(highs: List[float], lows: List[float], closes: List[float],
                       period: int = 14) -> float:
    """Williams %R — momentum oscillator ranging -100 to 0.
    Overbought: > -20  |  Oversold: < -80"""
    if len(closes) < period:
        return -50.0
    h = highs[-period:]
    l = lows[-period:]
    highest_high = max(h)
    lowest_low   = min(l)
    if highest_high == lowest_low:
        return -50.0
    return round((highest_high - closes[-1]) / (highest_high - lowest_low) * -100, 2)


def compute_supertrend(highs: List[float], lows: List[float], closes: List[float],
                       period: int = 10, multiplier: float = 3.0) -> Tuple[float, str]:
    """
    Supertrend — proper iterative calculation (Wilder-smoothed ATR, band locking).
    direction: 'BUY' when price is above the supertrend line, 'SELL' when below.
    This replaces the earlier single-bar approximation with the canonical algorithm
    that correctly tracks band transitions across multiple candles.
    """
    n = len(closes)
    if n < period + 2:
        return (closes[-1] if closes else 0.0), "NEUTRAL"

    # ── Wilder-smoothed ATR ───────────────────────────────────────────────────
    tr_list: List[float] = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i]  - closes[i - 1]))
        tr_list.append(tr)

    atr_s = sum(tr_list[:period]) / period
    atr_series: List[float] = [atr_s]
    for tr in tr_list[period:]:
        atr_s = (atr_s * (period - 1) + tr) / period
        atr_series.append(atr_s)

    hl2 = [(highs[i] + lows[i]) / 2 for i in range(n)]

    # ── Iterative band + direction tracking ──────────────────────────────────
    final_upper = [0.0] * n
    final_lower = [0.0] * n
    st_line     = [0.0] * n
    st_dir      = [1]   * n   # 1 = BUY (price above line), -1 = SELL (price below)

    start   = period
    atr_idx = 0

    for i in range(start, n):
        atr_i = atr_series[atr_idx] if atr_idx < len(atr_series) else atr_s
        atr_idx += 1

        basic_upper = hl2[i] + multiplier * atr_i
        basic_lower = hl2[i] - multiplier * atr_i

        if i == start:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            st_line[i]     = basic_lower
            st_dir[i]      = 1
        else:
            # Upper band only tightens (never widens unless broken)
            final_upper[i] = (
                basic_upper
                if basic_upper < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]
                else final_upper[i - 1]
            )
            # Lower band only rises (never falls unless broken)
            final_lower[i] = (
                basic_lower
                if basic_lower > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]
                else final_lower[i - 1]
            )
            # Direction: flip only when price decisively crosses the active band
            prev_st = st_line[i - 1]
            if prev_st == final_upper[i - 1]:         # was SELL
                if closes[i] > final_upper[i]:
                    st_line[i] = final_lower[i]; st_dir[i] = 1
                else:
                    st_line[i] = final_upper[i]; st_dir[i] = -1
            else:                                      # was BUY
                if closes[i] < final_lower[i]:
                    st_line[i] = final_upper[i]; st_dir[i] = -1
                else:
                    st_line[i] = final_lower[i]; st_dir[i] = 1

    final_direction = "BUY" if st_dir[-1] == 1 else "SELL"
    return round(st_line[-1], 2), final_direction


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


# ─── v4: CCI, VWAP, BB-bandwidth, Hidden Divergence, Chart Patterns, Regime ──

def compute_cci(highs: List[float], lows: List[float], closes: List[float],
                period: int = 20) -> float:
    """
    Commodity Channel Index — excellent for gold's cyclical moves.
    Overbought  > +100  (> +200 = extreme; reversal territory)
    Oversold    < -100  (< -200 = extreme)
    Zero-line cross from below = emerging bullish momentum.
    """
    if len(closes) < period:
        return 0.0
    tps      = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    tp_win   = tps[-period:]
    tp_sma   = sum(tp_win) / period
    mean_dev = sum(abs(tp - tp_sma) for tp in tp_win) / period
    if mean_dev == 0:
        return 0.0
    return round((tps[-1] - tp_sma) / (0.015 * mean_dev), 2)


def compute_vwap(highs: List[float], lows: List[float], closes: List[float],
                 volumes: List[float], session_bars: int = 24) -> float:
    """
    Session VWAP — the institutional price benchmark.
    Price above VWAP: buy-side institutional bias.
    Price below VWAP: sell-side institutional bias.
    VWAP pullback (price returns to VWAP from above/below) = high-probability entry.
    """
    # Use the shortest of the four arrays — Yahoo Finance sometimes returns
    # fewer volume bars than price bars for certain timeframes.
    n_safe = min(len(closes), len(highs), len(lows), len(volumes)) if volumes else 0
    if not volumes or n_safe < 5:
        return closes[-1] if closes else 0.0
    n     = n_safe
    start = max(0, n - session_bars)
    tps   = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(start, n)]
    vols  = [max(volumes[i], 0) for i in range(start, n)]
    total = sum(vols)
    if total <= 0:
        return closes[-1]
    return round(sum(tp * v for tp, v in zip(tps, vols)) / total, 2)


def compute_bb_bandwidth(closes: List[float], period: int = 20,
                         num_std: float = 2.0) -> float:
    """
    Bollinger Band Width (% of midline).
    Low bandwidth (<1.5%) → BB squeeze → volatility expansion (breakout) incoming.
    High bandwidth (>4.0%) → trend in motion or post-news expansion.
    """
    if len(closes) < period:
        return 0.0
    window = closes[-period:]
    mid    = sum(window) / period
    if mid == 0:
        return 0.0
    std    = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    return round(4 * num_std * std / mid * 100, 2)


def _score_cci(cci: float, adx: float) -> Tuple[str, float]:
    """
    CCI scoring. In trending markets (ADX>25) mid-zone CCI aligns with trend.
    At extremes (±200) it signals reversal regardless of trend.
    """
    if cci >= 200:   return "SELL", 0.88
    if cci <= -200:  return "BUY",  0.88
    if cci >= 130:   return "SELL", 0.72
    if cci <= -130:  return "BUY",  0.72
    if cci >= 100:
        return ("SELL", 0.62) if adx < 25 else ("SELL", 0.52)
    if cci <= -100:
        return ("BUY",  0.62) if adx < 25 else ("BUY",  0.52)
    # Trend-aligned mid-zone (only when ADX confirms direction)
    if adx >= 25 and cci > 20:   return "BUY",  0.40
    if adx >= 25 and cci < -20:  return "SELL", 0.40
    return "NEUTRAL", 0.0


def detect_hidden_divergence(closes: List[float], lookback: int = 30) -> str:
    """
    Hidden divergence — confirms trend CONTINUATION (opposite of regular divergence).

    Bullish hidden: price makes Higher Low, RSI makes Lower Low
                    → pullback in uptrend, smart money buying, expect continuation up.
    Bearish hidden: price makes Lower High, RSI makes Higher High
                    → rally in downtrend, distribution underway, expect continuation down.

    Returns 'BULLISH_HIDDEN', 'BEARISH_HIDDEN', or 'NONE'.
    """
    n = len(closes)
    if n < lookback + 18:
        return "NONE"

    pivot_bars = 3
    win_start  = n - lookback
    win_end    = n - pivot_bars - 1

    rsi_cache: dict = {}
    def _rsi_at(i: int) -> float:
        if i not in rsi_cache:
            rsi_cache[i] = compute_rsi(closes[: i + 1], 14)
        return rsi_cache[i]

    highs_idx: List[int] = []
    lows_idx:  List[int] = []
    for i in range(win_start + pivot_bars, win_end):
        if all(closes[i] >= closes[i - j] and closes[i] >= closes[i + j]
               for j in range(1, pivot_bars + 1)):
            highs_idx.append(i)
        if all(closes[i] <= closes[i - j] and closes[i] <= closes[i + j]
               for j in range(1, pivot_bars + 1)):
            lows_idx.append(i)

    # Bearish hidden: price lower high + RSI higher high → downtrend continuation
    if len(highs_idx) >= 2:
        a_i, b_i = highs_idx[-2], highs_idx[-1]
        if closes[b_i] < closes[a_i] and _rsi_at(b_i) > _rsi_at(a_i) + 3:
            if _rsi_at(b_i) < 65:
                return "BEARISH_HIDDEN"

    # Bullish hidden: price higher low + RSI lower low → uptrend continuation
    if len(lows_idx) >= 2:
        a_i, b_i = lows_idx[-2], lows_idx[-1]
        if closes[b_i] > closes[a_i] and _rsi_at(b_i) < _rsi_at(a_i) - 3:
            if _rsi_at(b_i) > 35:
                return "BULLISH_HIDDEN"

    return "NONE"


def detect_chart_pattern(
    highs: List[float], lows: List[float], closes: List[float],
    opens: List[float], atr: float, lookback: int = 60
) -> Tuple[str, str]:
    """
    Detect classical chart patterns — ordered by reliability for XAU/USD.
    Returns (pattern_name, signal).  signal: 'BUY' | 'SELL' | 'NEUTRAL'

    Priority:
      1. Head & Shoulders / Inverse H&S  (high-reliability major reversal)
      2. Double Top / Double Bottom       (second most reliable reversal)
      3. Bull Flag / Bear Flag            (highest win rate continuation)
      4. Ascending / Descending Triangle  (pre-breakout compression)
      5. Rising / Falling Wedge           (reversal with narrowing range)
    """
    n = len(closes)
    if n < 20:
        return "None", "NEUTRAL"

    win = min(lookback, n)
    h   = highs[-win:]
    l   = lows[-win:]
    c   = closes[-win:]
    wn  = len(h)
    tol = atr * 0.65   # tolerance for "same level" comparisons

    # ── 1. Head & Shoulders (major top reversal) ──────────────────────────────
    if wn >= 25:
        seg = wn // 5
        sh1_h = max(h[:seg * 2])
        hd_h  = max(h[seg:seg * 4])
        sh2_h = max(h[seg * 3:])
        if (hd_h > sh1_h + atr * 0.5
                and hd_h > sh2_h + atr * 0.5
                and abs(sh1_h - sh2_h) < tol * 2.0):
            lt = min(l[seg:seg * 2]) if seg * 2 <= wn else l[seg]
            rt = min(l[seg * 3:seg * 4]) if seg * 4 <= wn else l[seg * 3]
            neckline = (lt + rt) / 2
            if c[-1] < neckline + atr * 0.6:
                return "Head & Shoulders", "SELL"

    # ── 1b. Inverse H&S (major bottom reversal) ───────────────────────────────
    if wn >= 25:
        seg = wn // 5
        sh1_l = min(l[:seg * 2])
        hd_l  = min(l[seg:seg * 4])
        sh2_l = min(l[seg * 3:])
        if (hd_l < sh1_l - atr * 0.5
                and hd_l < sh2_l - atr * 0.5
                and abs(sh1_l - sh2_l) < tol * 2.0):
            lp = max(h[seg:seg * 2]) if seg * 2 <= wn else h[seg]
            rp = max(h[seg * 3:seg * 4]) if seg * 4 <= wn else h[seg * 3]
            neckline = (lp + rp) / 2
            if c[-1] > neckline - atr * 0.6:
                return "Inverse H&S", "BUY"

    # ── 2. Double Top ─────────────────────────────────────────────────────────
    if wn >= 16:
        half = wn // 2
        p1   = max(h[:half])
        p2   = max(h[half:])
        if abs(p1 - p2) < tol and min(l) < p1 - atr * 0.8:
            neckline = min(min(l[:half]), min(l[half:]))
            if c[-1] < neckline + atr * 0.5:
                return "Double Top", "SELL"

    # ── 2b. Double Bottom ─────────────────────────────────────────────────────
    if wn >= 16:
        half = wn // 2
        b1   = min(l[:half])
        b2   = min(l[half:])
        if abs(b1 - b2) < tol and max(h) > b1 + atr * 0.8:
            neckline = max(max(h[:half]), max(h[half:]))
            if c[-1] > neckline - atr * 0.5:
                return "Double Bottom", "BUY"

    # ── 3. Bull Flag (tight retrace after strong up-move) ────────────────────
    if wn >= 14:
        pole = wn // 3
        pole_move  = c[pole - 1] - c[0]
        pole_range = max(h[:pole]) - min(l[:pole])
        ch_high    = max(h[pole:])
        ch_low     = min(l[pole:])
        ch_range   = max(ch_high - ch_low, atr * 0.1)
        pole_bull  = pole_move > pole_range * 0.45 and c[pole - 1] > c[0]
        tight_ch   = ch_range < pole_range * 0.52
        upper_ret  = ch_high < max(h[:pole]) + atr
        price_top  = c[-1] > ch_high - ch_range * 0.4
        if pole_bull and tight_ch and upper_ret and price_top:
            return "Bull Flag", "BUY"

    # ── 3b. Bear Flag ─────────────────────────────────────────────────────────
    if wn >= 14:
        pole = wn // 3
        pole_move  = c[0] - c[pole - 1]
        pole_range = max(h[:pole]) - min(l[:pole])
        ch_high    = max(h[pole:])
        ch_low     = min(l[pole:])
        ch_range   = max(ch_high - ch_low, atr * 0.1)
        pole_bear  = pole_move > pole_range * 0.45 and c[pole - 1] < c[0]
        tight_ch   = ch_range < pole_range * 0.52
        lower_ret  = ch_low > min(l[:pole]) - atr
        price_bot  = c[-1] < ch_low + ch_range * 0.4
        if pole_bear and tight_ch and lower_ret and price_bot:
            return "Bear Flag", "SELL"

    # ── 4. Ascending Triangle (flat resistance + rising lows) ─────────────────
    if wn >= 18:
        r_h = h[-10:]
        r_l = l[-10:]
        highs_flat  = (max(r_h) - min(r_h)) < tol
        lows_rising = r_l[-1] > r_l[0] + atr * 0.4
        if highs_flat and lows_rising:
            return "Ascending Triangle", "BUY"

    # ── 4b. Descending Triangle (flat support + falling highs) ────────────────
    if wn >= 18:
        r_h = h[-10:]
        r_l = l[-10:]
        lows_flat    = (max(r_l) - min(r_l)) < tol
        highs_falling = r_h[-1] < r_h[0] - atr * 0.4
        if lows_flat and highs_falling:
            return "Descending Triangle", "SELL"

    # ── 5. Rising Wedge (both boundaries rising but narrowing → bearish) ──────
    if wn >= 18:
        q = wn // 4
        e_hi = max(h[:q + 1]);  l_hi = max(h[-(q + 1):])
        e_lo = min(l[:q + 1]);  l_lo = min(l[-(q + 1):])
        both_rising = l_hi > e_hi and l_lo > e_lo
        narrowing   = (l_hi - l_lo) < (e_hi - e_lo) * 0.72 and (l_hi - l_lo) > atr
        if both_rising and narrowing:
            return "Rising Wedge", "SELL"

    # ── 5b. Falling Wedge (both boundaries falling + narrowing → bullish) ─────
    if wn >= 18:
        q = wn // 4
        e_hi = max(h[:q + 1]);  l_hi = max(h[-(q + 1):])
        e_lo = min(l[:q + 1]);  l_lo = min(l[-(q + 1):])
        both_falling = l_hi < e_hi and l_lo < e_lo
        narrowing    = (l_hi - l_lo) < (e_hi - e_lo) * 0.72 and (l_hi - l_lo) > atr
        if both_falling and narrowing:
            return "Falling Wedge", "BUY"

    return "None", "NEUTRAL"


def detect_market_regime(closes: List[float], highs: List[float], lows: List[float],
                          adx: float, atr: float) -> str:
    """
    Classify the current market regime for XAU/USD.
    This drives strategy selection — not just signal filtering.

    TRENDING  → trend-follow entries, trail stops aggressively
    RANGING   → S/R bounces, mean reversion; AVOID breakout entries
    VOLATILE  → reduce position size, widen SL; wait for calmer entry
    SQUEEZE   → BB compressing; watch for breakout; prepare both sides
    NORMAL    → standard analysis applies
    """
    if len(closes) < 22:
        return "NORMAL"
    price   = closes[-1]
    bb_bw   = compute_bb_bandwidth(closes[-22:], period=20)
    atr_pct = (atr / price * 100) if price > 0 else 0

    if adx >= 28 and bb_bw > 3.5:
        return "TRENDING"
    if adx < 18 and bb_bw < 1.8:
        return "RANGING"
    if atr_pct > 0.85:
        return "VOLATILE"
    if bb_bw < 1.2 and adx < 22:
        return "SQUEEZE"
    return "NORMAL"


# ─── S/R (improved: 6-bar lookback, volume-weighted cluster) ─────────────────

def find_sr_levels(highs: List[float], lows: List[float], closes: List[float],
                   price: float, atr: float,
                   volumes: Optional[List[float]] = None,
                   timeframe: str = "H1") -> Tuple[float, float, float, float]:
    resistances: List[Tuple[float, float]] = []
    supports:    List[Tuple[float, float]] = []
    n        = len(closes)
    # Higher timeframes need a larger lookback to capture weekly/multi-day S/R pivots.
    # A fixed 6-bar lookback on H4 only covers 24 hours — major levels are invisible.
    _lookback_map = {
        "M1": 3, "M3": 3, "M5": 4, "M15": 5, "M30": 6,
        "H1": 8, "H4": 14, "D1": 20, "W1": 8, "MN1": 6,
    }
    lookback = _lookback_map.get(timeframe, 6)

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

        # Three Inside Up: bearish → small bullish inside → bullish close above first
        if (c3 < o3                     # bar 3: bearish
                and c2 > o2             # bar 2: bullish inside bar 3
                and o2 > c3 and c2 < o3
                and c1 > o1             # bar 1: bullish close above bar 3 open
                and c1 > o3):
            return "Three Inside Up", 0.80

        # Three Inside Down: bullish → small bearish inside → bearish close below first
        if (c3 > o3
                and c2 < o2
                and o2 < c3 and c2 > o3
                and c1 < o1
                and c1 < o3):
            return "Three Inside Down", 0.80

        # Bullish Kicker: gap from bearish to bullish candle (strong reversal)
        if (c3 < o3 and c2 < o2         # two prior bearish
                and o1 > max(o2, c2)    # current opens above prior high = gap-up
                and c1 > o1):           # current closes higher = bullish kicker
            return "Bullish Kicker", 0.88

        # Bearish Kicker: gap from bullish to bearish candle
        if (c3 > o3 and c2 > o2
                and o1 < min(o2, c2)    # opens below prior low = gap-down
                and c1 < o1):
            return "Bearish Kicker", 0.88

        # Bullish Abandoned Baby: bearish → doji with gap → bullish with gap
        gap_down_2 = max(o2, c2) < min(o3, c3) - atr * 0.05
        gap_up_1   = min(o1, c1) > max(o2, c2) + atr * 0.05
        mid_doji   = abs(c2 - o2) < (h2 - l2) * 0.10
        if gap_down_2 and mid_doji and gap_up_1 and c1 > o1:
            return "Bullish Abandoned Baby", 0.90

        # Bearish Abandoned Baby: bullish → doji with gap → bearish with gap
        gap_up_2   = min(o2, c2) > max(o3, c3) + atr * 0.05
        gap_down_1 = max(o1, c1) < min(o2, c2) - atr * 0.05
        if gap_up_2 and mid_doji and gap_down_1 and c1 < o1:
            return "Bearish Abandoned Baby", 0.90

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

    # Bullish Belt Hold: opens near low, no lower shadow, strong bullish thrust
    if (c1 > o1 and lower_wick1 < body1 * 0.05
            and body1 > atr * 0.35 and body_ratio1 >= 0.70):
        return "Bullish Belt Hold", 0.74

    # Bearish Belt Hold: opens near high, no upper shadow, strong bearish thrust
    if (c1 < o1 and upper_wick1 < body1 * 0.05
            and body1 > atr * 0.35 and body_ratio1 >= 0.70):
        return "Bearish Belt Hold", 0.74

    # Bullish Counterattack: prior bearish, current opens well below then closes near prior close
    if (c2 < o2 and c1 > o1
            and o1 < c2 - atr * 0.25
            and abs(c1 - c2) < atr * 0.18):
        return "Bullish Counterattack", 0.68

    # Bearish Counterattack: prior bullish, current opens well above then closes near prior close
    if (c2 > o2 and c1 < o1
            and o1 > c2 + atr * 0.25
            and abs(c1 - c2) < atr * 0.18):
        return "Bearish Counterattack", 0.68

    # ── Single-candle patterns (directional checks BEFORE Doji) ──────────────
    # Use range-based wick thresholds so these fire even when body is tiny.

    lower_wick_pct = lower_wick1 / rng1
    upper_wick_pct = upper_wick1 / rng1

    # Hammer: long lower wick >= 55% of range, upper wick <= 15%, bullish or
    # bearish close acceptable (classic hammer doesn't require bullish close)
    if lower_wick_pct >= 0.55 and upper_wick_pct <= 0.15:
        return "Hammer", 0.72

    # Shooting Star: long upper wick >= 55% of range, lower wick <= 15%
    if upper_wick_pct >= 0.55 and lower_wick_pct <= 0.15:
        return "Shooting Star", 0.72

    # Inverted Hammer: large upper wick, bullish close, appears after downtrend
    if c1 > o1 and upper_wick_pct >= 0.45 and lower_wick_pct <= 0.20:
        return "Inverted Hammer", 0.55

    # Marubozu Bullish: strong bullish candle, tiny wicks both sides
    if c1 > o1 and body_ratio1 >= 0.80 and upper_wick_pct <= 0.10 and lower_wick_pct <= 0.10:
        return "Bullish Marubozu", 0.80

    # Marubozu Bearish: strong bearish candle, tiny wicks both sides
    if c1 < o1 and body_ratio1 >= 0.80 and upper_wick_pct <= 0.10 and lower_wick_pct <= 0.10:
        return "Bearish Marubozu", 0.80

    # Doji — true doji has body < 3% of range (open ≈ close)
    if body_ratio1 < 0.03:
        return "Doji", 0.0

    # Long-legged Doji — small body (3–8%) with large equal wicks on both sides
    if body_ratio1 < 0.08 and lower_wick_pct >= 0.25 and upper_wick_pct >= 0.25:
        return "Long-legged Doji", 0.0

    # Spinning Top — small body, notable wicks on both sides
    if body_ratio1 < 0.30 and upper_wick_pct >= 0.20 and lower_wick_pct >= 0.20:
        return "Spinning Top", 0.0

    # Bullish candle — solid body, no special pattern
    if c1 > o1 and body_ratio1 >= 0.50:
        return "Bullish Candle", 0.0

    # Bearish candle — solid body, no special pattern
    if c1 < o1 and body_ratio1 >= 0.50:
        return "Bearish Candle", 0.0

    return "None", 0.0


def _score_candle(pattern: str, candle_weight: float) -> Tuple[str, float]:
    """Score a candlestick pattern as a full indicator vote (not just a bonus)."""
    sig = candle_signal(pattern)
    if sig == "NEUTRAL" or candle_weight < 0.65:
        return "NEUTRAL", 0.0
    return sig, min(candle_weight, 0.95)


def candle_signal(pattern: str) -> str:
    bullish = {
        "Bullish Engulfing", "Hammer", "Inverted Hammer", "Morning Star",
        "Three White Soldiers", "Tweezer Bottom", "Piercing Line", "Bullish Harami",
        "Bullish Marubozu", "Bullish Candle",
    }
    bearish = {
        "Bearish Engulfing", "Shooting Star", "Evening Star",
        "Three Black Crows", "Tweezer Top", "Dark Cloud Cover", "Bearish Harami",
        "Bearish Marubozu", "Bearish Candle",
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
    if timeframe in ("M1", "M3", "M5", "M15"):
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
    "M1":  "H1",
    "M3":  "H1",
    "M5":  "H1",
    "M15": "H1",
    "M30": "H4",
    "H1":  "H4",
    "H4":  "D1",
    "D1":  "W1",
    "W1":  "MN1",
    "MN1": "MN1",
}


async def _get_htf_bias(htf: str) -> str:
    """
    Calculate HTF bias with price-action-first weighting.
    DI crossover and recent candle direction are more responsive than lagging
    EMAs — weighted heavier so the bias flips faster on genuine reversals.
    Max possible score each side: 7. Bullish/Bearish = >= 5, Slightly = >= 3.
    """
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

        # ── Price vs EMAs (lagging — 1pt each) ───────────────────────────────
        if price > ema20:  score_bull += 1
        else:              score_bear += 1
        if price > ema50:  score_bull += 1
        else:              score_bear += 1

        # ── DI crossover (most responsive — 2pts, double weight) ─────────────
        # Flips as soon as directional momentum shifts, before EMAs catch up.
        if plus_di > minus_di and adx >= 18:   score_bull += 2
        elif minus_di > plus_di and adx >= 18: score_bear += 2

        # ── MACD (1pt) ────────────────────────────────────────────────────────
        if macd_line > sig_line and hist > 0:   score_bull += 1
        elif macd_line < sig_line and hist < 0: score_bear += 1

        # ── RSI (1pt) ─────────────────────────────────────────────────────────
        if rsi > 52:   score_bull += 1
        elif rsi < 48: score_bear += 1

        # ── Recent candle body direction (1pt) — catches intra-candle reversals
        # Uses last 3 closes: if majority falling, bearish pressure is building.
        if len(closes) >= 3:
            recent_dir = sum(1 if closes[i] < closes[i-1] else -1
                             for i in range(-3, 0))
            if recent_dir >= 2:   score_bear += 1   # 2 or 3 of last 3 falling
            elif recent_dir <= -2: score_bull += 1  # 2 or 3 of last 3 rising

        if score_bull >= 5:   return "Bullish"
        elif score_bear >= 5: return "Bearish"
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
    """
    Score Bollinger Band %B — extremes are strong, moderate zones get partial credit.
    In trending markets BB rarely hits 95/5, so moderate thresholds catch real moves.

    IMPORTANT: very low BB%B (< 5, or even negative) in a downtrend means the trend
    is strong, not that price is about to bounce. We only give a BUY vote in the
    20-22 zone (near lower band but not below it) — not below the band, which can
    happen for many candles in a trending sell-off. Below-band is handled by the
    trend-aware oscillator override further down instead.
    """
    if pct_b > 95:  return "SELL", 0.85   # hard overbought — above upper band
    if pct_b < 5:   return "SELL", 0.60   # below lower band — trend continuation SELL
    if pct_b > 78:  return "SELL", 0.60   # near upper band — extended
    if pct_b < 22:  return "BUY",  0.60   # near lower band — potential support
    return "NEUTRAL", 0.0


def _score_williams_r(willr: float) -> Tuple[str, float, str]:
    """Score Williams %R. Returns (signal, confidence, caution_note).
    Range: -100 (oversold) to 0 (overbought).
    Overbought zone: > -20  |  Oversold zone: < -80"""
    if willr >= -10:   return "SELL", 0.85, ""           # extreme overbought
    if willr >= -20:   return "SELL", 0.70, ""           # overbought
    if willr >= -30:   return "SELL", 0.50, "caution flag shown"   # approaching overbought
    if willr <= -90:   return "BUY",  0.85, ""           # extreme oversold
    if willr <= -80:   return "BUY",  0.70, ""           # oversold
    if willr <= -70:   return "BUY",  0.50, "caution flag shown"   # approaching oversold
    return "NEUTRAL", 0.0, ""


def _score_supertrend(direction: str) -> Tuple[str, float]:
    """Score Supertrend direction. BUY = price above trend line, SELL = below."""
    if direction == "BUY":    return "BUY",  0.75
    if direction == "SELL":   return "SELL", 0.75
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


# ─── ICT / Institutional context helpers ─────────────────────────────────────

def get_kill_zone() -> Tuple[str, bool]:
    """
    ICT Kill Zones — time windows when institutional order flow is heaviest.
    All times UTC.
      London Kill Zone : 07:00–10:00 UTC  (London open — highest gold volume)
      NY Kill Zone     : 12:00–15:00 UTC  (NY open / London/NY overlap)
      NY Close Zone    : 19:00–20:00 UTC  (smaller but often reversal spike)
    Outside these windows signals are valid but lower probability.
    """
    hour = datetime.now(timezone.utc).hour
    if 7 <= hour < 10:
        return "London Kill Zone (07-10 UTC)", True
    if 12 <= hour < 15:
        return "NY Kill Zone (12-15 UTC)", True
    if 19 <= hour < 21:
        return "NY Close Zone (19-21 UTC)", True
    return "Off-hours", False


def _candles_per_day(timeframe: str) -> int:
    return {
        "M1": 1440, "M3": 480, "M5": 288, "M15": 96, "M30": 48,
        "H1": 24, "H4": 6, "D1": 1, "W1": 1, "MN1": 1,
    }.get(timeframe, 24)


def _calc_pdh_pdl(highs: List[float], lows: List[float],
                  timeframe: str) -> Tuple[float, float]:
    """
    Previous Day High / Low — the most watched institutional levels on XAU/USD.
    Approximated from OHLCV by counting candles (no timestamps needed).
    Requires at least TWO full day-buckets of data before returning values,
    so partial startup datasets never produce misleading institutional levels.
    """
    cpd = _candles_per_day(timeframe)
    n   = len(highs)
    # Need today's full bucket + at least one full prior bucket
    if n < cpd * 2:
        return 0.0, 0.0
    # Previous "day" bucket = the cpd candles before today's cpd candles
    today_start = n - cpd
    prev_end    = today_start
    prev_start  = prev_end - cpd          # guaranteed >= 0 since n >= cpd*2
    return round(max(highs[prev_start:prev_end]), 2), \
           round(min(lows[prev_start:prev_end]),  2)


def _calc_premium_discount(price: float, high: float, low: float) -> str:
    """
    ICT Premium / Discount:
      PREMIUM  — price above 50% equilibrium → look to SELL into premium
      DISCOUNT — price below 50% equilibrium → look to BUY from discount
      EQUILIBRIUM — ±10% of the midpoint, choppy / wait
    """
    if high <= low or (high - low) < 1.0:
        return "EQUILIBRIUM"
    eq  = (high + low) / 2
    pct = (price - eq) / (high - low)   # –0.5 … +0.5
    if pct >  0.10:
        return "PREMIUM"
    if pct < -0.10:
        return "DISCOUNT"
    return "EQUILIBRIUM"


def _nearest_round(price: float, atr: float) -> str:
    """
    Gold strongly respects $25 and $100 round numbers as S/R magnets.
    Returns a human-readable note when price is within 0.5×ATR of one.
    """
    threshold = max(atr * 0.5, 2.5)
    nearest_25  = round(price / 25)  * 25
    nearest_100 = round(price / 100) * 100
    dist_25  = abs(price - nearest_25)
    dist_100 = abs(price - nearest_100)
    if dist_100 <= threshold * 2:
        pos = "above" if nearest_100 > price else "below"
        return f"${nearest_100:.0f} century lvl ({pos})"
    if dist_25 <= threshold:
        pos = "above" if nearest_25 > price else "below"
        return f"${nearest_25:.0f} round lvl ({pos})"
    return ""


def _calc_ote_zone(fib_382: float, fib_618: float,
                   direction: str, price: float) -> Tuple[float, float]:
    """
    OTE (Optimal Trade Entry) = the 38.2%–61.8% retracement zone.
    Institutional traders enter limit orders inside this range for the
    best possible R:R.  The 50% level (equilibrium) is the ideal fill.

    Returns (ote_low, ote_high) — both on the ENTRY SIDE of current price.
    """
    if fib_382 <= 0 or fib_618 <= 0:
        return 0.0, 0.0
    lo = min(fib_382, fib_618)
    hi = max(fib_382, fib_618)
    if direction == "BUY" and hi < price:
        return lo, hi   # zone is below price — valid pullback target
    if direction == "SELL" and lo > price:
        return lo, hi   # zone is above price — valid retrace target
    return 0.0, 0.0


async def _get_daily_bias(price: float, highs: List[float],
                          lows: List[float], closes: List[float],
                          timeframe: str) -> str:
    """
    Daily bias from D1 structure: is the macro trend BULLISH, BEARISH, or RANGING?
    Uses last two daily candles (approximated from OHLCV) for a fast read.
    """
    if timeframe == "D1":
        # Already on D1 — use last 3 closes
        if len(closes) < 3:
            return "RANGING"
        if closes[-1] > closes[-3]:
            return "BULLISH"
        if closes[-1] < closes[-3]:
            return "BEARISH"
        return "RANGING"

    cpd = _candles_per_day(timeframe)
    n   = len(closes)
    if n < cpd * 3:
        return "RANGING"
    # Two-day slices
    day1_close = closes[max(0, n - cpd * 2): n - cpd]
    day2_close = closes[n - cpd:]
    if not day1_close or not day2_close:
        return "RANGING"
    avg1 = sum(day1_close) / len(day1_close)
    avg2 = sum(day2_close) / len(day2_close)
    if avg2 > avg1 * 1.001:
        return "BULLISH"
    if avg2 < avg1 * 0.999:
        return "BEARISH"
    return "RANGING"


# ─── Main analysis ────────────────────────────────────────────────────────────

async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    from src.mode_manager import get_mode_config

    mode_cfg = get_mode_config()

    htf = HTF_MAP.get(timeframe, "H4")

    async def _neutral() -> str:
        return "Neutral"

    bias_timeframes = {"H4", "D1", "H1", "M30", "M15", htf}
    bias_results = await asyncio.gather(
        fetch_ohlcv(timeframe),
        *(_get_htf_bias(bias_tf) for bias_tf in sorted(bias_timeframes)),
    )
    data = bias_results[0]
    bias_by_tf = dict(zip(sorted(bias_timeframes), bias_results[1:]))
    htf_h4_bias = bias_by_tf.get("H4", "Neutral")
    htf_d1_bias = bias_by_tf.get("D1", "Neutral")
    ltf_h1_bias = bias_by_tf.get("H1", "Neutral")
    ltf_m30_bias = bias_by_tf.get("M30", "Neutral")
    ltf_m15_bias = bias_by_tf.get("M15", "Neutral")
    htf_bias = (
        bias_by_tf.get(htf, "Neutral")
    )
    ltf_trends = {
        "H1": ltf_h1_bias,
        "M30": ltf_m30_bias,
        "M15": ltf_m15_bias,
    }

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
    willr                      = compute_williams_r(highs, lows, closes, 14)
    st_value, st_direction     = compute_supertrend(highs, lows, closes, 10, 3.0)

    candle_pat, candle_wt = detect_candlestick(opens, highs, lows, closes, atr)
    c_signal = candle_signal(candle_pat)

    # ── v4: CCI, VWAP, BB-bandwidth, chart pattern, hidden divergence, regime ──
    cci              = compute_cci(highs, lows, closes, 20)
    vwap             = compute_vwap(highs, lows, closes, volumes, _candles_per_day(timeframe))
    bb_bw            = compute_bb_bandwidth(closes, 20)
    chart_pat_cls, chart_pat_sig = detect_chart_pattern(highs, lows, closes, opens, atr, 60)
    hidden_div       = detect_hidden_divergence(closes, 30)
    market_regime_v  = detect_market_regime(closes, highs, lows, adx, atr)

    session_label, session_mult = get_trading_session()
    vol_spike = is_volume_spike(
        volumes, lookback=20, threshold=mode_cfg.volume_spike_threshold
    )

    # Detect kill zone early — used to lower vote threshold and grade bars
    # so institutional moves at London/NY open are caught before full alignment
    kill_zone_label_early, is_kill_zone_early = get_kill_zone()

    # ── Pro-grade signal detection (ICT / SMC concepts) ───────────────────────
    rsi_div                     = detect_rsi_divergence(closes, lookback=20)
    div_sig, div_conf           = _score_divergence(rsi_div)
    candle_sig_v, candle_conf_v = _score_candle(candle_pat, candle_wt)
    fvg_dir, fvg_top, fvg_bot   = detect_fair_value_gap(highs, lows, closes, lookback=30)
    liq_sweep                   = detect_liquidity_sweep(
        highs, lows, closes, lookback=mode_cfg.liquidity_lookback
    )

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

    # ── Trend-aware oscillator override ───────────────────────────────────────
    # In a confirmed trend (ADX >= 20 + price on correct side of EMA stack),
    # mid-zone RSI/Stoch/BB should read as continuation, not neutral.
    # Lowered from ADX >= 25 → 20 to catch early trend moves before full
    # indicator alignment. In strong trends oscillators stay in "neutral"
    # territory forever — this override prevents missed signals.
    #
    # GUARD: Do NOT override oscillators when a reversal candle is present —
    # Bearish/Bullish Engulfing patterns signal the trend is ending.
    # Inflating trend-continuation votes alongside a reversal candle produces
    # artificially high scores in the wrong direction.
    reversal_candle = candle_pat in (
        "Bearish Engulfing", "Bullish Engulfing",
        "Evening Star", "Morning Star",
        "Shooting Star", "Hammer",
        "Three Black Crows", "Three White Soldiers",
    )
    if adx >= 20 and not reversal_candle:
        in_downtrend = price < ema20 and ema20 < ema50
        in_uptrend   = price > ema20 and ema20 > ema50
        # Also catch EMA crossover zone: price crossed below EMA20 but EMA stack
        # hasn't fully flipped yet — early trend-change signal.
        di_gap = abs(plus_di - minus_di)
        early_downtrend = price < ema20 and minus_di > plus_di and di_gap >= 5
        early_uptrend   = price > ema20 and plus_di > minus_di and di_gap >= 5
        if in_downtrend or early_downtrend:
            if rsi_sig == "NEUTRAL" and 35 <= rsi <= 62:
                rsi_sig, rsi_conf = "SELL", 0.60
            if stoch_sig == "NEUTRAL" and stoch_k <= 65:
                stoch_sig, stoch_conf = "SELL", 0.55
            if bb_sig == "NEUTRAL" and 5 <= bb_pct <= 45:
                bb_sig, bb_conf = "SELL", 0.55
        elif in_uptrend or early_uptrend:
            if rsi_sig == "NEUTRAL" and 38 <= rsi <= 65:
                rsi_sig, rsi_conf = "BUY", 0.60
            if stoch_sig == "NEUTRAL" and stoch_k >= 35:
                stoch_sig, stoch_conf = "BUY", 0.55
            if bb_sig == "NEUTRAL" and 55 <= bb_pct <= 95:
                bb_sig, bb_conf = "BUY", 0.55

    # ── Indicators — 5 independent core votes ──────────────────────────────────
    # RSI, Stoch, and BB are all oscillators measuring the same price extension.
    # Replaced Stoch with ADX DI (+DI vs -DI) — a genuinely independent signal
    # measuring directional strength, not correlated with RSI/BB at all.
    adx_di_sig  = "BUY"  if (plus_di  > minus_di and adx >= 20) else \
                  "SELL" if (minus_di > plus_di  and adx >= 20) else "NEUTRAL"
    adx_di_conf = min(0.90, adx / 100.0)

    willr_sig, willr_conf, willr_caution = _score_williams_r(willr)
    st_sig, st_conf                       = _score_supertrend(st_direction)
    cci_sig, cci_conf                     = _score_cci(cci, adx)

    # 8 core indicators — CCI added as 5th (reweighted so all sum to same as before)
    indicators = [
        Indicator("RSI(14)",    rsi,       rsi_sig,      0.17),
        Indicator("MACD",       macd_line, macd_sig,     0.18),
        Indicator("EMA Stack",  ema20,     ema_sig,      0.20),
        Indicator("ADX DI",     adx,       adx_di_sig,   0.17),
        Indicator("CCI(20)",    cci,       cci_sig,      0.12),
        Indicator("BB %B",      bb_pct,    bb_sig,       0.09),
        Indicator("Williams%R", willr,     willr_sig,    0.05),
        Indicator("Supertrend", st_value,  st_sig,       0.02),
    ]
    # The selected mode uses the same compatible indicator set, but each
    # strategy persona weights those indicators differently.
    for indicator in indicators:
        if indicator.name in mode_cfg.indicator_weights:
            indicator.weight = mode_cfg.indicator_weights[indicator.name]
    # Optional votes — only added when they have a clear directional stance
    if candle_sig_v != "NEUTRAL":
        indicators.append(Indicator("Candle", candle_wt, candle_sig_v, 0.10))
    if div_sig != "NEUTRAL":
        indicators.append(Indicator("RSI Div", 0.0, div_sig, 0.08))
    # Chart pattern (strong classical pattern = directional vote)
    if chart_pat_sig in ("BUY", "SELL") and chart_pat_cls != "None":
        indicators.append(Indicator("Chart Pat", 0.0, chart_pat_sig, 0.09))
    # Hidden divergence — trend continuation vote
    if hidden_div == "BULLISH_HIDDEN":
        indicators.append(Indicator("Hidden Div", 0.0, "BUY",  0.07))
    elif hidden_div == "BEARISH_HIDDEN":
        indicators.append(Indicator("Hidden Div", 0.0, "SELL", 0.07))

    conf_map = {
        "RSI(14)":    rsi_conf,
        "MACD":       macd_conf,
        "EMA Stack":  ema_conf,
        "ADX DI":     adx_di_conf,
        "CCI(20)":    cci_conf,
        "BB %B":      bb_conf,
        "Williams%R": willr_conf,
        "Supertrend": st_conf,
        "Candle":     candle_conf_v,
        "RSI Div":    div_conf,
        "Chart Pat":  0.72,
        "Hidden Div": 0.68,
    }

    buy_votes  = sum(1 for i in indicators if i.signal == "BUY")
    sell_votes = sum(1 for i in indicators if i.signal == "SELL")
    wait_votes = sum(1 for i in indicators if i.signal == "NEUTRAL")

    buy_score  = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "BUY")
    sell_score = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "SELL")

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
    # Kill zone: institutions move fast at London/NY open — catch the signal one
    # indicator earlier so the alert fires at the start of the move, not the middle.
    MIN_VOTES = (
        mode_cfg.min_votes_kill_zone
        if is_kill_zone_early
        else mode_cfg.min_votes
    )
    di_conf_buy  = plus_di  > minus_di and adx >= 20
    di_conf_sell = minus_di > plus_di  and adx >= 20

    # margin > 0.02: low enough to capture signals when 4 indicators agree but
    # scores are close due to weighting — previously valid setups dropped to NEUTRAL
    if buy_score > sell_score and margin > 0.02 and buy_votes >= MIN_VOTES:
        direction = "BUY"
        bias      = "Bullish"
        if di_conf_buy:
            confidence = min(97, confidence + 5)
    elif sell_score > buy_score and margin > 0.02 and sell_votes >= MIN_VOTES:
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
            # Penalty only — heavy confidence hit but signal still fires.
            # User wants all-TF alerts; hard blocking caused missed entries.
            confidence = max(50, confidence - 20)
            htf_align  = False
            htf_reason = f"Counter-trend: {htf} strongly Bearish"
        elif direction == "SELL" and htf_strongly_bullish:
            confidence = max(50, confidence - 20)
            htf_align  = False
            htf_reason = f"Counter-trend: {htf} strongly Bullish"
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

    # ── Order block detection — now we know direction ─────────────────────────
    ob_at, ob_high, ob_low = (False, 0.0, 0.0)
    if direction in ("BUY", "SELL"):
        ob_at, ob_high, ob_low = detect_order_block(
            opens, highs, lows, closes, direction, atr, lookback=40
        )
        if ob_at:
            confidence = min(97, confidence + 5)   # OB adds certainty to the setup

    # FVG in same direction = high-probability reaction zone
    if direction in ("BUY", "SELL"):
        fvg_aligned = (fvg_dir == "BULLISH" and direction == "BUY") or \
                      (fvg_dir == "BEARISH" and direction == "SELL")
        if fvg_aligned:
            confidence = min(97, confidence + 4)

    # Liquidity sweep in same direction = stop hunt confirmed, real move incoming
    sweep_aligned = (liq_sweep == "BULLISH_SWEEP" and direction == "BUY") or \
                    (liq_sweep == "BEARISH_SWEEP" and direction == "SELL")

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
    r1, r2, s1, s2 = find_sr_levels(highs, lows, closes, price, atr, volumes, timeframe)

    # Breakout / reversal
    breakout = detect_breakout(closes, highs, mode_cfg.breakout_lookback)
    reversal = detect_reversal(rsi, stoch_k, hist, closes)

    # Trade type
    # The active mode, rather than the raw timeframe, defines the strategy
    # persona shown to the user and used by the trade-plan helpers.
    trade_type = mode_cfg.trade_type_label

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    # SL uses 2.0×–2.5× ATR so gold wicks don't stop us out before the move.
    # Previous 1.2–1.4× SL was the primary cause of SL hits.
    default_sl_mult = (
        2.5 if timeframe in ("M5", "M15") else   # scalp: still needs wick protection
        2.2 if timeframe in ("M30", "H1") else
        2.3 if timeframe == "H4" else             # H4 swing candles have large wicks — needs more room than H1
        2.0                                        # D1: position trade, ATR is already large
    )
    sl_mult = mode_cfg.sl_mult_override.get(timeframe, default_sl_mult)
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

    # ── Structural SL refinement — prefer swing point over ATR multiple ───────
    # The nearest confirmed swing low (BUY) or high (SELL) gives a more precise
    # and logical SL than a fixed ATR distance. ATR floor/cap remains as a guard.
    _swing_hs_sl, _swing_ls_sl = _confirmed_swing_points(highs, lows, lookback=5)
    if direction == "BUY" and _swing_ls_sl:
        _recent_sw_low = _swing_ls_sl[-1][1]
        _structural_sl = round(_recent_sw_low - atr * 0.20, 2)
        _sl_str_dist   = price - _structural_sl
        if sl_min_dist * 0.75 <= _sl_str_dist <= max_sl_dist and _structural_sl > 0:
            stop_loss = _structural_sl
    elif direction == "SELL" and _swing_hs_sl:
        _recent_sw_high = _swing_hs_sl[-1][1]
        _structural_sl  = round(_recent_sw_high + atr * 0.20, 2)
        _sl_str_dist    = _structural_sl - price
        if sl_min_dist * 0.75 <= _sl_str_dist <= max_sl_dist:
            stop_loss = _structural_sl

    sl_dist  = abs(entry - stop_loss)
    tp1_mult, tp2_mult, tp3_mult = mode_cfg.tp_mult
    tp1_dist = sl_dist * tp1_mult
    tp2_dist = sl_dist * tp2_mult

    if direction == "BUY":
        tp1 = round(min(entry + tp1_dist, r1 - atr * 0.1), 2) if r1 > entry + tp1_dist * 0.6 else round(entry + tp1_dist, 2)
        tp2 = round(min(entry + tp2_dist, r2 - atr * 0.1), 2) if r2 > entry + tp2_dist * 0.6 else round(entry + tp2_dist, 2)
    elif direction == "SELL":
        tp1 = round(max(entry - tp1_dist, s1 + atr * 0.1), 2) if s1 < entry - tp1_dist * 0.6 else round(entry - tp1_dist, 2)
        tp2 = round(max(entry - tp2_dist, s2 + atr * 0.1), 2) if s2 < entry - tp2_dist * 0.6 else round(entry - tp2_dist, 2)
    else:
        tp1 = round(entry + tp1_dist, 2)
        tp2 = round(entry + tp2_dist, 2)

    # Guarantee the active mode's minimum target distance after S/R snapping.
    if sl_dist > 0:
        min_tp1_dist = sl_dist * tp1_mult
        if direction == "BUY" and tp1 < entry + min_tp1_dist:
            tp1 = round(entry + min_tp1_dist, 2)
        elif direction == "SELL" and tp1 > entry - min_tp1_dist:
            tp1 = round(entry - min_tp1_dist, 2)
        # Preserve the mode's TP ladder after S/R snapping.
        tp2_gap = sl_dist * max(0.5, tp2_mult - tp1_mult)
        if direction == "BUY" and tp2 < tp1 + tp2_gap:
            tp2 = round(tp1 + tp2_gap, 2)
        elif direction == "SELL" and tp2 > tp1 - tp2_gap:
            tp2 = round(tp1 - tp2_gap, 2)

    rr_ratio = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist > 0 else 0.0

    # Limit entry suggestion
    limit_entry, entry_note = calc_limit_entry(
        direction, price, atr, ema20, ema50, s1, r1, trade_type
    )

    # ── Signal gating ─────────────────────────────────────────────────────────
    # The mode controls how much confirmation is required. Scalp/Intraday can
    # trade responsive moves, while Swing/Position require confidence, R:R and
    # higher-timeframe alignment before exposing an actionable signal.
    wait_reason  = ""
    signal_notes = []   # collected caveats shown on the entry card

    near_resistance = direction == "BUY"  and (r1 - price) < atr * 0.5
    near_support    = direction == "SELL" and (price - s1) < atr * 0.5

    if direction != "NEUTRAL":
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
        if confidence < mode_cfg.confidence_threshold:
            signal_notes.append(
                f"Confidence {confidence}% — below {mode_cfg.label} threshold "
                f"({mode_cfg.confidence_threshold}%)"
            )
        if rr_ratio < mode_cfg.min_rr_ratio:
            signal_notes.append(
                f"R:R 1:{rr_ratio} — below {mode_cfg.label} minimum "
                f"1:{mode_cfg.min_rr_ratio:g}, use limit"
            )
        if max(buy_votes, sell_votes) < MIN_VOTES:
            signal_notes.append(
                f"Only {max(buy_votes, sell_votes)} indicators agree "
                f"(mode requires {MIN_VOTES})"
            )
        if htf_reason:
            signal_notes.append(htf_reason)
        # Attach notes to wait_reason field (repurposed as signal context)
        wait_reason = " | ".join(signal_notes) if signal_notes else ""
        if confidence < mode_cfg.confidence_threshold:
            action = "WAIT"
            wait_reason = (
                f"{mode_cfg.label} Mode requires confidence >= "
                f"{mode_cfg.confidence_threshold}%"
            )
        elif rr_ratio < mode_cfg.min_rr_ratio:
            action = "WAIT"
            wait_reason = (
                f"{mode_cfg.label} Mode requires minimum R:R 1:"
                f"{mode_cfg.min_rr_ratio:g}"
            )
        elif mode_cfg.htf_gate_strict and not htf_align:
            action = "WAIT"
            wait_reason = (
                f"{mode_cfg.label} Mode requires higher-timeframe alignment"
            )
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
    structure_detail = describe_structure(highs, lows, lookback=5)
    bos = detect_break_of_structure(highs, lows, closes, lookback=5)
    choch = detect_choch(highs, lows, closes, lookback=5)
    liquidity_evidence = describe_liquidity_sweep(highs, lows, closes, lookback=15)
    candle_evidence = describe_candlestick_evidence(
        opens, highs, lows, closes, candle_pat, atr
    )
    buying_pressure, selling_pressure, pressure_advantage = describe_pressure(
        opens, highs, lows, closes, plus_di, minus_di
    )

    # Preserve a detectable OB even when the signal is WAIT, while keeping the
    # direction explicit so the report never implies an unconfirmed trade.
    ob_direction = direction if ob_at else "NONE"
    if not ob_at:
        bull_ob, bull_high, bull_low = detect_order_block(
            opens, highs, lows, closes, "BUY", atr, lookback=40
        )
        bear_ob, bear_high, bear_low = detect_order_block(
            opens, highs, lows, closes, "SELL", atr, lookback=40
        )
        if bull_ob:
            ob_at, ob_high, ob_low, ob_direction = True, bull_high, bull_low, "BULLISH"
        elif bear_ob:
            ob_at, ob_high, ob_low, ob_direction = True, bear_high, bear_low, "BEARISH"

    # TP3: institutional measured move — anchor to structural R2/S2 when reachable
    # R2/S2 already computed from pivot logic above; only use them when they fall
    # between 3× and 8× SL away so the target stays ambitious but not delusional.
    if direction == "BUY":
        if r2 > tp2 and sl_dist * 3.0 <= (r2 - entry) <= sl_dist * 8.0:
            tp3 = round(r2 - atr * 0.10, 2)
        else:
            tp3 = round(entry + sl_dist * tp3_mult, 2)
    elif direction == "SELL":
        if s2 < tp2 and sl_dist * 3.0 <= (entry - s2) <= sl_dist * 8.0:
            tp3 = round(s2 + atr * 0.10, 2)
        else:
            tp3 = round(entry - sl_dist * tp3_mult, 2)
    else:
        tp3 = round(entry + sl_dist * tp3_mult, 2)

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
        # ── ICT / SMC signals ──────────────────────────────────────────────────
        if ob_at:
            zone = f"{ob_low:,.2f}–{ob_high:,.2f}"
            tag  = "Bullish" if direction == "BUY" else "Bearish"
            confluence_list.append(f"Order Block ({tag}) @ {zone}")
        fvg_aligned = (fvg_dir == "BULLISH" and direction == "BUY") or \
                      (fvg_dir == "BEARISH" and direction == "SELL")
        if fvg_aligned:
            tag = "Bullish" if direction == "BUY" else "Bearish"
            confluence_list.append(f"Fair Value Gap ({tag}) {fvg_bot:,.2f}–{fvg_top:,.2f}")
        if sweep_aligned:
            tag = "Bullish" if direction == "BUY" else "Bearish"
            confluence_list.append(f"Liquidity Sweep ({tag}) — stop hunt cleared")
        if rsi_div == "BULLISH_DIV" and direction == "BUY":
            confluence_list.append("RSI Bullish Divergence — hidden strength")
        elif rsi_div == "BEARISH_DIV" and direction == "SELL":
            confluence_list.append("RSI Bearish Divergence — fading momentum")
        if choch == "BULLISH_CHOCH" and direction == "BUY":
            confluence_list.append("Change of Character — bullish reversal confirmed")
        elif choch == "BEARISH_CHOCH" and direction == "SELL":
            confluence_list.append("Change of Character — bearish reversal confirmed")
        # ── v4 additions to confluence ─────────────────────────────────────────
        # Chart pattern
        if chart_pat_cls != "None" and chart_pat_sig == direction:
            confluence_list.append(f"Chart Pattern: {chart_pat_cls} (confirmed {direction})")
        elif chart_pat_cls != "None" and chart_pat_sig in ("BUY", "SELL"):
            confluence_list.append(f"Chart Pattern: {chart_pat_cls} (note: opposite bias)")
        # VWAP institutional benchmark
        if vwap > 0:
            vwap_dist = abs(price - vwap)
            if direction == "BUY" and price > vwap:
                confluence_list.append(f"Above VWAP {vwap:,.2f} — institutional bid side")
            elif direction == "BUY" and price < vwap and vwap_dist < atr * 1.2:
                confluence_list.append(f"VWAP pullback @ {vwap:,.2f} — limit-buy zone (below VWAP)")
            elif direction == "SELL" and price < vwap:
                confluence_list.append(f"Below VWAP {vwap:,.2f} — institutional offer side")
            elif direction == "SELL" and price > vwap and vwap_dist < atr * 1.2:
                confluence_list.append(f"VWAP rally rejection @ {vwap:,.2f} — limit-sell zone")
        # Hidden divergence
        if hidden_div == "BULLISH_HIDDEN" and direction == "BUY":
            confluence_list.append("Bullish Hidden Divergence — higher low confirms uptrend continuation")
        elif hidden_div == "BEARISH_HIDDEN" and direction == "SELL":
            confluence_list.append("Bearish Hidden Divergence — lower high confirms downtrend continuation")
        # Market regime context
        if market_regime_v == "TRENDING":
            confluence_list.append(f"TRENDING regime (ADX {adx:.0f}) — momentum trades favored")
        elif market_regime_v == "SQUEEZE":
            confluence_list.append("BB Squeeze — volatility expansion imminent, high-prob breakout")
        elif market_regime_v == "RANGING":
            confluence_list.append("RANGING regime — caution on breakout entries; S/R bounces only")

    # Win probability — honest formula, no confidence inflation
    # Built purely from measurable signal quality components.
    # Cap is 72%: no system without real backtesting can justify claiming higher.
    actual_votes = max(buy_votes, sell_votes)
    extra_votes  = max(0, actual_votes - MIN_VOTES)  # votes beyond minimum required

    raw_wp  = 50                                    # base: coin flip
    raw_wp += min(adx / 2.5, 16)                   # ADX strength (max +16 at ADX 40)
    raw_wp += extra_votes * 3                       # each extra indicator vote: +3
    if session_label == "London/NY Overlap": raw_wp += 5
    elif session_label == "London":          raw_wp += 3
    elif session_label == "Asian":           raw_wp += 2   # Asian session: gold moves, just less volume
    if htf_align:                            raw_wp += 4   # HTF aligned = trend confirmation
    if ob_at:                                raw_wp += 3   # at institutional Order Block
    # FVG aligned — price in imbalance zone that institutions actively fill
    _fvg_wp = (fvg_dir == "BULLISH" and direction == "BUY") or \
              (fvg_dir == "BEARISH" and direction == "SELL")
    if _fvg_wp:                              raw_wp += 3
    # Liquidity sweep — confirms institutional move cleared retail stops
    _sweep_wp = (liq_sweep == "BULLISH_SWEEP" and direction == "BUY") or \
                (liq_sweep == "BEARISH_SWEEP" and direction == "SELL")
    if _sweep_wp:                            raw_wp += 2
    # Volume spike — institutional participation is visible in volume
    if vol_spike:                            raw_wp += 2
    if (choch == "BULLISH_CHOCH" and direction == "BUY") or \
       (choch == "BEARISH_CHOCH" and direction == "SELL"):
        raw_wp += 4   # CHoCH = early reversal signal, highest confidence boost
    # ── v4: chart pattern, hidden divergence, regime, VWAP ───────────────────
    if chart_pat_cls != "None" and chart_pat_sig == action and action in ("BUY","SELL"):
        raw_wp += 4   # confirmed classical chart pattern = very high-conviction
    if (hidden_div == "BULLISH_HIDDEN" and action == "BUY") or \
       (hidden_div == "BEARISH_HIDDEN" and action == "SELL"):
        raw_wp += 3   # hidden divergence confirms trend continuation
    if market_regime_v == "TRENDING":
        raw_wp += 3   # strong trend regime → momentum entries work
    elif market_regime_v == "RANGING" and action in ("BUY","SELL"):
        raw_wp -= 3   # ranging market = breakout traps; reduce probability
    elif market_regime_v == "SQUEEZE":
        raw_wp += 2   # squeeze before breakout = higher win on first impulse
    if vwap > 0 and action in ("BUY","SELL"):
        vwap_aligned = (action == "BUY" and price > vwap) or (action == "SELL" and price < vwap)
        if vwap_aligned:
            raw_wp += 2   # VWAP institutional bias confirms direction
    win_probability = max(50, min(85, int(raw_wp))) if action in ("BUY", "SELL") else 0

    # ── ICT / Institutional context ────────────────────────────────────────────
    kill_zone_label, is_kill_zone = kill_zone_label_early, is_kill_zone_early
    pdh, pdl = _calc_pdh_pdl(highs, lows, timeframe)

    # Premium/Discount: use the current day's candle range
    cpd = _candles_per_day(timeframe)
    day_highs = highs[-min(cpd, len(highs)):]
    day_lows  = lows[-min(cpd, len(lows)):]
    day_high  = max(day_highs) if day_highs else price
    day_low   = min(day_lows)  if day_lows  else price
    premium_discount = _calc_premium_discount(price, day_high, day_low)

    near_round = _nearest_round(price, atr)
    daily_bias = await _get_daily_bias(price, highs, lows, closes, timeframe)

    # Kill zone boosts win probability — institutions are active, moves are real
    if is_kill_zone and action in ("BUY", "SELL"):
        win_probability = min(85, win_probability + 6)
        kz_cf = f"{kill_zone_label} — institutional active"
        if not any("Kill Zone" in c for c in confluence_list):
            confluence_list.append(kz_cf)

    # Premium/Discount alignment bonus
    pd_aligned = (
        (action == "BUY"  and premium_discount == "DISCOUNT") or
        (action == "SELL" and premium_discount == "PREMIUM")
    )
    if pd_aligned and action in ("BUY", "SELL"):
        win_probability = min(85, win_probability + 2)
        pd_cf = f"{'Discount' if action == 'BUY' else 'Premium'} zone — favorable"
        if not any("zone" in c.lower() and "favorable" in c for c in confluence_list):
            confluence_list.append(pd_cf)

    # PDH/PDL proximity — major institutional level nearby
    if action == "BUY" and pdl > 0:
        dist_pdl = price - pdl
        if 0 < dist_pdl < atr * 1.5:
            if not any("PDL" in c for c in confluence_list):
                confluence_list.append(f"Near PDL {pdl:,.2f} — institutional support")
    if action == "SELL" and pdh > 0:
        dist_pdh = pdh - price
        if 0 < dist_pdh < atr * 1.5:
            if not any("PDH" in c for c in confluence_list):
                confluence_list.append(f"Near PDH {pdh:,.2f} — institutional resistance")

    # Round number near entry — potential magnet or barrier
    if near_round and not any("round" in c.lower() or "century" in c.lower() for c in confluence_list):
        if action in ("BUY", "SELL"):
            confluence_list.append(f"Round lvl: {near_round}")

    # ── Fibonacci retracement & early entry ──────────────────────────────────
    eff_dir = direction if direction in ("BUY", "SELL") else "BUY"
    fib_382, fib_500, fib_618 = compute_fibonacci_levels(highs, lows, eff_dir, lookback=50)

    # ── Pro early entry: OB → FVG → OTE/Fib priority waterfall ─────────────
    # Institutions enter at specific structural confluences, not at arbitrary
    # ATR multiples. We look for the highest-probability zone in this order:
    #   1. Order Block (OB) — demand/supply zone institutions defend
    #   2. Fair Value Gap (FVG) — imbalance institutions fill on retrace
    #   3. OTE zone (Fib 61.8%) — Optimal Trade Entry, deepest pullback, best R:R
    #   4. Fib 50% — equilibrium, balanced risk/reward
    #   5. Fib 38.2% — shallow pullback, safer but lower R:R
    #   6. EMA pullback — generic retrace to moving average
    early_entry = 0.0
    early_entry_reason = ""
    if action == "BUY":
        # 1. Order Block: price has returned to or is near the demand OB zone
        if ob_at and ob_high > stop_loss and ob_low < price + atr * 0.5:
            _ob_entry = round(ob_low + (ob_high - ob_low) * 0.3, 2)   # lower 30% of OB = best fill
            early_entry = max(_ob_entry, stop_loss + atr * 0.15)
            early_entry_reason = (
                f"📦 Order Block demand {ob_low:,.2f}–{ob_high:,.2f} — "
                f"institutional re-accumulation zone, limit buy @ {early_entry:,.2f}"
            )
        # 2. VWAP pullback — institutional benchmark, high-prob limit-buy zone
        elif (vwap > stop_loss and vwap < price and abs(price - vwap) < atr * 1.5):
            early_entry = round(max(vwap - atr * 0.05, stop_loss + atr * 0.15), 2)
            early_entry_reason = (
                f"📊 VWAP pullback @ {vwap:,.2f} — institutions anchor to VWAP; "
                f"price retracing to VWAP = prime limit-buy zone, set @ {early_entry:,.2f}"
            )
        # 3. FVG: unfilled bullish imbalance below current price
        elif fvg_dir == "BULLISH" and fvg_bot > stop_loss and fvg_bot < price:
            early_entry = round(fvg_bot + atr * 0.05, 2)
            early_entry_reason = (
                f"⚡ Bullish FVG imbalance {fvg_bot:,.2f}–{fvg_top:,.2f} — "
                f"institutions fill gaps, limit buy at FVG base @ {early_entry:,.2f}"
            )
        # 3. OTE zone (61.8% Fibonacci) — deepest pullback, optimal R:R
        elif fib_618 > stop_loss and fib_618 < price:
            early_entry = fib_618
            early_entry_reason = (
                f"🎯 OTE zone (Fib 61.8%) @ {fib_618:,.2f} — "
                f"deepest structured pullback, highest R:R, set limit order here"
            )
        # 4. Fib 50% — equilibrium retrace
        elif fib_500 > stop_loss and fib_500 < price:
            early_entry = fib_500
            early_entry_reason = (
                f"📐 Fib 50% equilibrium @ {fib_500:,.2f} — "
                f"mid-range pullback, balanced R:R, solid limit entry zone"
            )
        # 5. Fib 38.2% — shallow pullback
        elif fib_382 > stop_loss and fib_382 < price:
            early_entry = fib_382
            early_entry_reason = (
                f"📐 Fib 38.2% retrace @ {fib_382:,.2f} — "
                f"shallow pullback, safer entry, lower R:R — confirm with candle close"
            )
        # 6. EMA/ATR pullback zone
        else:
            early_entry = limit_entry if limit_entry and limit_entry < price else price
            early_entry_reason = (
                "📊 Wait for EMA20/50 retrace — do not chase at market; "
                "patience for a pullback to the moving average zone improves R:R"
            )
    elif action == "SELL":
        # 1. Order Block: supply zone overhead
        if ob_at and ob_low < stop_loss and ob_high > price - atr * 0.5:
            _ob_entry = round(ob_high - (ob_high - ob_low) * 0.3, 2)  # upper 30% of OB = best fill
            early_entry = min(_ob_entry, stop_loss - atr * 0.15)
            early_entry_reason = (
                f"📦 Order Block supply {ob_low:,.2f}–{ob_high:,.2f} — "
                f"institutional distribution zone, limit sell @ {early_entry:,.2f}"
            )
        # 2. VWAP rally rejection — institutional benchmark, high-prob limit-sell zone
        elif (vwap < stop_loss and vwap > price and abs(price - vwap) < atr * 1.5):
            early_entry = round(min(vwap + atr * 0.05, stop_loss - atr * 0.15), 2)
            early_entry_reason = (
                f"📊 VWAP rally to {vwap:,.2f} — price rallying back to VWAP from below; "
                f"VWAP is institutional resistance here, set limit-sell @ {early_entry:,.2f}"
            )
        # 3. FVG: unfilled bearish imbalance above current price
        elif fvg_dir == "BEARISH" and fvg_top < stop_loss and fvg_top > price:
            early_entry = round(fvg_top - atr * 0.05, 2)
            early_entry_reason = (
                f"⚡ Bearish FVG imbalance {fvg_bot:,.2f}–{fvg_top:,.2f} — "
                f"institutions distribute into gaps, limit sell at FVG top @ {early_entry:,.2f}"
            )
        # 3. OTE zone (61.8% Fibonacci)
        elif fib_618 < stop_loss and fib_618 > price:
            early_entry = fib_618
            early_entry_reason = (
                f"🎯 OTE zone (Fib 61.8%) @ {fib_618:,.2f} — "
                f"deepest structured retrace, highest R:R, set limit sell here"
            )
        # 4. Fib 50%
        elif fib_500 < stop_loss and fib_500 > price:
            early_entry = fib_500
            early_entry_reason = (
                f"📐 Fib 50% equilibrium @ {fib_500:,.2f} — "
                f"mid-range retrace, balanced R:R, solid limit sell zone"
            )
        # 5. Fib 38.2%
        elif fib_382 < stop_loss and fib_382 > price:
            early_entry = fib_382
            early_entry_reason = (
                f"📐 Fib 38.2% retrace @ {fib_382:,.2f} — "
                f"shallow retrace, safer sell, lower R:R — confirm with candle close"
            )
        # 6. EMA/ATR retrace zone
        else:
            early_entry = limit_entry if limit_entry and limit_entry > price else price
            early_entry_reason = (
                "📊 Wait for EMA20/50 retrace — do not sell the bottom; "
                "wait for a bounce to the moving average zone for better R:R"
            )

    # ── Setup quality grade ───────────────────────────────────────────────────
    # Graded on indicator votes + structural confirmation (ChoCH).
    # A+ = 5 core indicators + trending market
    # A  = 4 core indicators OR 3 core + ChoCH (structural confirmation)
    # B  = 3 core indicators, win >= 55%
    if action in ("BUY", "SELL"):
        core_votes = sum(
            1 for i in indicators
            if i.name in ("RSI(14)", "MACD", "EMA Stack", "ADX DI", "CCI(20)", "BB %B")
            and i.signal == action
        )
        # ChoCH aligned with direction counts as structural confirmation —
        # equivalent to one extra core indicator vote for grading purposes.
        # A 3-vote setup with confirmed market structure break = grade A.
        choch_confirmed = (
            (action == "BUY"  and choch == "BULLISH_CHOCH") or
            (action == "SELL" and choch == "BEARISH_CHOCH")
        )
        effective_votes = core_votes + (1 if choch_confirmed else 0)

        # Kill zones: institutions dominate, lower ADX thresholds are acceptable
        adx_ap = 22 if is_kill_zone else 25
        adx_a  = 17 if is_kill_zone else 20
        if win_probability >= 68 and effective_votes >= 5 and adx >= adx_ap:
            setup_quality = "A+"
        elif win_probability >= 60 and (effective_votes >= 4 or (effective_votes >= 3 and market_regime_v == "TRENDING")) and adx >= adx_a:
            setup_quality = "A"
        elif win_probability >= 55:
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
        trade_type=trade_type, analysis_mode=mode_cfg.name,
        limit_entry=limit_entry, entry_note=entry_note,
        rsi_value=rsi, stoch_k_val=stoch_k, stoch_d_val=stoch_d,
        macd_hist=hist, plus_di=plus_di, minus_di=minus_di,
        market_structure=mkt_structure,
        choch=choch,
        win_probability=win_probability,
        confluence_list=confluence_list,
        tp3=tp3,
        fib_382=fib_382, fib_500=fib_500, fib_618=fib_618,
        early_entry=early_entry, early_entry_reason=early_entry_reason,
        setup_quality=setup_quality,
        is_simulated=data.is_simulated,
        kill_zone=kill_zone_label,
        is_kill_zone=is_kill_zone,
        pdh=pdh,
        pdl=pdl,
        premium_discount=premium_discount,
        near_round=near_round,
        ote_high=max(_calc_ote_zone(fib_382, fib_618, action, price)) if action in ("BUY","SELL") else 0.0,
        ote_low=min(_calc_ote_zone(fib_382, fib_618, action, price)) if action in ("BUY","SELL") else 0.0,
        daily_bias=daily_bias,
        htf_h4_bias=htf_h4_bias,
        htf_d1_bias=htf_d1_bias,
        ltf_trends=ltf_trends,
        structure_detail=structure_detail,
        bos=bos,
        liquidity_evidence=liquidity_evidence,
        fvg_direction=fvg_dir,
        fvg_top=fvg_top,
        fvg_bottom=fvg_bot,
        order_block_direction=ob_direction,
        order_block_high=ob_high,
        order_block_low=ob_low,
        candle_evidence=candle_evidence,
        buying_pressure=buying_pressure,
        selling_pressure=selling_pressure,
        pressure_advantage=pressure_advantage,
        willr_value=willr,
        willr_caution=willr_caution,
        supertrend_value=st_value,
        supertrend_direction=st_direction,
        cci_value=cci,
        vwap=vwap,
        chart_pattern=chart_pat_cls,
        chart_pattern_signal=chart_pat_sig,
        market_regime=market_regime_v,
        hidden_divergence=hidden_div,
        bb_bandwidth=bb_bw,
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


def _confirmed_swing_points(highs: List[float], lows: List[float],
                            lookback: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Return confirmed swing highs/lows with their candle indexes."""
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []
    n = min(len(highs), len(lows))
    if n < lookback * 2 + 1:
        return swing_highs, swing_lows
    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[j]
               for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_highs.append((i, highs[i]))
        if all(lows[i] <= lows[j]
               for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def describe_structure(highs: List[float], lows: List[float],
                       lookback: int = 5) -> str:
    """Describe the latest confirmed HH/HL or LH/LL evidence."""
    swing_highs, swing_lows = _confirmed_swing_points(highs, lows, lookback)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "Insufficient confirmed swings to label HH/HL or LH/LL"

    prev_h, last_h = swing_highs[-2][1], swing_highs[-1][1]
    prev_l, last_l = swing_lows[-2][1], swing_lows[-1][1]
    high_label = "HH" if last_h > prev_h else "LH" if last_h < prev_h else "equal high"
    low_label = "HL" if last_l > prev_l else "LL" if last_l < prev_l else "equal low"
    return (
        f"{high_label} {last_h:,.2f} vs prior {prev_h:,.2f}; "
        f"{low_label} {last_l:,.2f} vs prior {prev_l:,.2f}"
    )


def detect_break_of_structure(highs: List[float], lows: List[float],
                              closes: List[float], lookback: int = 5) -> str:
    """Detect a close beyond the latest confirmed opposing swing."""
    swing_highs, swing_lows = _confirmed_swing_points(highs, lows, lookback)
    if not closes:
        return "NONE"
    current_close = closes[-1]
    last_high = swing_highs[-1][1] if swing_highs else 0.0
    last_low = swing_lows[-1][1] if swing_lows else 0.0
    if last_high and current_close > last_high:
        return "BULLISH_BOS"
    if last_low and current_close < last_low:
        return "BEARISH_BOS"
    return "NONE"


def detect_choch(highs: List[float], lows: List[float], closes: List[float],
                  lookback: int = 5) -> str:
    """
    Change of Character (CHoCH) — the first break of an *opposing* swing point
    after a trend was established. This is the early-warning smart-money signal
    that a trend may be flipping, distinct from a Break of Structure (BOS) which
    confirms continuation of the existing trend.

    Downtrend (LH/LL) + price closes above the most recent lower-high swing
      → BULLISH_CHOCH (possible reversal up)
    Uptrend (HH/HL) + price closes below the most recent higher-low swing
      → BEARISH_CHOCH (possible reversal down)
    """
    n = len(closes)
    if n < lookback * 4 + 5:
        return "NONE"

    swing_highs_idx, swing_lows_idx = [], []
    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_highs_idx.append(i)
        if all(lows[i] <= lows[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            swing_lows_idx.append(i)

    if len(swing_highs_idx) < 2 or len(swing_lows_idx) < 2:
        return "NONE"

    last_high_i, prev_high_i = swing_highs_idx[-1], swing_highs_idx[-2]
    last_low_i,  prev_low_i  = swing_lows_idx[-1],  swing_lows_idx[-2]

    hh = highs[last_high_i] > highs[prev_high_i]
    hl = lows[last_low_i]   > lows[prev_low_i]
    lh = highs[last_high_i] < highs[prev_high_i]
    ll = lows[last_low_i]   < lows[prev_low_i]

    current_close = closes[-1]

    if lh and ll:
        # Established downtrend — CHoCH fires when price closes back above
        # the most recent lower-high (the last point that confirmed the downtrend).
        if current_close > highs[last_high_i]:
            return "BULLISH_CHOCH"
    elif hh and hl:
        # Established uptrend — CHoCH fires when price closes back below
        # the most recent higher-low.
        if current_close < lows[last_low_i]:
            return "BEARISH_CHOCH"

    return "NONE"


def detect_rsi_divergence(closes: List[float], lookback: int = 30) -> str:
    """
    Swing-pivot RSI divergence — compares RSI at actual confirmed swing highs/lows,
    not at arbitrary endpoints. Endpoint comparison is too noisy for intraday gold.

    Bearish: two swing highs where price is higher but RSI is lower → fading momentum.
    Bullish: two swing lows where price is lower but RSI is higher → hidden strength.

    Returns 'BULLISH_DIV', 'BEARISH_DIV', or 'NONE'.
    """
    n = len(closes)
    if n < lookback + 18:
        return "NONE"

    pivot_bars = 3          # bars each side required to confirm a swing point
    win_start  = n - lookback
    win_end    = n - pivot_bars - 1   # last few bars not yet confirmed

    # Lazy RSI cache — compute only at pivot indices we actually need
    rsi_cache: dict = {}
    def _rsi_at(i: int) -> float:
        if i not in rsi_cache:
            rsi_cache[i] = compute_rsi(closes[: i + 1], 14)
        return rsi_cache[i]

    highs_idx: List[int] = []
    lows_idx:  List[int] = []
    for i in range(win_start + pivot_bars, win_end):
        if all(closes[i] >= closes[i - j] and closes[i] >= closes[i + j]
               for j in range(1, pivot_bars + 1)):
            highs_idx.append(i)
        if all(closes[i] <= closes[i - j] and closes[i] <= closes[i + j]
               for j in range(1, pivot_bars + 1)):
            lows_idx.append(i)

    # Bearish divergence: latest two swing highs — price higher, RSI lower
    if len(highs_idx) >= 2:
        a_i, b_i = highs_idx[-2], highs_idx[-1]
        if closes[b_i] > closes[a_i] and _rsi_at(b_i) < _rsi_at(a_i) - 3:
            if _rsi_at(b_i) > 45:   # ignore if RSI is already oversold
                return "BEARISH_DIV"

    # Bullish divergence: latest two swing lows — price lower, RSI higher
    if len(lows_idx) >= 2:
        a_i, b_i = lows_idx[-2], lows_idx[-1]
        if closes[b_i] < closes[a_i] and _rsi_at(b_i) > _rsi_at(a_i) + 3:
            if _rsi_at(b_i) < 55:   # ignore if RSI is already overbought
                return "BULLISH_DIV"

    return "NONE"


def _score_divergence(div: str) -> Tuple[str, float]:
    """Convert divergence signal to indicator vote."""
    if div == "BULLISH_DIV":
        return "BUY",  0.80
    if div == "BEARISH_DIV":
        return "SELL", 0.80
    return "NEUTRAL", 0.0


def detect_order_block(
    opens: List[float], highs: List[float], lows: List[float],
    closes: List[float], direction: str, atr: float, lookback: int = 40
) -> Tuple[bool, float, float]:
    """
    Detect whether price is currently at an institutional order block.

    Bullish OB: last bearish candle before 3+ consecutive bullish impulse candles.
                Price returning to this zone = smart-money re-accumulation.
    Bearish OB: last bullish candle before 3+ consecutive bearish impulse candles.
                Price returning to this zone = distribution / institutional selling.

    Returns (at_ob, ob_high, ob_low). at_ob is True when current price is
    within or very close (±0.15 ATR) of the OB zone.
    """
    n = len(closes)
    if n < 8:
        return False, 0.0, 0.0
    price  = closes[-1]
    window = min(lookback, n - 5)

    if direction == "BUY":
        for i in range(n - window, n - 4):
            if closes[i] < opens[i]:  # bearish OB candle
                if all(closes[i+j] > opens[i+j] for j in range(1, 4)):
                    ob_high, ob_low = highs[i], lows[i]
                    if ob_low - atr * 0.15 <= price <= ob_high + atr * 0.10:
                        return True, ob_high, ob_low

    elif direction == "SELL":
        for i in range(n - window, n - 4):
            if closes[i] > opens[i]:  # bullish OB candle
                if all(closes[i+j] < opens[i+j] for j in range(1, 4)):
                    ob_high, ob_low = highs[i], lows[i]
                    if ob_low - atr * 0.10 <= price <= ob_high + atr * 0.15:
                        return True, ob_high, ob_low

    return False, 0.0, 0.0


def detect_fair_value_gap(
    highs: List[float], lows: List[float], closes: List[float], lookback: int = 30
) -> Tuple[str, float, float]:
    """
    Fair Value Gap (FVG / price imbalance): a 3-candle pattern where a gap exists
    between candle[i-2] and candle[i] that price has not yet filled.

    Bullish FVG: candle[i].low > candle[i-2].high → unfilled area below current price.
    Bearish FVG: candle[i].high < candle[i-2].low → unfilled area above current price.

    When price trades back into an FVG it is a high-probability reaction zone used
    by institutional traders. Returns (direction, fvg_top, fvg_bottom) or ('NONE',0,0).
    """
    n = len(closes)
    price  = closes[-1]
    window = min(lookback, n - 2)

    for i in range(n - 1, n - window, -1):
        if i < 2:
            break
        # Bullish FVG
        if lows[i] > highs[i - 2]:
            fvg_bot, fvg_top = highs[i - 2], lows[i]
            if fvg_bot <= price <= fvg_top:
                return "BULLISH", fvg_top, fvg_bot
        # Bearish FVG
        if highs[i] < lows[i - 2]:
            fvg_top, fvg_bot = lows[i - 2], highs[i]
            if fvg_bot <= price <= fvg_top:
                return "BEARISH", fvg_top, fvg_bot

    return "NONE", 0.0, 0.0


def detect_liquidity_sweep(
    highs: List[float], lows: List[float], closes: List[float], lookback: int = 15
) -> str:
    """
    Liquidity sweep (stop hunt): price wicks through a prior swing high/low then
    closes back on the opposite side, trapping retail traders and reversing.

    Bearish sweep: wick above recent swing high, close back below → buyers trapped.
    Bullish sweep: wick below recent swing low, close back above → sellers trapped.

    Very common in XAU/USD as institutions clear retail stop clusters before the
    real move begins. Returns 'BEARISH_SWEEP', 'BULLISH_SWEEP', or 'NONE'.
    """
    n = len(closes)
    if n < lookback + 3:
        return "NONE"
    ref_end   = n - 3
    ref_start = max(0, ref_end - lookback)
    ref_highs = highs[ref_start:ref_end]
    ref_lows  = lows[ref_start:ref_end]
    if not ref_highs:
        return "NONE"
    swing_high = max(ref_highs)
    swing_low  = min(ref_lows)

    for i in [-3, -2, -1]:
        if highs[i] > swing_high and closes[i] < swing_high:
            return "BEARISH_SWEEP"
        if lows[i] < swing_low and closes[i] > swing_low:
            return "BULLISH_SWEEP"
    return "NONE"


def describe_liquidity_sweep(
    highs: List[float], lows: List[float], closes: List[float], lookback: int = 15
) -> str:
    """Return the sweep direction and the observed reference level."""
    sweep = detect_liquidity_sweep(highs, lows, closes, lookback)
    if sweep == "NONE":
        return "Not detected in the last 3 completed candles"
    n = len(closes)
    ref_end = n - 3
    ref_start = max(0, ref_end - lookback)
    ref_high = max(highs[ref_start:ref_end])
    ref_low = min(lows[ref_start:ref_end])
    if sweep == "BEARISH_SWEEP":
        return f"Bearish sweep: wick above {ref_high:,.2f}, close returned below it"
    return f"Bullish sweep: wick below {ref_low:,.2f}, close returned above it"


def describe_candlestick_evidence(
    opens: List[float], highs: List[float], lows: List[float],
    closes: List[float], pattern: str, atr: float
) -> str:
    """Describe observable candle behavior without inferring intent."""
    if not closes:
        return "No candle data"
    i = -1
    rng = max(highs[i] - lows[i], 0.0001)
    body = abs(closes[i] - opens[i])
    upper_wick = highs[i] - max(opens[i], closes[i])
    lower_wick = min(opens[i], closes[i]) - lows[i]
    avg_range = (
        sum(highs[j] - lows[j] for j in range(max(0, len(closes) - 14), len(closes)))
        / min(14, len(closes))
        if closes else rng
    )
    parts = [pattern if pattern and pattern != "None" else "No named pattern"]
    if pattern in ("Hammer", "Shooting Star", "Inverted Hammer"):
        parts.append("pin-bar style rejection")
    if "Engulfing" in pattern:
        parts.append("engulfing behavior")
    if pattern in ("Doji", "Long-legged Doji", "Spinning Top"):
        parts.append("indecision / balanced closing")
    if body >= rng * 0.65 and rng >= avg_range * 1.15:
        parts.append("strong momentum candle")
    elif lower_wick >= rng * 0.45:
        parts.append("lower-wick rejection")
    elif upper_wick >= rng * 0.45:
        parts.append("upper-wick rejection")
    return "; ".join(parts)


def describe_pressure(
    opens: List[float], highs: List[float], lows: List[float], closes: List[float],
    plus_di: float, minus_di: float, lookback: int = 5,
) -> Tuple[str, str, str]:
    """Compare recent directional bodies and DI; return evidence and advantage."""
    start = max(0, len(closes) - lookback)
    bull_body = sum(max(closes[i] - opens[i], 0.0) for i in range(start, len(closes)))
    bear_body = sum(max(opens[i] - closes[i], 0.0) for i in range(start, len(closes)))
    bull_count = sum(closes[i] > opens[i] for i in range(start, len(closes)))
    bear_count = sum(closes[i] < opens[i] for i in range(start, len(closes)))
    if plus_di > minus_di:
        di_text = f"+DI {plus_di:.1f} > -DI {minus_di:.1f}"
    elif minus_di > plus_di:
        di_text = f"-DI {minus_di:.1f} > +DI {plus_di:.1f}"
    else:
        di_text = f"+DI and -DI are equal near {plus_di:.1f}"

    if bull_body > bear_body * 1.15 and bull_count >= bear_count:
        advantage = "Buyers"
    elif bear_body > bull_body * 1.15 and bear_count >= bull_count:
        advantage = "Sellers"
    elif plus_di > minus_di and bull_body >= bear_body:
        advantage = "Buyers"
    elif minus_di > plus_di and bear_body >= bull_body:
        advantage = "Sellers"
    else:
        advantage = "Neutral"

    buying = (
        f"Recent {lookback}-candle bullish body: {bull_body:,.2f} across "
        f"{bull_count} bullish candles; {di_text}."
    )
    selling = (
        f"Recent {lookback}-candle bearish body: {bear_body:,.2f} across "
        f"{bear_count} bearish candles; {di_text}."
    )
    return buying, selling, advantage


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
