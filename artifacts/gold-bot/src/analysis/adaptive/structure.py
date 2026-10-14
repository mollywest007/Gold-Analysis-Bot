"""Confirmed swing, market-structure, support/resistance and sweep evidence."""


def pivots(highs, lows, radius=2, lookback=72):
    start = max(radius, len(highs) - lookback)
    end = len(highs) - radius  # never use the latest unfinished neighborhood
    highs_out, lows_out = [], []
    for i in range(start, max(start, end)):
        left_h, right_h = highs[i-radius:i], highs[i+1:i+radius+1]
        left_l, right_l = lows[i-radius:i], lows[i+1:i+radius+1]
        if len(left_h) != radius or len(right_h) != radius:
            continue
        if highs[i] >= max(left_h + right_h):
            highs_out.append((i, float(highs[i])))
        if lows[i] <= min(left_l + right_l):
            lows_out.append((i, float(lows[i])))
    return highs_out, lows_out


def analyze_structure(data, atr_value, radius=2, lookback=72):
    highs, lows, closes = data.highs, data.lows, data.closes
    swing_highs, swing_lows = pivots(highs, lows, radius, lookback)
    trend = "UNCLEAR"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        old_h, new_h = swing_highs[-2][1], swing_highs[-1][1]
        old_l, new_l = swing_lows[-2][1], swing_lows[-1][1]
        delta = max(float(atr_value or 0) * .03, .01)
        if new_h > old_h + delta and new_l > old_l + delta:
            trend = "BULLISH"
        elif new_h < old_h - delta and new_l < old_l - delta:
            trend = "BEARISH"
        elif new_h <= old_h + delta and new_l >= old_l - delta:
            trend = "RANGE"
    last = float(closes[-1])
    bos = "NONE"
    if swing_highs and last > swing_highs[-1][1] + max(float(atr_value or 0) * .03, .01):
        bos = "BULLISH"
    elif swing_lows and last < swing_lows[-1][1] - max(float(atr_value or 0) * .03, .01):
        bos = "BEARISH"
    supports = sorted({p for _, p in swing_lows if p < last}, reverse=True)
    resistances = sorted({p for _, p in swing_highs if p > last})
    return {
        "trend": trend, "bos": bos, "highs": swing_highs, "lows": swing_lows,
        "support": supports[0] if supports else None,
        "support_next": supports[1] if len(supports) > 1 else None,
        "resistance": resistances[0] if resistances else None,
        "resistance_next": resistances[1] if len(resistances) > 1 else None,
        "last": last,
    }


def liquidity_sweep(data, lookback=16, minimum_atr=.06, atr_value=0.0):
    if len(data.closes) < lookback + 2:
        return None
    i = len(data.closes) - 1
    start = max(0, i - lookback)
    prior_high = max(data.highs[start:i])
    prior_low = min(data.lows[start:i])
    body = abs(float(data.closes[i]) - float(data.opens[i]))
    penetration = max(float(atr_value or 0) * minimum_atr, .01)
    bullish = (
        data.lows[i] < prior_low - penetration
        and data.closes[i] > prior_low
        and data.closes[i] > data.opens[i]
        and data.closes[i] - data.lows[i] >= body * .45
    )
    bearish = (
        data.highs[i] > prior_high + penetration
        and data.closes[i] < prior_high
        and data.closes[i] < data.opens[i]
        and data.highs[i] - data.closes[i] >= body * .45
    )
    return "BUY" if bullish else "SELL" if bearish else None
