import logging
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

from .market_data import fetch_ohlcv, OHLCVData

logger = logging.getLogger(__name__)


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
    bb_pct:         float = 0.0    # 0–100 position within Bollinger Band
    indicators: List[Indicator] = field(default_factory=list)
    buy_votes:  int = 0
    sell_votes: int = 0
    wait_votes: int = 0
    verdict_reason: str = ""


# ─── TA functions ─────────────────────────────────────────────────────────────

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


def _wilder_smooth(values: List[float], period: int) -> float:
    """Wilder smoothing (used in ATR / ADX)."""
    if not values:
        return 0.0
    if len(values) <= period:
        return sum(values) / len(values)
    s = sum(values[:period])
    for v in values[period:]:
        s = s - (s / period) + v
    return s / period


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
    ema_s = sum(closes[:slow]) / slow

    macd_vals: List[float] = []
    for i in range(fast, len(closes)):
        ema_f = closes[i] * k_fast + ema_f * (1 - k_fast)
        if i >= slow:
            ema_s = closes[i] * k_slow + ema_s * (1 - k_slow)
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
    """Returns (upper, middle, lower, pct_b)  where pct_b is 0–100."""
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
    """Returns (ADX, +DI, -DI) using Wilder smoothing."""
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
        plus_dms.append(up   if (up > dn and up > 0)   else 0.0)
        minus_dms.append(dn  if (dn > up and dn > 0)   else 0.0)

    # First Wilder smooth
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

    # Smooth DX into ADX
    adx = sum(dx_vals[:period]) / period if len(dx_vals) >= period else sum(dx_vals) / len(dx_vals)
    for dx in dx_vals[period:]:
        adx = (adx * (period - 1) + dx) / period

    # Final +DI / -DI
    if atr14 > 0:
        last_plus_di  = round(100 * plus_dm14  / atr14, 2)
        last_minus_di = round(100 * minus_dm14 / atr14, 2)
    else:
        last_plus_di = last_minus_di = 50.0

    return round(adx, 2), last_plus_di, last_minus_di


def find_sr_levels(highs: List[float], lows: List[float], closes: List[float],
                   price: float, atr: float) -> Tuple[float, float, float, float]:
    resistances: List[float] = []
    supports:    List[float] = []
    n        = len(closes)
    lookback = 3

    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            resistances.append(highs[i])
        if all(lows[i]  <= lows[j]  for j in range(i - lookback, i + lookback + 1) if j != i):
            supports.append(lows[i])

    # Cluster nearby levels (within 0.5 ATR)
    def cluster(levels: List[float], tolerance: float) -> List[float]:
        levels = sorted(levels)
        merged: List[float] = []
        for lv in levels:
            if merged and abs(lv - merged[-1]) < tolerance:
                merged[-1] = (merged[-1] + lv) / 2
            else:
                merged.append(lv)
        return merged

    tol  = atr * 0.5
    resistances = cluster([r for r in resistances if r > price], tol)
    supports    = cluster(sorted([s for s in supports if s < price], reverse=True), tol)

    r1 = resistances[0] if resistances else round(price + atr * 3, 2)
    r2 = resistances[1] if len(resistances) > 1 else round(price + atr * 6, 2)
    s1 = supports[0]    if supports    else round(price - atr * 3, 2)
    s2 = supports[1]    if len(supports) > 1 else round(price - atr * 6, 2)

    return round(r1, 2), round(r2, 2), round(s1, 2), round(s2, 2)


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


# ─── Indicator scoring ────────────────────────────────────────────────────────

def _score_rsi(rsi: float) -> Tuple[str, float]:
    if rsi >= 70:    return "SELL",    0.9
    if rsi <= 30:    return "BUY",     0.9
    if 55 <= rsi < 70: return "BUY",  0.55
    if 30 < rsi <= 45: return "SELL", 0.55
    return "NEUTRAL", 0.0


def _score_macd(macd_line: float, signal_line: float, hist: float) -> Tuple[str, float]:
    if macd_line > signal_line and hist > 0:
        strength = min(abs(hist) / max(abs(macd_line), 0.0001), 1.0)
        return "BUY",  0.5 + strength * 0.5
    if macd_line < signal_line and hist < 0:
        strength = min(abs(hist) / max(abs(macd_line), 0.0001), 1.0)
        return "SELL", 0.5 + strength * 0.5
    if macd_line > signal_line: return "BUY",  0.3
    if macd_line < signal_line: return "SELL", 0.3
    return "NEUTRAL", 0.0


