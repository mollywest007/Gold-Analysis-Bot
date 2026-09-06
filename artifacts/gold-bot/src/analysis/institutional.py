"""Institutional market-context analysis for XAU/USD.

This module deliberately uses deterministic, explainable price/volume rules.
It does not pretend that OHLCV alone can reveal institutional orders, delta,
economic calendars, or dealer positioning.  Those fields are marked unavailable
until a real feed is supplied.

The public surface is:
  - ``build_context``: independent analysis for one timeframe
  - ``combine_contexts``: weighted multi-timeframe decision
  - ``fetch_intermarket_snapshot``: optional cached Yahoo cross-asset snapshot
"""

from __future__ import annotations

import asyncio
import math
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import aiohttp


TFS = ("W1", "D1", "H4", "H1", "M15", "M5")
TF_WEIGHT = {"W1": 3.0, "D1": 2.7, "H4": 2.2, "H1": 1.8, "M15": 1.2, "M5": 0.8}
YAHOO_SYMBOLS = {
    "DXY": "DX-Y.NYB",
    "US2Y": "^UST2Y",
    "US10Y": "^TNX",
    "SILVER": "SI=F",
    "OIL": "CL=F",
    "SPX": "^GSPC",
    "NASDAQ": "^IXIC",
    "VIX": "^VIX",
    "BTC": "BTC-USD",
}


def _safe(values: Sequence[float], n: int = 0) -> list[float]:
    values = list(values or [])
    return [float(x) for x in (values[-n:] if n else values) if x is not None and math.isfinite(float(x))]


def _mean(values: Sequence[float], default: float = 0.0) -> float:
    values = _safe(values)
    return statistics.fmean(values) if values else default


def _median(values: Sequence[float], default: float = 0.0) -> float:
    values = _safe(values)
    return statistics.median(values) if values else default


def _atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float:
    if not closes:
        return 0.0
    trs = []
    for i, (high, low) in enumerate(zip(highs, lows)):
        prev = closes[i - 1] if i else closes[i]
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    return _mean(trs[-period:], 0.0)


def _slope(values: Sequence[float], window: int = 20) -> float:
    values = _safe(values, window)
    if len(values) < 3:
        return 0.0
    x_mean = (len(values) - 1) / 2
    y_mean = _mean(values)
    denom = sum((i - x_mean) ** 2 for i in range(len(values)))
    return sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values)) / denom if denom else 0.0


def _pct_change(values: Sequence[float], lookback: int = 1) -> float:
    values = _safe(values)
    if len(values) <= lookback or not values[-lookback - 1]:
        return 0.0
    return (values[-1] / values[-lookback - 1] - 1.0) * 100.0


def _ema(values: Sequence[float], period: int) -> float:
    values = _safe(values)
    if not values:
        return 0.0
    result = values[0]
    alpha = 2.0 / (period + 1)
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(values: Sequence[float], period: int = 14) -> float:
    values = _safe(values)
    if len(values) < 2:
        return 50.0
    gains, losses = [], []
    for a, b in zip(values[-period - 1:-1], values[-period:]):
        delta = b - a
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    gain, loss = _mean(gains), _mean(losses)
    return 100.0 if loss == 0 and gain else 50.0 if loss == 0 else 100 - (100 / (1 + gain / loss))


