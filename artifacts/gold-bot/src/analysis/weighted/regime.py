"""Mode-neutral regime classification from structure and realized volatility."""
from __future__ import annotations

from dataclasses import dataclass

from .indicators import IndicatorSnapshot
from .structure import StructureSnapshot


@dataclass(frozen=True)
class RegimeSnapshot:
    label: str
    volatility_ratio: float | None
    session: str


def classify_regime(
    structure: StructureSnapshot,
    indicators: IndicatorSnapshot,
    *,
    price: float,
    candle_timestamp: float | None,
    volatility_limit: float,
) -> RegimeSnapshot:
    ratio = indicators.atr_expansion
    if ratio is not None and ratio >= volatility_limit:
        label = "HIGH_VOLATILITY"
    elif (
        structure.direction == "BUY"
        and indicators.ema20 is not None
        and indicators.ema50 is not None
        and price >= indicators.ema20
    ):
        label = "BULLISH_TREND"
    elif (
        structure.direction == "SELL"
        and indicators.ema20 is not None
        and indicators.ema50 is not None
        and price <= indicators.ema20
    ):
        label = "BEARISH_TREND"
    elif structure.label == "RANGE":
        label = "RANGE_BOUND"
    else:
        label = "TRANSITIONING_OR_UNCLEAR"

    # Approximate UTC session labels only; they are context, not a trade trigger.
    session = "UNAVAILABLE"
    if candle_timestamp:
        hour = __import__("datetime").datetime.fromtimestamp(
            float(candle_timestamp), tz=__import__("datetime").timezone.utc
        ).hour
        if 12 <= hour < 16:
            session = "LONDON_NY_OVERLAP"
        elif 7 <= hour < 12:
            session = "LONDON"
        elif 16 <= hour < 21:
            session = "NEW_YORK"
        else:
            session = "ASIA_OR_OFF_HOURS"
    return RegimeSnapshot(label, ratio, session)

