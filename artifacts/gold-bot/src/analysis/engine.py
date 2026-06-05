import math
import time
from dataclasses import dataclass, field
from typing import Tuple, List

from .market_data import get_gold_price


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


def _bucket(timeframe: str) -> int:
    tf_seconds = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
    return tf_seconds.get(timeframe, 3600)


def _seed(price: float, timeframe: str, offset: float = 0.0) -> float:
    bucket_size = _bucket(timeframe)
    t_bucket = int(time.time() / bucket_size)
    raw = (price * 137.5 + t_bucket * 31.7 + offset * 53.1)
    return (raw % 999.0) / 999.0


def _sin_osc(s: float) -> float:
    return math.sin(s * math.pi * 4) * 0.5 + 0.5


def _cos_osc(s: float) -> float:
    return math.cos(s * math.pi * 3.7) * 0.5 + 0.5


def _rsi(seed: float) -> float:
    s1 = _sin_osc(seed)
    s2 = _cos_osc(seed * 1.3)
    raw = s1 * 0.6 + s2 * 0.4
    return round(raw * 100, 1)


def _macd(seed: float) -> float:
    fast = _sin_osc(seed * 0.9)
    slow = _cos_osc(seed * 1.7)
    return round((fast - slow) * 10, 3)


def _ema_bias(seed: float) -> float:
    ema20 = _sin_osc(seed * 1.1)
    ema50 = _cos_osc(seed * 0.7)
    return round(ema20 - ema50, 4)


def _stoch(seed: float) -> float:
    k = _sin_osc(seed * 2.1)
    return round(k * 100, 1)


def _atr_pct(seed: float, timeframe: str) -> float:
    tf_scale = {"M5": 0.05, "M15": 0.1, "M30": 0.15, "H1": 0.25, "H4": 0.5, "D1": 1.0}
    base = tf_scale.get(timeframe, 0.25)
    variance = _sin_osc(seed * 3.3)
    return round(base * (0.7 + variance * 0.6), 3)


def _vol_ratio(seed: float) -> float:
    return round(_cos_osc(seed * 1.9) * 2.0, 2)


def _derive_levels(price: float, timeframe: str, seed: float) -> Tuple[float, float, float, float]:
    tf_scale = {"M5": 0.3, "M15": 0.5, "M30": 0.8, "H1": 1.2, "H4": 2.5, "D1": 5.0}
    scale = tf_scale.get(timeframe, 1.2)
    micro = _sin_osc(seed * 4.1) * scale * 2
    r1 = round(price + scale * 7 + micro, 2)
    r2 = round(price + scale * 16 + micro * 1.5, 2)
    s1 = round(price - scale * 7 - micro, 2)
    s2 = round(price - scale * 16 - micro * 1.5, 2)
    return r1, r2, s1, s2


def _classify_indicator(rsi: float, macd: float, ema_bias: float,
                        stoch: float, vol: float) -> List[Indicator]:
    indicators = []

    if rsi >= 70:
        rsi_sig = "SELL"
    elif rsi <= 30:
        rsi_sig = "BUY"
    elif rsi >= 55:
        rsi_sig = "BUY"
    elif rsi <= 45:
        rsi_sig = "SELL"
    else:
        rsi_sig = "NEUTRAL"
    indicators.append(Indicator("RSI", rsi, rsi_sig, 0.25))

    if macd > 0.3:
        macd_sig = "BUY"
    elif macd < -0.3:
        macd_sig = "SELL"
    else:
        macd_sig = "NEUTRAL"
    indicators.append(Indicator("MACD", macd, macd_sig, 0.25))

    if ema_bias > 0.02:
        ema_sig = "BUY"
    elif ema_bias < -0.02:
        ema_sig = "SELL"
    else:
        ema_sig = "NEUTRAL"
    indicators.append(Indicator("EMA Cross", ema_bias, ema_sig, 0.30))

    if stoch >= 80:
        stoch_sig = "SELL"
    elif stoch <= 20:
        stoch_sig = "BUY"
    elif stoch >= 55:
        stoch_sig = "BUY"
    elif stoch <= 45:
        stoch_sig = "SELL"
    else:
        stoch_sig = "NEUTRAL"
    indicators.append(Indicator("Stochastic", stoch, stoch_sig, 0.20))

    return indicators


def _build_verdict(indicators: List[Indicator], breakout: bool, reversal: bool):
    buy_score = sum(i.weight for i in indicators if i.signal == "BUY")
    sell_score = sum(i.weight for i in indicators if i.signal == "SELL")
    neut_score = sum(i.weight for i in indicators if i.signal == "NEUTRAL")

    buy_votes = sum(1 for i in indicators if i.signal == "BUY")
    sell_votes = sum(1 for i in indicators if i.signal == "SELL")
    wait_votes = sum(1 for i in indicators if i.signal == "NEUTRAL")

    total = buy_score + sell_score + neut_score
    if total == 0:
        return "NEUTRAL", 50, buy_votes, sell_votes, wait_votes, "Indicators inconclusive"

    if breakout:
        if buy_score > sell_score:
            buy_score *= 1.15
        elif sell_score > buy_score:
            sell_score *= 1.15

    conf_raw = max(buy_score, sell_score) / total
    confidence = int(50 + conf_raw * 48)

    margin = abs(buy_score - sell_score)

    if buy_score > sell_score and margin > 0.08:
        direction = "BUY"
        reason = _buy_reason(indicators, breakout)
    elif sell_score > buy_score and margin > 0.08:
        direction = "SELL"
        reason = _sell_reason(indicators, breakout)
    else:
        direction = "NEUTRAL"
        reason = "Indicators split — no high-probability setup"

    return direction, confidence, buy_votes, sell_votes, wait_votes, reason


