"""Rule-based market regime classification from actual completed candles."""

from .indicators import atr


def classify_regime(data, structure, atr_value):
    if atr_value is None or atr_value <= 0 or len(data.closes) < 30:
        return "TRANSITIONING / UNCLEAR"
    highs, lows, closes = data.highs, data.lows, data.closes
    ranges = []
    for end in range(15, len(closes) + 1):
        value = atr(highs[:end], lows[:end], closes[:end], 14)
        if value:
            ranges.append(value)
    baseline = sorted(ranges[-50:])[len(ranges[-50:]) // 2] if ranges else atr_value
    recent_range = max(highs[-12:]) - min(lows[-12:])
    last_range = highs[-1] - lows[-1]
    if atr_value >= baseline * 1.8 or last_range >= atr_value * 2.5:
        return "HIGH-VOLATILITY / UNSTABLE"
    if structure["trend"] == "BULLISH":
        return "BULLISH TREND"
    if structure["trend"] == "BEARISH":
        return "BEARISH TREND"
    if recent_range <= atr_value * 7.0:
        return "RANGE-BOUND"
    return "TRANSITIONING / UNCLEAR"
