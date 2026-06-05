import logging
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

from .market_data import fetch_ohlcv, OHLCVData

logger = logging.getLogger(__name__)


@dataclass
class Indicator:
    name: str
    value: float
    signal: str
    weight: float


@dataclass
class MarketAnalysis:
    price: float
    timeframe: str
    bias: str
    trend: str
    strength: str
    momentum: str
    confidence: int
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    rr_ratio: float
    action: str
    wait_reason: str
    resistance1: float
    resistance2: float
    support1: float
    support2: float
    breakout: bool
    reversal: bool
    liquidity_zone: str
    indicators: List[Indicator] = field(default_factory=list)
    buy_votes: int = 0
    sell_votes: int = 0
    wait_votes: int = 0
    verdict_reason: str = ""


# ─── Pure TA functions ────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _ema_series(prices: List[float], period: int) -> List[float]:
    if len(prices) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(prices[:period]) / period]
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def compute_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 2:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
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

    if len(macd_vals) < sig:
        signal_val = sum(macd_vals) / len(macd_vals)
    else:
        signal_val = sum(macd_vals[:sig]) / sig
        for m in macd_vals[sig:]:
            signal_val = m * k_sig + signal_val * (1 - k_sig)

    macd_line = macd_vals[-1]
    hist = macd_line - signal_val
    return round(macd_line, 4), round(signal_val, 4), round(hist, 4)


def compute_stoch(highs: List[float], lows: List[float], closes: List[float],
                  k_period: int = 14, d_period: int = 3) -> Tuple[float, float]:
    if len(closes) < k_period:
        return 50.0, 50.0
    k_vals: List[float] = []
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1: i + 1])
        ll = min(lows[i - k_period + 1: i + 1])
        if hh == ll:
            k_vals.append(50.0)
        else:
            k_vals.append((closes[i] - ll) / (hh - ll) * 100)
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
            abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    if len(trs) < period:
        return sum(trs) / len(trs)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def find_sr_levels(highs: List[float], lows: List[float], closes: List[float],
                   price: float, atr: float) -> Tuple[float, float, float, float]:
    resistances: List[float] = []
    supports: List[float] = []
    n = len(closes)
    lookback = 3

    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            resistances.append(highs[i])
        if all(lows[i] <= lows[j] for j in range(i - lookback, i + lookback + 1) if j != i):
            supports.append(lows[i])

    res_above = sorted([r for r in resistances if r > price])
    sup_below = sorted([s for s in supports if s < price], reverse=True)

    r1 = res_above[0] if res_above else round(price + atr * 3, 2)
    r2 = res_above[1] if len(res_above) > 1 else round(price + atr * 6, 2)
    s1 = sup_below[0] if sup_below else round(price - atr * 3, 2)
    s2 = sup_below[1] if len(sup_below) > 1 else round(price - atr * 6, 2)

    return round(r1, 2), round(r2, 2), round(s1, 2), round(s2, 2)


def detect_breakout(closes: List[float], highs: List[float], period: int = 20) -> bool:
    if len(closes) < period + 1:
        return False
    recent_high = max(highs[-period - 1:-1])
    return closes[-1] > recent_high


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
    if rsi >= 70:
        return "SELL", 0.8
    elif rsi <= 30:
        return "BUY", 0.8
    elif 55 <= rsi < 70:
        return "BUY", 0.5
    elif 30 < rsi <= 45:
        return "SELL", 0.5
    else:
        return "NEUTRAL", 0.0


def _score_macd(macd_line: float, signal_line: float, hist: float) -> Tuple[str, float]:
    if macd_line > signal_line and hist > 0:
        strength = min(abs(hist) / max(abs(macd_line), 0.0001), 1.0)
        return "BUY", 0.5 + strength * 0.5
    elif macd_line < signal_line and hist < 0:
        strength = min(abs(hist) / max(abs(macd_line), 0.0001), 1.0)
        return "SELL", 0.5 + strength * 0.5
    elif macd_line > signal_line:
        return "BUY", 0.3
    elif macd_line < signal_line:
        return "SELL", 0.3
    else:
        return "NEUTRAL", 0.0