def _score_ema(price: float, ema20: float, ema50: float) -> Tuple[str, float]:
    if price > ema20 > ema50: return "BUY",  1.0
    if price < ema20 < ema50: return "SELL", 1.0
    if price > ema50:         return "BUY",  0.5
    if price < ema50:         return "SELL", 0.5
    return "NEUTRAL", 0.0


def _score_stoch(k: float, d: float) -> Tuple[str, float]:
    if k >= 80 and k < d: return "SELL", 0.9
    if k <= 20 and k > d: return "BUY",  0.9
    if k > d and k < 70:  return "BUY",  0.5
    if k < d and k > 30:  return "SELL", 0.5
    return "NEUTRAL", 0.0


def _score_bb(pct_b: float) -> Tuple[str, float]:
    """Bollinger Band %B: <10 oversold, >90 overbought."""
    if pct_b > 95:  return "SELL", 0.8
    if pct_b < 5:   return "BUY",  0.8
    if pct_b > 80:  return "SELL", 0.4
    if pct_b < 20:  return "BUY",  0.4
    return "NEUTRAL", 0.0


# ─── Main analysis ────────────────────────────────────────────────────────────

async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    from src.config import CONFIDENCE_THRESHOLD, MIN_RR_RATIO

    data: Optional[OHLCVData] = await fetch_ohlcv(timeframe)

    if data is None or len(data) < 30:
        logger.error(f"Insufficient data for {timeframe}")
        raise RuntimeError(f"Could not fetch enough market data for {timeframe}")

    closes = data.closes
    highs  = data.highs
    lows   = data.lows
    price  = data.price     # accurate XAU/USD spot price

    # ── Compute all indicators ──
    rsi                       = compute_rsi(closes, 14)
    macd_line, sig_line, hist = compute_macd(closes, 12, 26, 9)
    ema20                     = _ema(closes, 20)
    ema50                     = _ema(closes, 50)
    stoch_k, stoch_d          = compute_stoch(highs, lows, closes, 14, 3)
    atr                       = compute_atr(highs, lows, closes, 14)
    bb_upper, bb_mid, bb_lower, bb_pct = compute_bollinger(closes, 20, 2.0)
    adx, plus_di, minus_di    = compute_adx(highs, lows, closes, 14)

    logger.info(
        f"[{timeframe}] Price={price:.2f} RSI={rsi} MACD={macd_line:.2f}/{sig_line:.2f} "
        f"EMA20={ema20:.2f} EMA50={ema50:.2f} Stoch={stoch_k:.1f}/{stoch_d:.1f} "
        f"ATR={atr:.2f} BB%={bb_pct:.1f} ADX={adx:.1f}(+DI={plus_di:.1f}/-DI={minus_di:.1f})"
    )

    # ── Score indicators ──
    rsi_sig,   rsi_conf   = _score_rsi(rsi)
    macd_sig,  macd_conf  = _score_macd(macd_line, sig_line, hist)
    ema_sig,   ema_conf   = _score_ema(price, ema20, ema50)
    stoch_sig, stoch_conf = _score_stoch(stoch_k, stoch_d)
    bb_sig,    bb_conf    = _score_bb(bb_pct)

    # ADX acts as a confidence multiplier (trending = more weight, ranging = less)
    adx_mult = 1.0 if adx >= 25 else (0.7 if adx >= 15 else 0.5)

    indicators = [
        Indicator("RSI(14)",   rsi,       rsi_sig,   0.22),
        Indicator("MACD",      macd_line, macd_sig,  0.22),
        Indicator("EMA 20/50", ema20,     ema_sig,   0.26),
        Indicator("Stoch(14)", stoch_k,   stoch_sig, 0.18),
        Indicator("BB %B",     bb_pct,    bb_sig,    0.12),
    ]

    conf_map = {
        "RSI(14)":   rsi_conf,
        "MACD":      macd_conf,
        "EMA 20/50": ema_conf,
        "Stoch(14)": stoch_conf,
        "BB %B":     bb_conf,
    }

    buy_score  = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "BUY")  * adx_mult
    sell_score = sum(i.weight * conf_map[i.name] for i in indicators if i.signal == "SELL") * adx_mult

    buy_votes  = sum(1 for i in indicators if i.signal == "BUY")
    sell_votes = sum(1 for i in indicators if i.signal == "SELL")
    wait_votes = sum(1 for i in indicators if i.signal == "NEUTRAL")

    total_score = buy_score + sell_score
    raw_conf    = max(buy_score, sell_score) / total_score if total_score > 0 else 0.5
    confidence  = max(52, min(97, int(50 + raw_conf * 48)))

    # +DI/-DI confirmation: if ADX > 20, directional bias from DI lines
    di_confirms_buy  = plus_di  > minus_di and adx >= 20
    di_confirms_sell = minus_di > plus_di  and adx >= 20

    margin = abs(buy_score - sell_score)
    if buy_score > sell_score and margin > 0.04:
        direction = "BUY"
        bias      = "Bullish"
        if di_confirms_buy:
            confidence = min(97, confidence + 5)
    elif sell_score > buy_score and margin > 0.04:
        direction = "SELL"
        bias      = "Bearish"
        if di_confirms_sell:
            confidence = min(97, confidence + 5)
    else:
        direction = "NEUTRAL"
        bias      = "Neutral"

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

    # Verdict reason
    parts = []
    if direction == "BUY":
        if rsi_sig  == "BUY":   parts.append(f"RSI {rsi:.0f} — oversold/bullish")
        if macd_sig == "BUY":   parts.append("MACD bullish cross")
        if ema_sig  == "BUY":   parts.append("Price above EMAs")
        if bb_sig   == "BUY":   parts.append(f"BB%B {bb_pct:.0f} — near lower band")
        if di_confirms_buy:      parts.append(f"ADX {adx:.0f} — strong trend")
    elif direction == "SELL":
        if rsi_sig  == "SELL":  parts.append(f"RSI {rsi:.0f} — overbought/bearish")
        if macd_sig == "SELL":  parts.append("MACD bearish cross")
        if ema_sig  == "SELL":  parts.append("Price below EMAs")
        if bb_sig   == "SELL":  parts.append(f"BB%B {bb_pct:.0f} — near upper band")
        if di_confirms_sell:     parts.append(f"ADX {adx:.0f} — strong trend")
    verdict_reason = ". ".join(parts[:3]) if parts else "Indicators mixed — no clear edge"

    # S/R levels
    r1, r2, s1, s2 = find_sr_levels(highs, lows, closes, price, atr)

    # Breakout / reversal
    breakout = detect_breakout(closes, highs, 20)
    reversal = detect_reversal(rsi, stoch_k, hist, closes)

    # Entry / SL / TP (ATR-based, from spot price)
    # Adjust multipliers based on ATR % of price (tighter if volatile)
    atr_pct     = atr / price * 100
    sl_mult     = 1.5 if atr_pct < 0.5 else 1.2
    tp1_mult    = sl_mult * 2.0    # always 1:2 R:R minimum
    tp2_mult    = sl_mult * 3.5

    if direction == "BUY":
        entry     = round(price, 2)
        stop_loss = round(price - atr * sl_mult, 2)
        tp1       = round(price + atr * tp1_mult, 2)
        tp2       = round(price + atr * tp2_mult, 2)
    elif direction == "SELL":
        entry     = round(price, 2)
        stop_loss = round(price + atr * sl_mult, 2)
        tp1       = round(price - atr * tp1_mult, 2)
        tp2       = round(price - atr * tp2_mult, 2)
    else:
        entry     = round(price, 2)
        stop_loss = round(price - atr * sl_mult, 2)
        tp1       = round(price + atr * tp1_mult, 2)
        tp2       = round(price + atr * tp2_mult, 2)

    sl_dist  = abs(entry - stop_loss)
    rr_ratio = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist > 0 else 0.0

    # Signal gating
    wait_reason = ""
    if direction != "NEUTRAL":
        if confidence < CONFIDENCE_THRESHOLD:
            action      = "WAIT"
            wait_reason = f"Confidence {confidence}% below {CONFIDENCE_THRESHOLD}% threshold"
        elif rr_ratio < MIN_RR_RATIO:
            action      = "WAIT"
            wait_reason = f"R:R 1:{rr_ratio} below 1:{int(MIN_RR_RATIO)}"
        else:
            action = direction
    else:
        action      = "WAIT"
        wait_reason = verdict_reason or "Indicators mixed"

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
    )