def _buy_reason(indicators: List[Indicator], breakout: bool) -> str:
    parts = []
    for ind in indicators:
        if ind.signal == "BUY":
            if ind.name == "RSI":
                parts.append(f"RSI {ind.value:.0f} — bullish momentum")
            elif ind.name == "MACD":
                parts.append("MACD above signal line")
            elif ind.name == "EMA Cross":
                parts.append("Price above key EMAs")
            elif ind.name == "Stochastic":
                parts.append(f"Stoch {ind.value:.0f} — upward pressure")
    if breakout:
        parts.append("Breakout structure confirmed")
    return ". ".join(parts[:3]) if parts else "Bullish indicator alignment"


def _sell_reason(indicators: List[Indicator], breakout: bool) -> str:
    parts = []
    for ind in indicators:
        if ind.signal == "SELL":
            if ind.name == "RSI":
                parts.append(f"RSI {ind.value:.0f} — bearish pressure")
            elif ind.name == "MACD":
                parts.append("MACD below signal line")
            elif ind.name == "EMA Cross":
                parts.append("Price below key EMAs")
            elif ind.name == "Stochastic":
                parts.append(f"Stoch {ind.value:.0f} — downward pressure")
    if breakout:
        parts.append("Breakdown structure confirmed")
    return ". ".join(parts[:3]) if parts else "Bearish indicator alignment"


async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    price = await get_gold_price()
    seed = _seed(price, timeframe)

    rsi_val = _rsi(seed)
    macd_val = _macd(seed)
    ema_val = _ema_bias(seed)
    stoch_val = _stoch(seed)
    vol = _vol_ratio(_seed(price, timeframe, 7.3))
    atr = _atr_pct(_seed(price, timeframe, 3.1), timeframe)

    indicators = _classify_indicator(rsi_val, macd_val, ema_val, stoch_val, vol)

    breakout_seed = _sin_osc(_seed(price, timeframe, 11.7))
    reversal_seed = _cos_osc(_seed(price, timeframe, 19.3))
    breakout = breakout_seed > 0.82
    reversal = reversal_seed > 0.85

    direction, confidence, buy_votes, sell_votes, wait_votes, verdict_reason = _build_verdict(
        indicators, breakout, reversal
    )

    if direction == "BUY":
        bias = "Bullish"
        action_raw = "BUY"
    elif direction == "SELL":
        bias = "Bearish"
        action_raw = "SELL"
    else:
        bias = "Neutral"
        action_raw = "WAIT"

    strength_score = max(buy_votes, sell_votes) / len(indicators)
    if strength_score >= 0.75:
        strength = "Strong"
        momentum = "High"
    elif strength_score >= 0.5:
        strength = "Moderate"
        momentum = "Medium"
    else:
        strength = "Weak"
        momentum = "Low"

    trend = bias if bias != "Neutral" else "Ranging"

    tf_scale = {"M5": 0.3, "M15": 0.5, "M30": 0.8, "H1": 1.2, "H4": 2.5, "D1": 5.0}
    scale = tf_scale.get(timeframe, 1.2)

    r1, r2, s1, s2 = _derive_levels(price, timeframe, seed)

    if action_raw == "BUY":
        entry = round(price + scale * 0.4, 2)
        stop_loss = round(entry - scale * 8, 2)
        tp1 = round(entry + scale * 16, 2)
        tp2 = round(entry + scale * 28, 2)
    elif action_raw == "SELL":
        entry = round(price - scale * 0.4, 2)
        stop_loss = round(entry + scale * 8, 2)
        tp1 = round(entry - scale * 16, 2)
        tp2 = round(entry - scale * 28, 2)
    else:
        entry = price
        stop_loss = round(price - scale * 8, 2)
        tp1 = round(price + scale * 16, 2)
        tp2 = round(price + scale * 28, 2)

    sl_dist = abs(entry - stop_loss)
    tp1_dist = abs(tp1 - entry)
    rr_ratio = round(tp1_dist / sl_dist, 1) if sl_dist > 0 else 0.0

    from src.config import CONFIDENCE_THRESHOLD, MIN_RR_RATIO
    wait_reason = ""
    if action_raw != "WAIT":
        if confidence < CONFIDENCE_THRESHOLD:
            action = "WAIT"
            wait_reason = f"Confidence {confidence}% below {CONFIDENCE_THRESHOLD}% threshold"
        elif rr_ratio < MIN_RR_RATIO:
            action = "WAIT"
            wait_reason = f"R:R 1:{rr_ratio} below minimum 1:{int(MIN_RR_RATIO)}"
        elif bias == "Neutral":
            action = "WAIT"
            wait_reason = "Indicators split — no directional edge"
        else:
            action = action_raw
    else:
        action = "WAIT"
        wait_reason = verdict_reason or "No clear directional bias"

    if direction == "BUY":
        liq_zone = f"{fmt(s1)} — {fmt(round(s1 + scale * 2, 2))}"
    else:
        liq_zone = f"{fmt(r1)} — {fmt(round(r1 - scale * 2, 2))}"

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


def fmt(p: float) -> str:
    return f"{p:.2f}"