def _score_ema(price: float, ema20: float, ema50: float) -> Tuple[str, float]:
    if price > ema20 > ema50:
        return "BUY", 1.0
    elif price < ema20 < ema50:
        return "SELL", 1.0
    elif price > ema50:
        return "BUY", 0.5
    elif price < ema50:
        return "SELL", 0.5
    else:
        return "NEUTRAL", 0.0


def _score_stoch(k: float, d: float) -> Tuple[str, float]:
    if k >= 80 and k < d:
        return "SELL", 0.9
    elif k <= 20 and k > d:
        return "BUY", 0.9
    elif k > d and k < 70:
        return "BUY", 0.5
    elif k < d and k > 30:
        return "SELL", 0.5
    else:
        return "NEUTRAL", 0.0


# ─── Main analysis ────────────────────────────────────────────────────────────

async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    from src.config import CONFIDENCE_THRESHOLD, MIN_RR_RATIO

    data: Optional[OHLCVData] = await fetch_ohlcv(timeframe)

    if data is None or len(data) < 30:
        logger.error(f"Insufficient data for {timeframe} — cannot analyze")
        raise RuntimeError(f"Could not fetch enough market data for {timeframe}")

    closes = data.closes
    highs  = data.highs
    lows   = data.lows
    price  = data.price

    # ── Compute indicators ──
    rsi                     = compute_rsi(closes, 14)
    macd_line, sig_line, hist = compute_macd(closes, 12, 26, 9)
    ema20                   = _ema(closes, 20)
    ema50                   = _ema(closes, 50)
    stoch_k, stoch_d        = compute_stoch(highs, lows, closes, 14, 3)
    atr                     = compute_atr(highs, lows, closes, 14)

    logger.info(
        f"[{timeframe}] RSI={rsi} MACD={macd_line:.2f}/{sig_line:.2f} "
        f"EMA20={ema20:.2f} EMA50={ema50:.2f} "
        f"Stoch={stoch_k:.1f}/{stoch_d:.1f} ATR={atr:.2f}"
    )

    # ── Score each indicator ──
    rsi_sig,  rsi_conf  = _score_rsi(rsi)
    macd_sig, macd_conf = _score_macd(macd_line, sig_line, hist)
    ema_sig,  ema_conf  = _score_ema(price, ema20, ema50)
    stoch_sig, stoch_conf = _score_stoch(stoch_k, stoch_d)

    indicators = [
        Indicator("RSI(14)",    rsi,        rsi_sig,   0.25),
        Indicator("MACD",       macd_line,  macd_sig,  0.25),
        Indicator("EMA 20/50",  ema20,      ema_sig,   0.30),
        Indicator("Stoch(14)",  stoch_k,    stoch_sig, 0.20),
    ]

    # ── Weighted vote ──
    buy_score  = sum(i.weight * (rsi_conf if i.name == "RSI(14)" else
                                 macd_conf if i.name == "MACD" else
                                 ema_conf if i.name == "EMA 20/50" else
                                 stoch_conf)
                     for i in indicators if i.signal == "BUY")
    sell_score = sum(i.weight * (rsi_conf if i.name == "RSI(14)" else
                                 macd_conf if i.name == "MACD" else
                                 ema_conf if i.name == "EMA 20/50" else
                                 stoch_conf)
                     for i in indicators if i.signal == "SELL")

    buy_votes  = sum(1 for i in indicators if i.signal == "BUY")
    sell_votes = sum(1 for i in indicators if i.signal == "SELL")
    wait_votes = sum(1 for i in indicators if i.signal == "NEUTRAL")

    total_score = buy_score + sell_score
    margin = abs(buy_score - sell_score)

    if total_score > 0:
        raw_conf = max(buy_score, sell_score) / total_score
    else:
        raw_conf = 0.5

    confidence = int(50 + raw_conf * 48)
    confidence = max(52, min(97, confidence))

    # ── Direction ──
    if buy_score > sell_score and margin > 0.05:
        direction = "BUY"
        bias      = "Bullish"
    elif sell_score > buy_score and margin > 0.05:
        direction = "SELL"
        bias      = "Bearish"
    else:
        direction = "NEUTRAL"
        bias      = "Neutral"

    # ── Trend strength ──
    strength_score = max(buy_votes, sell_votes) / len(indicators)
    if strength_score >= 0.75:
        strength = "Strong"
        momentum = "High"
    elif strength_score >= 0.50:
        strength = "Moderate"
        momentum = "Medium"
    else:
        strength = "Weak"
        momentum = "Low"

    trend = bias if bias != "Neutral" else "Ranging"

    # ── Verdict reason ──
    if direction == "BUY":
        parts = []
        if rsi_sig == "BUY":
            parts.append(f"RSI {rsi:.0f} — momentum building")
        if macd_sig == "BUY":
            parts.append("MACD bullish crossover")
        if ema_sig == "BUY":
            parts.append("Price above key EMAs")
        if stoch_sig == "BUY":
            parts.append(f"Stoch {stoch_k:.0f} — upward pressure")
        verdict_reason = ". ".join(parts[:3])
    elif direction == "SELL":
        parts = []
        if rsi_sig == "SELL":
            parts.append(f"RSI {rsi:.0f} — overbought/bearish")
        if macd_sig == "SELL":
            parts.append("MACD bearish crossover")
        if ema_sig == "SELL":
            parts.append("Price below key EMAs")
        if stoch_sig == "SELL":
            parts.append(f"Stoch {stoch_k:.0f} — downward pressure")
        verdict_reason = ". ".join(parts[:3])
    else:
        verdict_reason = "Indicators mixed — no clear edge"

    # ── Support / Resistance from actual swing levels ──
    r1, r2, s1, s2 = find_sr_levels(highs, lows, closes, price, atr)

    # ── Breakout / Reversal ──
    breakout = detect_breakout(closes, highs, 20)
    reversal = detect_reversal(rsi, stoch_k, hist, closes)

    # ── Entry / SL / TP using ATR ──
    atr_sl_mult  = 1.5
    atr_tp1_mult = 3.0
    atr_tp2_mult = 4.5

    if direction == "BUY":
        entry     = round(price, 2)
        stop_loss = round(price - atr * atr_sl_mult, 2)
        tp1       = round(price + atr * atr_tp1_mult, 2)
        tp2       = round(price + atr * atr_tp2_mult, 2)
    elif direction == "SELL":
        entry     = round(price, 2)
        stop_loss = round(price + atr * atr_sl_mult, 2)
        tp1       = round(price - atr * atr_tp1_mult, 2)
        tp2       = round(price - atr * atr_tp2_mult, 2)
    else:
        entry     = round(price, 2)
        stop_loss = round(price - atr * atr_sl_mult, 2)
        tp1       = round(price + atr * atr_tp1_mult, 2)
        tp2       = round(price + atr * atr_tp2_mult, 2)

    sl_dist  = abs(entry - stop_loss)
    tp1_dist = abs(tp1 - entry)
    rr_ratio = round(tp1_dist / sl_dist, 1) if sl_dist > 0 else 0.0

    # ── Signal gating ──
    wait_reason = ""
    if direction != "NEUTRAL":
        if confidence < CONFIDENCE_THRESHOLD:
            action = "WAIT"
            wait_reason = f"Confidence {confidence}% below {CONFIDENCE_THRESHOLD}% threshold"
        elif rr_ratio < MIN_RR_RATIO:
            action = "WAIT"
            wait_reason = f"R:R 1:{rr_ratio} below minimum 1:{int(MIN_RR_RATIO)}"
        else:
            action = direction
    else:
        action = "WAIT"
        wait_reason = verdict_reason or "Indicators mixed"

    # ── Liquidity zone ──
    if direction == "BUY":
        liq_zone = f"{s1:.2f} — {round(s1 + atr, 2):.2f}"
    else:
        liq_zone = f"{round(r1 - atr, 2):.2f} — {r1:.2f}"

    return MarketAnalysis(
        price=price,
        timeframe=timeframe,
        bias=bias,
        trend=trend,
        strength=strength,
        momentum=momentum,
        confidence=confidence,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        rr_ratio=rr_ratio,
        action=action,
        wait_reason=wait_reason,
        resistance1=r1,
        resistance2=r2,
        support1=s1,
        support2=s2,
        breakout=breakout,
        reversal=reversal,
        liquidity_zone=liq_zone,
        indicators=indicators,
        buy_votes=buy_votes,
        sell_votes=sell_votes,
        wait_votes=wait_votes,
        verdict_reason=verdict_reason,
    )
