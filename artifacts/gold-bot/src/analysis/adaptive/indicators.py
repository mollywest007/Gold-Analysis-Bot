"""Pure indicator helpers; missing or insufficient data remains unavailable."""

import math


def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = float(values[0])
    for value in values[1:]:
        result = float(value) * alpha + result * (1 - alpha)
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for before, after in zip(values[-period-1:-1], values[-period:]):
        change = float(after) - float(before)
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def macd_histogram(values):
    if len(values) < 35:
        return None
    def series(period):
        alpha = 2 / (period + 1)
        out = [float(values[0])]
        for value in values[1:]:
            out.append(float(value) * alpha + out[-1] * (1 - alpha))
        return out
    fast, slow = series(12), series(26)
    line = [a - b for a, b in zip(fast, slow)]
    alpha = 2 / 10
    signal = [line[0]]
    for value in line[1:]:
        signal.append(value * alpha + signal[-1] * (1 - alpha))
    return line[-1] - signal[-1]


def atr(highs, lows, closes, period=14):
    if min(len(highs), len(lows), len(closes)) <= period:
        return None
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(
            float(highs[i]) - float(lows[i]),
            abs(float(highs[i]) - float(closes[i-1])),
            abs(float(lows[i]) - float(closes[i-1])),
        ))
    return sum(tr[-period:]) / period if len(tr) >= period else None


def valid_series(data):
    if data is None:
        return False
    arrays = [getattr(data, name, None) for name in ("opens", "highs", "lows", "closes")]
    if any(not values or len(values) < 35 for values in arrays):
        return False
    if len({len(values) for values in arrays}) != 1:
        return False
    for o, h, l, c in zip(*arrays):
        if not all(math.isfinite(float(x)) and float(x) > 0 for x in (o, h, l, c)):
            return False
        if float(h) < max(float(o), float(c), float(l)) or float(l) > min(float(o), float(c)):
            return False
    return True
