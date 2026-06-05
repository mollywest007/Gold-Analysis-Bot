import random
import math
import time
from dataclasses import dataclass
from typing import Tuple

from .market_data import get_gold_price


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


def _seed(price: float, timeframe: str) -> float:
    tf_map = {"M5": 1, "M15": 2, "M30": 3, "H1": 4, "H4": 5, "D1": 6}
    tf_val = tf_map.get(timeframe, 4)
    t_bucket = int(time.time() / (tf_val * 60)) * tf_val
    return (price * 100 + t_bucket) % 1


def _derive_levels(price: float, timeframe: str) -> Tuple[float, float, float, float]:
    tf_scale = {"M5": 0.3, "M15": 0.5, "M30": 0.8, "H1": 1.2, "H4": 2.5, "D1": 5.0}
    scale = tf_scale.get(timeframe, 1.2)
    r1 = round(price + scale * 8, 2)
    r2 = round(price + scale * 18, 2)
    s1 = round(price - scale * 8, 2)
    s2 = round(price - scale * 18, 2)
    return r1, r2, s1, s2


def _oscillator(seed: float) -> float:
    return math.sin(seed * math.pi * 6) * 0.5 + 0.5


async def analyze(timeframe: str = "H1") -> MarketAnalysis:
    price = await get_gold_price()
    seed = _seed(price, timeframe)

    osc = _oscillator(seed)
    osc2 = _oscillator(seed * 1.618)
    osc3 = _oscillator(seed * 2.718)

    combined = (osc * 0.5 + osc2 * 0.3 + osc3 * 0.2)

    if combined > 0.62:
        bias = "Bullish"
        action_raw = "BUY"
    elif combined < 0.38:
        bias = "Bearish"
        action_raw = "SELL"
    else:
        bias = "Neutral"
        action_raw = "WAIT"

    strength_val = abs(combined - 0.5) * 2
    if strength_val > 0.7:
        strength = "Strong"
        trend = bias if bias != "Neutral" else "Ranging"
        momentum = "High"
    elif strength_val > 0.4:
        strength = "Moderate"
        trend = bias if bias != "Neutral" else "Ranging"
        momentum = "Medium"
    else:
        strength = "Weak"
        trend = "Ranging"
        momentum = "Low"

    base_conf = int(55 + strength_val * 40)
    conf_noise = int((osc3 - 0.5) * 8)
    confidence = min(98, max(52, base_conf + conf_noise))

    tf_scale = {"M5": 0.3, "M15": 0.5, "M30": 0.8, "H1": 1.2, "H4": 2.5, "D1": 5.0}
    scale = tf_scale.get(timeframe, 1.2)

    r1, r2, s1, s2 = _derive_levels(price, timeframe)

    if action_raw == "BUY":
        entry = round(price + scale * 0.5, 2)
        stop_loss = round(entry - scale * 8, 2)
        tp1 = round(entry + scale * 8 * 2, 2)
        tp2 = round(entry + scale * 8 * 3.5, 2)
    elif action_raw == "SELL":
        entry = round(price - scale * 0.5, 2)
        stop_loss = round(entry + scale * 8, 2)
        tp1 = round(entry - scale * 8 * 2, 2)
        tp2 = round(entry - scale * 8 * 3.5, 2)
    else:
        entry = price
        stop_loss = round(price - scale * 8, 2)
        tp1 = round(price + scale * 8 * 2, 2)
        tp2 = round(price + scale * 8 * 3.5, 2)

    sl_dist = abs(entry - stop_loss)
    tp1_dist = abs(tp1 - entry)
    rr_ratio = round(tp1_dist / sl_dist, 1) if sl_dist > 0 else 0

    from src.config import CONFIDENCE_THRESHOLD, MIN_RR_RATIO
    wait_reason = ""
    if action_raw != "WAIT":
        if confidence < CONFIDENCE_THRESHOLD:
            action = "WAIT"
            wait_reason = f"Confidence {confidence}% below threshold"
        elif rr_ratio < MIN_RR_RATIO:
            action = "WAIT"
            wait_reason = f"R:R {rr_ratio} below minimum 1:2"
        elif bias == "Neutral":
            action = "WAIT"
            wait_reason = "Market structure unclear"
        else:
            action = action_raw
    else:
        action = "WAIT"
        wait_reason = "No clear directional bias detected"

    breakout = strength == "Strong" and strength_val > 0.75
    reversal = osc2 < 0.15 or osc2 > 0.85

    if combined > 0.55:
        liq_zone = f"{s1} - {round(s1 + scale * 2, 2)}"
    else:
        liq_zone = f"{r1} - {round(r1 - scale * 2, 2)}"

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
    )
