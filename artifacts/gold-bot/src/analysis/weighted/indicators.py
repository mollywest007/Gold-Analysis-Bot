"""Small deterministic indicator set for weighted XAU/USD analysis."""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Sequence


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def ema_series(values: Sequence[float], period: int) -> list[float]:
    if period <= 0 or not values or any(not _finite(value) for value in values):
        return []
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(float(value) * alpha + result[-1] * (1.0 - alpha))
    return result


def rsi_series(values: Sequence[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) <= period or any(not _finite(value) for value in values):
        return result
    deltas = [float(values[index]) - float(values[index - 1]) for index in range(1, len(values))]
    average_gain = sum(max(delta, 0.0) for delta in deltas[:period]) / period
    average_loss = sum(max(-delta, 0.0) for delta in deltas[:period]) / period

    def value_for(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + gain / loss))

    result[period] = value_for(average_gain, average_loss)
    for close_index in range(period + 1, len(values)):
        delta = deltas[close_index - 1]
        average_gain = (average_gain * (period - 1) + max(delta, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-delta, 0.0)) / period
        result[close_index] = value_for(average_gain, average_loss)
    return result


def atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    size = min(len(highs), len(lows), len(closes))
    result: list[float | None] = [None] * size
    # ATR(period) requires a full window of `period` true ranges, which means
    # one prior close plus `period` completed candles.
    if period <= 0 or size <= period:
        return result
    true_ranges = [0.0] * size
    for index in range(1, size):
        high = float(highs[index])
        low = float(lows[index])
        previous_close = float(closes[index - 1])
        if not all(_finite(value) for value in (high, low, previous_close)):
            return [None] * size
        true_ranges[index] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
    seed_end = min(size, period + 1)
    seed = true_ranges[1:seed_end]
    if not seed:
        return result
    current = sum(seed) / len(seed)
    result[seed_end - 1] = current
    for index in range(seed_end, size):
        current = (current * (period - 1) + true_ranges[index]) / period
        result[index] = current
    return result


def _macd_histogram(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[float | None]:
    if len(closes) < slow + signal:
        return [None] * len(closes)
    fast_values = ema_series(closes, fast)
    slow_values = ema_series(closes, slow)
    macd = [left - right for left, right in zip(fast_values, slow_values)]
    signal_values = ema_series(macd, signal)
    return [line - sig for line, sig in zip(macd, signal_values)]


@dataclass(frozen=True)
class IndicatorSnapshot:
    ema20: float | None
    ema50: float | None
    rsi14: float | None
    rsi14_previous: float | None
    macd_hist: float | None
    macd_hist_previous: float | None
    atr14: float | None
    atr_reference: float | None
    atr_expansion: float | None


def calculate_indicators(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    atr_period: int = 14,
    atr_reference_lookback: int = 40,
) -> IndicatorSnapshot:
    size = min(len(highs), len(lows), len(closes))
    highs, lows, closes = highs[:size], lows[:size], closes[:size]
    ema20_values = ema_series(closes, 20)
    ema50_values = ema_series(closes, 50)
    rsi_values = rsi_series(closes, 14)
    macd_values = _macd_histogram(closes)
    atr_values = atr_series(highs, lows, closes, atr_period)

    valid_atr = [float(value) for value in atr_values if value is not None and value > 0]
    current_atr = atr_values[-1] if atr_values else None
    reference = (
        median(valid_atr[-atr_reference_lookback:])
        if valid_atr
        else None
    )
    expansion = (
        float(current_atr) / reference
        if current_atr is not None and reference is not None and reference > 0
        else None
    )
    return IndicatorSnapshot(
        ema20=ema20_values[-1] if len(ema20_values) == size else None,
        ema50=ema50_values[-1] if len(ema50_values) == size else None,
        rsi14=rsi_values[-1] if rsi_values else None,
        rsi14_previous=rsi_values[-2] if len(rsi_values) > 1 else None,
        macd_hist=macd_values[-1] if macd_values else None,
        macd_hist_previous=macd_values[-2] if len(macd_values) > 1 else None,
        atr14=current_atr,
        atr_reference=reference,
        atr_expansion=expansion,
    )