def _swings(highs: Sequence[float], lows: Sequence[float], radius: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    sh, sl = [], []
    n = min(len(highs), len(lows))
    for i in range(radius, n - radius):
        if highs[i] >= max(highs[i - radius:i + radius + 1]) and highs[i] > max(highs[i - radius:i] or [0]):
            sh.append((i, highs[i]))
        if lows[i] <= min(lows[i - radius:i + radius + 1]) and lows[i] < min(lows[i - radius:i] or [float("inf")]):
            sl.append((i, lows[i]))
    return sh, sl


def _direction(score: float, threshold: float = 0.12) -> str:
    if score > threshold:
        return "BUY"
    if score < -threshold:
        return "SELL"
    return "WAIT"


def _pattern_direction(name: str) -> str:
    if any(word in name for word in ("Bullish", "Hammer", "Morning", "Piercing", "Three White", "Bottom", "Up")):
        return "BUY"
    if any(word in name for word in ("Bearish", "Hanging", "Gravestone", "Evening", "Dark Cloud", "Three Black", "Top", "Down")):
        return "SELL"
    return "WAIT"


def detect_candles(opens, highs, lows, closes) -> list[str]:
    """Return all named formations visible in the latest three candles."""
    if len(closes) < 3:
        return []
    out: list[str] = []
    i = len(closes) - 1
    rng = max(highs[i] - lows[i], 1e-9)
    body = abs(closes[i] - opens[i])
    upper = highs[i] - max(opens[i], closes[i])
    lower = min(opens[i], closes[i]) - lows[i]
    body_ratio, upper_ratio, lower_ratio = body / rng, upper / rng, lower / rng
    if body_ratio <= 0.03:
        out.append("Doji")
        if lower_ratio >= 0.35 and upper_ratio <= 0.15:
            out.append("Dragonfly Doji")
        elif upper_ratio >= 0.35 and lower_ratio <= 0.15:
            out.append("Gravestone Doji")
        elif lower_ratio >= 0.25 and upper_ratio >= 0.25:
            out.append("Long-legged Doji")
    elif body_ratio < 0.30 and upper_ratio >= 0.2 and lower_ratio >= 0.2:
        out.append("Spinning Top")
    if body_ratio >= 0.82 and upper_ratio <= 0.10 and lower_ratio <= 0.10:
        out.append("Bullish Marubozu" if closes[i] > opens[i] else "Bearish Marubozu")
    if lower_ratio >= 0.55 and upper_ratio <= 0.15:
        out.append("Hammer" if closes[i] >= opens[i] else "Hanging Man")
    if upper_ratio >= 0.55 and lower_ratio <= 0.15:
        out.append("Gravestone Doji" if body_ratio <= 0.08 else "Shooting Star")
    if len(closes) >= 2:
        p = i - 1
        if closes[p] < opens[p] and closes[i] > opens[i] and opens[i] <= closes[p] and closes[i] >= opens[p]:
            out.append("Bullish Engulfing")
        if closes[p] > opens[p] and closes[i] < opens[i] and opens[i] >= closes[p] and closes[i] <= opens[p]:
            out.append("Bearish Engulfing")
        if abs(highs[i] - highs[p]) <= rng * 0.18:
            out.append("Tweezer Tops")
        if abs(lows[i] - lows[p]) <= rng * 0.18:
            out.append("Tweezer Bottoms")
        if closes[p] < opens[p] and closes[i] > opens[i] and closes[i] > (opens[p] + closes[p]) / 2:
            out.append("Piercing Line")
        if closes[p] > opens[p] and closes[i] < opens[i] and closes[i] < (opens[p] + closes[p]) / 2:
            out.append("Dark Cloud Cover")
    if len(closes) >= 3:
        a, b, c = i - 2, i - 1, i
        if closes[a] < opens[a] and abs(closes[b] - opens[b]) < (highs[b] - lows[b]) * .35 and closes[c] > opens[c] and closes[c] > (opens[a] + closes[a]) / 2:
            out.append("Morning Star")
        if closes[a] > opens[a] and abs(closes[b] - opens[b]) < (highs[b] - lows[b]) * .35 and closes[c] < opens[c] and closes[c] < (opens[a] + closes[a]) / 2:
            out.append("Evening Star")
        if all(closes[j] > opens[j] and closes[j] > closes[j - 1] for j in (b, c)) and closes[a] > opens[a]:
            out.append("Three White Soldiers")
        if all(closes[j] < opens[j] and closes[j] < closes[j - 1] for j in (b, c)) and closes[a] < opens[a]:
            out.append("Three Black Crows")
        if closes[a] < opens[a] and closes[b] > opens[b] and closes[b] < opens[a] and closes[b] > closes[a] and closes[c] > highs[b]:
            out.append("Three Inside Up")
        if closes[a] > opens[a] and closes[b] < opens[b] and closes[b] > opens[a] and closes[b] < closes[a] and closes[c] < lows[b]:
            out.append("Three Inside Down")
        if closes[a] < opens[a] and closes[b] > opens[b] and opens[b] < closes[a] and closes[b] > opens[a] and closes[c] > highs[b]:
            out.append("Three Outside Up")
        if closes[a] > opens[a] and closes[b] < opens[b] and opens[b] > closes[a] and closes[b] < opens[a] and closes[c] < lows[b]:
            out.append("Three Outside Down")
    return list(dict.fromkeys(out))


def detect_chart_patterns(highs, lows, closes, atr) -> list[str]:
    """Conservative geometry tests for major continuation/reversal formations."""
    if len(closes) < 24:
        return []
    h, l, c = highs[-60:], lows[-60:], closes[-60:]
    atr = max(float(atr), _mean([x - y for x, y in zip(h, l)]) * .5, 1e-9)
    out: list[str] = []
    half = len(c) // 2
    q = len(c) // 4
    if abs(max(h[:half]) - max(h[half:])) <= atr * 1.25 and min(l) < max(h) - atr * 2:
        out.append("Double Top")
    if abs(min(l[:half]) - min(l[half:])) <= atr * 1.25 and max(h) > min(l) + atr * 2:
        out.append("Double Bottom")
    peak_groups = [max(h[:q]), max(h[q:q * 2]), max(h[q * 2:q * 3])] if q else []
    trough_groups = [min(l[:q]), min(l[q:q * 2]), min(l[q * 2:q * 3])] if q else []
    if len(peak_groups) == 3 and max(peak_groups) - min(peak_groups) <= atr * 1.5:
        out.append("Triple Top")
    if len(trough_groups) == 3 and max(trough_groups) - min(trough_groups) <= atr * 1.5:
        out.append("Triple Bottom")
    if len(c) >= 30:
        left, mid, right = max(h[:q * 2]), max(h[q: q * 3]), max(h[q * 2:])
        if mid > left + atr and mid > right + atr and abs(left - right) < atr * 2:
            out.append("Head and Shoulders")
        left, mid, right = min(l[:q * 2]), min(l[q: q * 3]), min(l[q * 2:])
        if mid < left - atr and mid < right - atr and abs(left - right) < atr * 2:
            out.append("Inverse Head and Shoulders")
    recent_h, recent_l = h[-12:], l[-12:]
    if max(recent_h) - min(recent_h) < atr * 2.2 and recent_l[-1] > recent_l[0] + atr * .35:
        out.append("Ascending Triangle")
    if max(recent_h) < recent_h[0] + atr * .15 and min(recent_l) - recent_l[0] < -atr * .35:
        out.append("Descending Triangle")
    if _slope(recent_h) > 0 and _slope(recent_l) > 0 and (recent_h[-1] - recent_l[-1]) < (recent_h[0] - recent_l[0]) * .75:
        out.append("Rising Wedge")
    if _slope(recent_h) < 0 and _slope(recent_l) < 0 and (recent_h[-1] - recent_l[-1]) < (recent_h[0] - recent_l[0]) * .75:
        out.append("Falling Wedge")
    if _slope(recent_h) < -atr * .02 and _slope(recent_l) > atr * .02:
        out.append("Symmetrical Triangle")
    if len(c) >= 35 and abs(c[-1] - c[-8]) < atr * 1.5 and max(recent_h) - min(recent_l) < atr * 3:
        out.append("Pennant")
    if len(c) >= 40 and c[len(c) // 2] < c[0] - atr and abs(c[-1] - c[0]) < atr * 1.5:
        out.append("Cup and Handle")
    ranges = [highs[i] - lows[i] for i in range(max(1, len(highs) - 14), len(highs))]
    if len(ranges) >= 10 and _slope(ranges) > atr * .03:
        out.append("Megaphone Pattern")
    if len(ranges) >= 14 and max(ranges[:7]) < min(ranges[7:]) * .8 and max(ranges[7:]) > min(ranges[7:]) * 1.1:
        out.append("Diamond")
    if max(recent_h) - min(recent_l) < atr * 5:
        out.append("Rectangle")
    pole = c[max(0, len(c) - 20)] - c[max(0, len(c) - 35)] if len(c) >= 35 else 0
    if pole > atr * 3 and _slope(c[-12:]) < 0 and max(recent_h) - min(recent_l) < abs(pole) * .55:
        out.append("Bull Flag")
    if pole < -atr * 3 and _slope(c[-12:]) > 0 and max(recent_h) - min(recent_l) < abs(pole) * .55:
        out.append("Bear Flag")
    if len(c) >= 50 and abs(_slope(c[-20:])) < atr * .05:
        out.append("Rounded Bottom" if c[-1] > c[-20] else "Rounded Top")
    return list(dict.fromkeys(out))


def _structure(highs, lows, closes, atr) -> dict[str, Any]:
    sh, sl = _swings(highs, lows, 3)
    high_labels, low_labels = [], []
    for seq, prefix in ((sh, "H"), (sl, "L")):
        if len(seq) >= 2:
            for (_, a), (_, b) in zip(seq[-4:-1], seq[-3:]):
                if prefix == "H":
                    high_labels.append("HH" if b > a else "LH")
                else:
                    low_labels.append("HL" if b > a else "LL")
    trend = "RANGING"
    if high_labels and low_labels:
        if high_labels[-1] == "HH" and low_labels[-1] == "HL":
            trend = "BULLISH"
        elif high_labels[-1] == "LH" and low_labels[-1] == "LL":
            trend = "BEARISH"
        else:
            trend = "TRANSITION"
    last_high = sh[-1][1] if sh else 0
    last_low = sl[-1][1] if sl else 0
    bos = "NONE"
    if closes and last_high and closes[-1] > last_high + atr * .05:
        bos = "BULLISH_BOS"
    elif closes and last_low and closes[-1] < last_low - atr * .05:
        bos = "BEARISH_BOS"
    prev = closes[-4:-1] if len(closes) >= 4 else closes
    expansion = _mean([highs[i] - lows[i] for i in range(max(0, len(highs) - 5), len(highs))]) > _mean([highs[i] - lows[i] for i in range(max(0, len(highs) - 20), max(0, len(highs) - 5))]) * 1.25 if len(highs) >= 20 else False
    compression = not expansion and len(highs) >= 20 and _mean([highs[i] - lows[i] for i in range(-5, 0)]) < _mean([highs[i] - lows[i] for i in range(-20, -5)]) * .72
    return {
        "trend": trend, "higher_high": high_labels[-1:] == ["HH"], "higher_low": low_labels[-1:] == ["HL"],
        "lower_high": high_labels[-1:] == ["LH"], "lower_low": low_labels[-1:] == ["LL"],
        "swing_highs": [round(x[1], 2) for x in sh[-5:]], "swing_lows": [round(x[1], 2) for x in sl[-5:]],
        "fractal_swings": {"highs": len(sh), "lows": len(sl)}, "bos": bos,
        "choch": ("BULLISH_CHOCH" if trend == "BEARISH" and bos == "BULLISH_BOS" else
                  "BEARISH_CHOCH" if trend == "BULLISH" and bos == "BEARISH_BOS" else "NONE"),
        "mss": bos.replace("BOS", "MSS") if bos != "NONE" else "NONE",
        "internal": "BULLISH" if _slope(closes, 8) > 0 else "BEARISH" if _slope(closes, 8) < 0 else "RANGING",
        "external": trend, "continuation": trend in ("BULLISH", "BEARISH") and bos.endswith(trend),
        "exhaustion": (abs(_slope(closes, 8)) < atr * .05 and _rsi(closes) > 68) or (abs(_slope(closes, 8)) < atr * .05 and _rsi(closes) < 32),
        "compression": compression, "expansion": expansion,
        "channel": {"upper": round(max(highs[-20:]), 2), "lower": round(min(lows[-20:]), 2)} if len(highs) >= 20 else {},
        "trendline_break": bos != "NONE", "dynamic_sr": {"ema20": round(_ema(closes, 20), 2), "ema50": round(_ema(closes, 50), 2)},
        "labels": high_labels[-3:] + low_labels[-3:],
    }


def _levels(highs, lows, closes, price) -> dict[str, Any]:
    n = len(closes)
    def bucket(size, offset=0):
        if n < size * (offset + 1):
            return {}
        start, end = n - size * (offset + 1), n - size * offset
        return {"high": round(max(highs[start:end]), 2), "low": round(min(lows[start:end]), 2)}
    atr = _atr(highs, lows, closes)
    pivot = (max(highs[-20:]) + min(lows[-20:]) + closes[-1]) / 3 if n >= 20 else price
    round_levels = sorted({round(price / step) * step for step in (5, 10, 25, 50, 100)})
    return {
        "daily": bucket(24), "weekly": bucket(24 * 5), "monthly": bucket(24 * 21),
        "previous_session": bucket(8, 1), "pivot_points": {"p": round(pivot, 2), "r1": round(2 * pivot - min(lows[-20:]), 2), "s1": round(2 * pivot - max(highs[-20:]), 2)},
        "round_numbers": [x for x in round_levels if abs(x - price) <= max(atr * 2, 50)],
        "psychological_levels": [round(price / 100) * 100, round(price / 50) * 50],
        "institutional_levels": [round(price / 25) * 25, round(price / 100) * 100],
        "midpoint": round((max(highs[-20:]) + min(lows[-20:])) / 2, 2) if n >= 20 else price,
        "range_boundaries": {"high": round(max(highs[-20:]), 2), "low": round(min(lows[-20:]), 2)} if n >= 20 else {},
    }


def _smc(opens, highs, lows, closes, volumes, atr, structure) -> dict[str, Any]:
    recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    last = closes[-1]
    equal_highs = len(highs) >= 4 and abs(max(highs[-4:]) - sorted(highs[-4:])[-2]) <= atr * .25
    equal_lows = len(lows) >= 4 and abs(min(lows[-4:]) - sorted(lows[-4:])[1]) <= atr * .25
    sweep = "BULLISH" if lows[-1] < recent_low and closes[-1] > recent_low else "BEARISH" if highs[-1] > recent_high and closes[-1] < recent_high else "NONE"
    fvg = "NONE"
    fvg_zone = {}
    for i in range(max(2, len(closes) - 5), len(closes)):
        if lows[i] > highs[i - 2] + atr * .05:
            fvg, fvg_zone = "BULLISH", {"bottom": round(highs[i - 2], 2), "top": round(lows[i], 2)}
        elif highs[i] < lows[i - 2] - atr * .05:
            fvg, fvg_zone = "BEARISH", {"bottom": round(highs[i], 2), "top": round(lows[i - 2], 2)}
    displacement = bool(volumes and volumes[-1] > _mean(volumes[-21:-1]) * 1.8 and abs(closes[-1] - opens[-1]) > atr * .8)
    return {
        "buy_side_liquidity": round(recent_high, 2), "sell_side_liquidity": round(recent_low, 2),
        "equal_highs": equal_highs, "equal_lows": equal_lows, "liquidity_sweep": sweep,
        "liquidity_grab": sweep != "NONE", "stop_hunt": sweep != "NONE", "order_block": "BULLISH" if structure["trend"] == "BULLISH" else "BEARISH" if structure["trend"] == "BEARISH" else "NONE",
        "mitigation_block": "PENDING" if fvg != "NONE" else "NONE", "breaker_block": "PENDING" if structure["choch"] != "NONE" else "NONE",
        "fair_value_gap": fvg, "fvg_zone": fvg_zone, "inverse_fvg": "PENDING" if fvg != "NONE" else "NONE",
        "balanced_price_range": round((recent_high + recent_low) / 2, 2), "premium": last > (recent_high + recent_low) / 2,
        "discount": last < (recent_high + recent_low) / 2, "equilibrium": round((recent_high + recent_low) / 2, 2),
        "ote": {"low": round(recent_low + (recent_high - recent_low) * .382, 2), "high": round(recent_low + (recent_high - recent_low) * .618, 2)},
        "inducement": "PENDING" if equal_highs or equal_lows else "NONE", "displacement": displacement,
        "liquidity_void": displacement and abs(closes[-1] - opens[-1]) > atr * 1.2, "institutional_candle": displacement,
    }


def _volume(opens, highs, lows, closes, volumes, atr) -> dict[str, Any]:
    avg = _mean(volumes[-21:-1]) if len(volumes) > 2 else 0
    recent = volumes[-1] if volumes else 0
    delta = sum((1 if c >= o else -1) * v for o, c, v in zip(opens[-20:], closes[-20:], volumes[-20:]))
    typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    bins: dict[float, float] = {}
    for p, v in zip(typical[-60:], volumes[-60:]):
        key = round(p / max(atr, 1) / 2) * max(atr, 1) * 2
        bins[key] = bins.get(key, 0) + v
    poc = max(bins, key=bins.get) if bins else 0
    nodes = sorted(bins, key=bins.get, reverse=True)
    return {
        "spike": bool(avg and recent > avg * 1.8), "climax": bool(avg and recent > avg * 2.5),
        "low_volume_pullback": bool(avg and recent < avg * .65), "high_volume_breakout": bool(avg and recent > avg * 1.5),
        "volume_divergence": "BEARISH" if _pct_change(closes, 5) > 0 and _pct_change(volumes, 5) < 0 else "BULLISH" if _pct_change(closes, 5) < 0 and _pct_change(volumes, 5) > 0 else "NONE",
        "profile": {"poc": round(poc, 2), "high_volume_nodes": [round(x, 2) for x in nodes[:3]], "low_volume_nodes": [round(x, 2) for x in nodes[-3:]]} if bins else {},
        "delta": round(delta, 2), "delta_available": True,
    }


def _momentum(closes, highs, lows) -> dict[str, Any]:
    rsi = _rsi(closes)
    macd_fast, macd_slow = _ema(closes, 12), _ema(closes, 26)
    hist = macd_fast - macd_slow
    k = ((closes[-1] - min(lows[-14:])) / max(max(highs[-14:]) - min(lows[-14:]), 1e-9)) * 100 if len(closes) >= 14 else 50
    return {
        "rsi": round(rsi, 2), "rsi_divergence": "BEARISH" if closes[-1] > max(closes[-10:-1]) and rsi < _rsi(closes[:-1]) else "BULLISH" if closes[-1] < min(closes[-10:-1]) and rsi > _rsi(closes[:-1]) else "NONE",
        "hidden_rsi_divergence": "BULLISH" if _slope(closes[-12:]) > 0 and _slope([_rsi(closes[:i]) for i in range(max(15, len(closes)-12), len(closes)+1)]) < 0 else "BEARISH" if _slope(closes[-12:]) < 0 else "NONE",
        "macd_hist": round(hist, 4), "macd_divergence": "BEARISH" if hist < 0 and _slope(closes[-8:]) > 0 else "BULLISH" if hist > 0 and _slope(closes[-8:]) < 0 else "NONE",
        "stochastic": round(k, 2), "stochastic_divergence": "NONE", "momentum_shift": "BULLISH" if hist > 0 else "BEARISH" if hist < 0 else "NONE",
        "momentum_exhaustion": rsi > 75 or rsi < 25, "rsi_failure_swing": (rsi > 70 and _rsi(closes[:-2]) < 70) or (rsi < 30 and _rsi(closes[:-2]) > 30),
    }


def _volatility(highs, lows, closes) -> dict[str, Any]:
    atr = _atr(highs, lows, closes)
    atr_pct = atr / closes[-1] * 100 if closes and closes[-1] else 0
    ranges = [h - l for h, l in zip(highs, lows)]
    avg = _mean(ranges[-21:-1])
    std = statistics.pstdev(closes[-20:]) if len(closes) >= 20 else 0
    mid = _mean(closes[-20:])
    upper, lower = mid + 2 * std, mid - 2 * std
    bandwidth = (upper - lower) / mid * 100 if mid else 0
    prior_bw = _mean([(max(closes[i - 20:i]) - min(closes[i - 20:i])) / max(_mean(closes[i - 20:i]), 1) * 100 for i in range(max(20, len(closes)-8), len(closes))])
    return {"atr": round(atr, 2), "atr_percent": round(atr_pct, 3), "atr_expansion": bool(avg and ranges[-1] > avg * 1.35), "atr_compression": bool(avg and ranges[-1] < avg * .65), "bb_bandwidth": round(bandwidth, 3), "bb_squeeze": bool(prior_bw and bandwidth < prior_bw * .75), "bb_expansion": bool(prior_bw and bandwidth > prior_bw * 1.25), "volatility_breakout": bool(ranges[-1] > avg * 1.5 if avg else False), "regime": "HIGH" if atr_pct > .9 else "LOW" if atr_pct < .25 else "NORMAL"}


def _fibonacci(highs, lows) -> dict[str, Any]:
    high, low = max(highs[-50:]), min(lows[-50:])
    span = high - low
    levels = {str(x): round(high - span * x, 2) for x in (.236, .382, .5, .618, .786)}
    extensions = {str(x): round(low + span * x, 2) for x in (1.272, 1.618, 2.0)}
    return {"retracements": levels, "extensions": extensions, "golden_pocket": [levels["0.618"], levels["0.786"]], "clusters": [levels["0.382"], levels["0.5"], levels["0.618"]], "confluence_zones": [levels["0.5"]]}


def _wyckoff(structure, volume, closes) -> dict[str, Any]:
    trend = structure["trend"]
    spring = structure["choch"] == "BULLISH_CHOCH" and volume["spike"]
    upthrust = structure["choch"] == "BEARISH_CHOCH" and volume["spike"]
    return {"phase": "ACCUMULATION" if trend == "BULLISH" else "DISTRIBUTION" if trend == "BEARISH" else "RANGE",
            "accumulation": trend == "BULLISH", "distribution": trend == "BEARISH", "spring": spring, "upthrust": upthrust,
            "buying_climax": upthrust and volume["climax"], "selling_climax": spring and volume["climax"],
            "automatic_rally": spring, "secondary_test": "PENDING", "sign_of_strength": trend == "BULLISH" and volume["high_volume_breakout"],
            "sign_of_weakness": trend == "BEARISH" and volume["high_volume_breakout"], "last_point_of_support": trend == "BULLISH", "last_point_of_supply": trend == "BEARISH"}


def _elliott(structure, closes) -> dict[str, Any]:
    labels = structure.get("labels", [])
    impulse = len(labels) >= 3 and len(set(labels[-3:])) > 1
    correction = structure["trend"] == "TRANSITION"
    return {"impulse": impulse, "corrective": correction, "abc_correction": correction, "zigzag": correction and abs(_slope(closes[-10:])) > 0,
            "flat": correction and abs(_slope(closes[-10:])) < .1, "triangle": structure["compression"], "wave_extension": abs(_slope(closes[-8:])) > abs(_slope(closes[-20:])) * 1.5 if len(closes) >= 20 else False, "truncated_wave": False, "confidence": "LOW"}


def _session() -> dict[str, Any]:
    hour = datetime.now(timezone.utc).hour
    name = "Asian" if hour < 7 else "London" if hour < 13 else "New York" if hour < 22 else "Asian"
    kill = "London Kill Zone" if 7 <= hour < 10 else "New York Kill Zone" if 12 <= hour < 15 else "Off-hours"
    return {"name": name, "kill_zone": kill, "in_kill_zone": kill != "Off-hours", "overlap": 13 <= hour < 16, "london_open_expansion": 7 <= hour < 10, "london_fakeout": False, "london_sweep": False, "new_york_reversal": 13 <= hour < 15, "new_york_continuation": 15 <= hour < 18, "london_close_reversal": 15 <= hour < 17, "asian_range": "AVAILABLE", "ict_kill_zones": ("07:00-10:00 UTC", "12:00-15:00 UTC")}


def _macro_context() -> dict[str, Any]:
    """Read an operator-supplied UTC event schedule; never invent calendar data."""
    raw = os.getenv("HIGH_IMPACT_EVENTS_UTC", "").strip()
    now = time.time()
    upcoming = []
    for item in raw.split(","):
        try:
            stamp, name = item.split("|", 1)
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            if 0 <= when - now <= 3600:
                upcoming.append({"name": name.strip(), "minutes": round((when - now) / 60)})
        except (ValueError, TypeError):
            continue
    return {"status": "HIGH_IMPACT_IMMINENT" if upcoming else "NO_SCHEDULED_EVENT" if raw else "UNAVAILABLE",
            "events": upcoming, "calendar_source": "HIGH_IMPACT_EVENTS_UTC" if raw else "UNAVAILABLE",
            "covered_events": ("FOMC", "Federal Reserve Speeches", "CPI", "Core CPI", "PPI", "NFP", "Unemployment", "GDP", "Retail Sales", "ISM Manufacturing", "ISM Services", "PCE", "Treasury Auctions")}


@dataclass
class InstitutionalContext:
    timeframe: str
    direction: str = "WAIT"
    bullish_probability: int = 50
    bearish_probability: int = 50
    confidence_score: int = 0
    risk_level: str = "HIGH"
    reasons_supporting: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    invalidating_conditions: list[str] = field(default_factory=list)
    best_entry_zone: dict[str, float] = field(default_factory=dict)
    suggested_stop_loss: float = 0.0
    suggested_take_profit: float = 0.0
    recommended_rr: float = 0.0
    market_structure: dict[str, Any] = field(default_factory=dict)
    support_resistance: dict[str, Any] = field(default_factory=dict)
    candlesticks: list[str] = field(default_factory=list)
    chart_patterns: list[str] = field(default_factory=list)
    smc: dict[str, Any] = field(default_factory=dict)
    wyckoff: dict[str, Any] = field(default_factory=dict)
    elliott_wave: dict[str, Any] = field(default_factory=dict)
    fibonacci: dict[str, Any] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    volatility: dict[str, Any] = field(default_factory=dict)
    momentum: dict[str, Any] = field(default_factory=dict)
    moving_averages: dict[str, Any] = field(default_factory=dict)
    breakout: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] = field(default_factory=dict)
    time_statistics: dict[str, Any] = field(default_factory=dict)
    macro: dict[str, Any] = field(default_factory=dict)
    intermarket: dict[str, Any] = field(default_factory=dict)
    statistics: dict[str, Any] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    data_quality: str = "REAL_OHLCV"


def build_context(data, timeframe: str, intermarket: Mapping[str, Any] | None = None) -> InstitutionalContext:
    opens, highs, lows, closes, volumes = map(list, (data.opens, data.highs, data.lows, data.closes, data.volumes))
    price, atr = float(data.price), _atr(highs, lows, closes)
    structure = _structure(highs, lows, closes, atr)
    levels = _levels(highs, lows, closes, price)
    smc = _smc(opens, highs, lows, closes, volumes, atr, structure)
    volume = _volume(opens, highs, lows, closes, volumes, atr)
    volatility = _volatility(highs, lows, closes)
    momentum = _momentum(closes, highs, lows)
    candles, charts = detect_candles(opens, highs, lows, closes), detect_chart_patterns(highs, lows, closes, atr)
    trend_score = {"BULLISH": 1.0, "BEARISH": -1.0}.get(structure["trend"], 0.0)
    score = {"trend": trend_score, "structure": trend_score + (0.35 if structure["bos"] == "BULLISH_BOS" else -0.35 if structure["bos"] == "BEARISH_BOS" else 0),
             "liquidity": .35 if smc["liquidity_sweep"] == "BULLISH" else -.35 if smc["liquidity_sweep"] == "BEARISH" else 0,
             "order_block": .25 if smc["order_block"] == "BULLISH" else -.25 if smc["order_block"] == "BEARISH" else 0,
             "fvg": .2 if smc["fair_value_gap"] == "BULLISH" else -.2 if smc["fair_value_gap"] == "BEARISH" else 0,
             "candles": sum(.12 if _pattern_direction(x) == "BUY" else -.12 if _pattern_direction(x) == "SELL" else 0 for x in candles),
             "momentum": .35 if momentum["momentum_shift"] == "BULLISH" else -.35 if momentum["momentum_shift"] == "BEARISH" else 0,
             "volatility": -.15 if volatility["regime"] == "LOW" else 0}
    if charts:
        score["chart_pattern"] = .25 if any(_pattern_direction(x) == "BUY" for x in charts) else -.25
    if intermarket:
        score["intermarket"] = float(intermarket.get("gold_confirmation", 0.0))
    total = sum(score.values())
    direction = _direction(total, .25)
    macro = _macro_context()
    if macro["status"] == "HIGH_IMPACT_IMMINENT":
        score["macro"] = -.4
        direction = "WAIT"
    bull = round(max(1, min(99, 50 + total * 25)))
    bear = 100 - bull
    if direction == "BUY":
        bull, bear = max(bull, 55), min(bear, 45)
    elif direction == "SELL":
        bear, bull = max(bear, 55), min(bull, 45)
    confidence = round(abs(bull - bear) * .9)
    reasons = []
    against = []
    if structure["trend"] in ("BULLISH", "BEARISH"):
        reasons.append(f"External structure is {structure['trend'].lower()} with {structure['labels'][-2:]}")
    if structure["bos"] != "NONE":
        reasons.append(structure["bos"].replace("_", " "))
    if smc["liquidity_sweep"] != "NONE":
        reasons.append(f"{smc['liquidity_sweep'].lower()} liquidity sweep")
    if candles:
        reasons.append("Candles: " + ", ".join(candles[:3]))
    if volatility["regime"] == "LOW":
        against.append("Low-volatility regime; breakout follow-through is unconfirmed")
    if structure["trend"] in ("RANGING", "TRANSITION"):
        against.append("Choppy/transitioning structure")
    if macro["status"] == "HIGH_IMPACT_IMMINENT":
        against.append("High-impact macro event within 60 minutes")
    if not intermarket:
        against.append("Intermarket confirmation unavailable")
    support = levels.get("range_boundaries", {}).get("low", price - atr)
    resistance = levels.get("range_boundaries", {}).get("high", price + atr)
    zone = {"low": round(max(support, price - atr * .8), 2), "high": round(min(resistance, price + atr * .8), 2)}
    rr = round(max(0.0, (resistance - price) / max(price - support, atr * .5)), 2) if direction == "BUY" else round(max(0.0, (price - support) / max(resistance - price, atr * .5)), 2)
    if rr < 1.5:
        against.append(f"Projected R:R {rr:.2f} is below institutional minimum")
        direction = "WAIT"
    risk = "LOW" if confidence >= 55 and not against else "MEDIUM" if confidence >= 35 else "HIGH"
    return InstitutionalContext(
        timeframe=timeframe, direction=direction, bullish_probability=bull, bearish_probability=bear,
        confidence_score=max(0, min(100, confidence)), risk_level=risk,
        reasons_supporting=reasons[:8], reasons_against=against[:8],
        invalidating_conditions=[f"Close below {support:.2f}" if direction == "BUY" else f"Close above {resistance:.2f}", "High-impact event becomes imminent", "Higher-timeframe bias changes"],
        best_entry_zone=zone, suggested_stop_loss=round(support - atr * .2 if direction == "BUY" else resistance + atr * .2, 2),
        suggested_take_profit=round(resistance, 2) if direction == "BUY" else round(support, 2), recommended_rr=rr,
        market_structure=structure, support_resistance=levels, candlesticks=candles, chart_patterns=charts,
        smc=smc, wyckoff=_wyckoff(structure, volume, closes), elliott_wave=_elliott(structure, closes),
        fibonacci=_fibonacci(highs, lows), volume=volume, volatility=volatility, momentum=momentum,
        moving_averages={"ema20": round(_ema(closes, 20), 2), "ema50": round(_ema(closes, 50), 2), "ema200": round(_ema(closes, 200), 2), "price_vs_ema20": "ABOVE" if price > _ema(closes, 20) else "BELOW", "price_vs_ema50": "ABOVE" if price > _ema(closes, 50) else "BELOW", "vwap": round(sum(((h + l + c) / 3) * v for h, l, c, v in zip(highs, lows, closes, volumes)) / max(sum(volumes), 1), 2)},
        breakout={"range_breakout": structure["bos"] != "NONE", "true_breakout": structure["bos"] != "NONE" and volume["high_volume_breakout"], "false_breakout": structure["bos"] != "NONE" and not volume["high_volume_breakout"], "break_and_retest": "PENDING", "failed_breakout": False, "opening_range_breakout": "PENDING"},
        session=_session(), time_statistics={"day_of_week": datetime.now(timezone.utc).strftime("%A"), "hour_utc": datetime.now(timezone.utc).hour, "month_end_rebalancing": datetime.now(timezone.utc).day >= 26, "quarter_end_rebalancing": datetime.now(timezone.utc).month in (3, 6, 9, 12) and datetime.now(timezone.utc).day >= 26, "seasonal_gold_trend": "UNAVAILABLE", "hourly_win_probability": "UNAVAILABLE"},
        macro=macro, intermarket=dict(intermarket or {"status": "UNAVAILABLE"}), statistics={"adr": round(_mean([max(highs[i - 23:i + 1]) - min(lows[i - 23:i + 1]) for i in range(23, len(closes))][-20:]), 2) if len(closes) >= 24 else 0, "atr": round(atr, 2), "z_score": round((price - _mean(closes[-20:])) / max(statistics.pstdev(closes[-20:]), 1e-9), 2) if len(closes) >= 20 else 0, "trend_persistence": round(sum(1 for a, b in zip(closes[-10:-1], closes[-9:]) if (b - a) * (closes[-1] - closes[-2]) > 0) / 9, 2) if len(closes) >= 10 else 0, "mean_reversion_probability": round(1 - min(1, abs((price - _mean(closes[-20:])) / max(statistics.pstdev(closes[-20:]), 1e-9)) / 3), 2) if len(closes) >= 20 else 0},
        score_breakdown={k: round(v, 3) for k, v in score.items()}, data_quality="SIMULATED_OHLCV" if getattr(data, "is_simulated", False) else "REAL_OHLCV",
    )


def as_dict(context: InstitutionalContext) -> dict[str, Any]:
    return asdict(context)


def combine_contexts(contexts: Mapping[str, InstitutionalContext]) -> dict[str, Any]:
    usable = [(tf, c) for tf, c in contexts.items() if c and c.data_quality == "REAL_OHLCV"]
    if not usable:
        return {"direction": "WAIT", "confidence_score": 0, "bullish_probability": 50, "bearish_probability": 50, "reason": "No real multi-timeframe data available"}
    score = 0.0
    total_weight = 0.0
    rows = {}
    for tf, c in usable:
        weight = TF_WEIGHT.get(tf, 1.0)
        score += (c.bullish_probability - c.bearish_probability) / 100 * weight
        total_weight += weight
        rows[tf] = {"direction": c.direction, "bullish_probability": c.bullish_probability, "bearish_probability": c.bearish_probability, "confidence_score": c.confidence_score}
    normalized = score / max(total_weight, 1)
    direction = "BUY" if normalized > .12 else "SELL" if normalized < -.12 else "WAIT"
    bull = round(max(1, min(99, 50 + normalized * 45)))
    return {"direction": direction, "bullish_probability": bull, "bearish_probability": 100 - bull, "confidence_score": round(abs(bull - (100 - bull)) * .9), "timeframes": rows, "reason": "Weighted W1→M5 consensus; lower timeframes cannot overrule higher-timeframe structure without CHoCH/MSS"}


_intermarket_cache: tuple[dict[str, Any], float] = ({}, 0.0)
_intermarket_lock = asyncio.Lock()


async def fetch_intermarket_snapshot() -> dict[str, Any]:
    global _intermarket_cache
    async with _intermarket_lock:
        if _intermarket_cache[0] and time.time() - _intermarket_cache[1] < 300:
            return _intermarket_cache[0]
    result: dict[str, Any] = {"status": "UNAVAILABLE", "assets": {}, "gold_confirmation": 0.0}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            async def one(name, symbol):
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
                try:
                    async with session.get(url, headers={"User-Agent": "GoldAnalysisBot/1.0"}) as response:
                        payload = await response.json()
                        closes = payload["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                        values = [float(x) for x in closes if x is not None]
                        return name, _pct_change(values, min(3, len(values) - 1))
                except Exception:
                    return name, None
            rows = await asyncio.gather(*(one(name, symbol) for name, symbol in YAHOO_SYMBOLS.items()))
        assets = {name: value for name, value in rows if value is not None}
        # Gold typically benefits from lower DXY/yields and a weaker real-rate proxy.
        score = 0.0
        for name in ("DXY", "US2Y", "US10Y"):
            if name in assets:
                score -= math.copysign(.18, assets[name]) if assets[name] else 0
        if "VIX" in assets and assets["VIX"] > 0:
            score += .08
        assets["REAL_YIELD"] = "UNAVAILABLE"
        result = {"status": "AVAILABLE" if assets else "UNAVAILABLE", "assets": assets, "gold_confirmation": max(-.5, min(.5, score))}
    except Exception:
        pass
    async with _intermarket_lock:
        _intermarket_cache = (result, time.time())
    return result