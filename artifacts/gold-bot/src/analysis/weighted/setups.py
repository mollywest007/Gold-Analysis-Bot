"""Developing price-action setups; each pattern has its own evidence rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .indicators import IndicatorSnapshot
from .structure import StructureSnapshot, liquidity_sweeps


@dataclass(frozen=True)
class SetupCandidate:
    direction: str
    setup_type: str
    level: float
    invalidation_level: float
    trigger_strength: float
    location_strength: float
    price_action_strength: float
    extension_atr: float
    evidence: tuple[str, ...]


def _nearest_zone(zones, price: float, direction: str, tolerance: float):
    if direction == "BUY":
        candidates = [zone for zone in zones if zone.center <= price + tolerance]
        return max(candidates, key=lambda zone: zone.center) if candidates else None
    candidates = [zone for zone in zones if zone.center >= price - tolerance]
    return min(candidates, key=lambda zone: zone.center) if candidates else None


def _candle_signal(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    atr: float,
    profile,
) -> tuple[bool, bool, float, float]:
    if not closes:
        return False, False, 0.0, 0.0
    opening, high, low, close = map(float, (opens[-1], highs[-1], lows[-1], closes[-1]))
    candle_range = max(high - low, 0.01)
    body = abs(close - opening)
    upper_wick = high - max(opening, close)
    lower_wick = min(opening, close) - low
    close_position = (close - low) / candle_range
    rejection_close = float(profile.rejection_close_position)
    bullish_rejection = (
        lower_wick >= max(
            body * float(profile.rejection_wick_body_ratio),
            atr * float(profile.rejection_wick_min_atr),
        )
        and close_position >= rejection_close
        and close >= opening
    )
    bearish_rejection = (
        upper_wick >= max(
            body * float(profile.rejection_wick_body_ratio),
            atr * float(profile.rejection_wick_min_atr),
        )
        and close_position <= 1.0 - rejection_close
        and close <= opening
    )
    return bullish_rejection, bearish_rejection, close_position, body / candle_range


def _momentum_direction(indicators: IndicatorSnapshot, direction: str) -> bool:
    if direction == "BUY":
        return bool(
            (indicators.rsi14 is not None and indicators.rsi14_previous is not None
             and indicators.rsi14 > indicators.rsi14_previous and indicators.rsi14 >= 40)
            or (indicators.macd_hist is not None and indicators.macd_hist_previous is not None
                and indicators.macd_hist > indicators.macd_hist_previous)
        )
    return bool(
        (indicators.rsi14 is not None and indicators.rsi14_previous is not None
         and indicators.rsi14 < indicators.rsi14_previous and indicators.rsi14 <= 60)
        or (indicators.macd_hist is not None and indicators.macd_hist_previous is not None
            and indicators.macd_hist < indicators.macd_hist_previous)
    )


def detect_setups(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    price: float,
    atr: float,
    indicators: IndicatorSnapshot,
    structure: StructureSnapshot,
    context_direction: str,
    refinement_direction: str,
    regime: str,
    profile,
    breakout_lookback: int,
    sweep_lookback: int,
) -> list[SetupCandidate]:
    """Return credible candidates without requiring a completed confirmation chain."""
    size = min(len(opens), len(highs), len(lows), len(closes))
    if size < 20 or atr <= 0:
        return []
    opens, highs, lows, closes = (
        opens[:size], highs[:size], lows[:size], closes[:size]
    )
    last = size - 1
    last_close = float(closes[last])
    opening = float(opens[last])
    high = float(highs[last])
    low = float(lows[last])
    tolerance = max(atr * float(profile.level_tolerance_atr), 0.01)
    extension_limit = float(profile.maximum_ema_distance_atr)
    ema_distance = (
        abs(price - indicators.ema20) / atr
        if indicators.ema20 is not None
        else None
    )
    bullish_rejection, bearish_rejection, close_position, body_ratio = _candle_signal(
        opens, highs, lows, closes, atr=atr, profile=profile
    )
    candidates: list[SetupCandidate] = []

    def add(
        direction: str,
        setup_type: str,
        level: float,
        invalidation: float | None,
        trigger_strength: float,
        location_strength: float,
        price_action_strength: float,
        evidence: list[str],
        *,
        extension: float | None = None,
    ) -> None:
        if invalidation is None:
            return
        if extension is None:
            extension = ema_distance if ema_distance is not None else 0.0
        if extension > extension_limit:
            return
        candidates.append(
            SetupCandidate(
                direction=direction,
                setup_type=setup_type,
                level=float(level),
                invalidation_level=float(invalidation),
                trigger_strength=max(0.0, min(1.0, trigger_strength)),
                location_strength=max(0.0, min(1.0, location_strength)),
                price_action_strength=max(0.0, min(1.0, price_action_strength)),
                extension_atr=float(extension),
                evidence=tuple(evidence),
            )
        )

    # B. Liquidity sweep: the prior range excludes the latest completed candle;
    # a wick alone is insufficient without a close back inside the range.
    for sweep in liquidity_sweeps(
        highs,
        lows,
        opens,
        closes,
        atr=atr,
        lookback=sweep_lookback,
        minimum_penetration_atr=float(profile.sweep_penetration_atr),
    ):
        direction = sweep["direction"]
        invalidation = low if direction == "BUY" else high
        rejection = bullish_rejection if direction == "BUY" else bearish_rejection
        if rejection or _momentum_direction(indicators, direction):
            add(
                direction,
                "LIQUIDITY_SWEEP",
                sweep["level"],
                invalidation,
                0.95,
                0.90,
                0.90 if rejection else 0.68,
                [sweep["evidence"], "Re-entry was paired with rejection or momentum recovery."],
                extension=abs(price - float(sweep["level"])) / atr,
            )

    # C/D. Structural breakout and breakout-retest. Only completed bars are used.
    range_lookback = min(max(int(breakout_lookback), 5), size - 1)
    prior_high = max(float(value) for value in highs[-range_lookback - 1:-1])
    prior_low = min(float(value) for value in lows[-range_lookback - 1:-1])
    breakout_buffer = max(atr * float(profile.confirmation_break_atr), 0.01)
    candle_range = max(high - low, 0.01)
    bullish_break = (
        last_close > prior_high + breakout_buffer
        and close_position >= float(profile.breakout_close_position)
        and body_ratio >= float(profile.breakout_minimum_body_ratio)
        and abs(last_close - opening)
        <= atr * float(profile.maximum_breakout_body_atr)
        and candle_range <= atr * float(profile.maximum_breakout_range_atr)
    )
    bearish_break = (
        last_close < prior_low - breakout_buffer
        and close_position <= 1.0 - float(profile.breakout_close_position)
        and body_ratio >= float(profile.breakout_minimum_body_ratio)
        and abs(last_close - opening)
        <= atr * float(profile.maximum_breakout_body_atr)
        and candle_range <= atr * float(profile.maximum_breakout_range_atr)
    )
    if bullish_break:
        add(
            "BUY", "BREAKOUT", prior_high, prior_high,
            0.82, 0.78, 0.72,
            ["Completed candle closed through a recent structural high with contained range."],
            extension=max(
                abs(price - prior_high) / atr,
                ema_distance or 0.0,
            ),
        )
    if bearish_break:
        add(
            "SELL", "BREAKOUT", prior_low, prior_low,
            0.82, 0.78, 0.72,
            ["Completed candle closed through a recent structural low with contained range."],
            extension=max(
                abs(price - prior_low) / atr,
                ema_distance or 0.0,
            ),
        )

    retest_window = min(4, size - 2)
    for direction in ("BUY", "SELL"):
        for breakout_index in range(last - retest_window, last):
            before_start = max(0, breakout_index - range_lookback)
            if direction == "BUY":
                boundary = max(float(value) for value in highs[before_start:breakout_index])
                broke = float(closes[breakout_index]) > boundary + breakout_buffer
                retested = (
                    broke
                    and float(lows[last]) <= boundary + tolerance
                    and last_close > boundary
                    and (bullish_rejection or last_close > opening)
                )
                invalidation = min(float(value) for value in lows[breakout_index:last + 1])
            else:
                boundary = min(float(value) for value in lows[before_start:breakout_index])
                broke = float(closes[breakout_index]) < boundary - breakout_buffer
                retested = (
                    broke
                    and float(highs[last]) >= boundary - tolerance
                    and last_close < boundary
                    and (bearish_rejection or last_close < opening)
                )
                invalidation = max(float(value) for value in highs[breakout_index:last + 1])
            if retested:
                add(
                    direction,
                    "BREAKOUT_RETEST",
                    boundary,
                    invalidation,
                    0.92,
                    0.95,
                    0.88,
                    ["A recent structural break returned to its boundary and held on a completed candle."],
                    extension=abs(price - boundary) / atr,
                )
                break

    # A. Trend pullback: trend context plus a meaningful zone/EMA test and a
    # developing rejection or refinement-timeframe shift.
    trend_bias = (
        structure.direction
        if structure.direction in ("BUY", "SELL")
        else context_direction
    )
    for direction in ("BUY", "SELL"):
        if trend_bias != direction:
            continue
        zones = structure.supports if direction == "BUY" else structure.resistances
        zone = _nearest_zone(zones, price, direction, tolerance)
        ema_level = indicators.ema20
        reference = (
            zone.center
            if zone is not None
            else float(ema_level) if ema_level is not None else None
        )
        if reference is None:
            continue
        near_level = abs(price - reference) <= tolerance or (
            min(abs(float(lows[index]) - reference) for index in range(max(0, last - 3), last + 1))
            <= tolerance
            if direction == "BUY"
            else min(abs(float(highs[index]) - reference) for index in range(max(0, last - 3), last + 1))
            <= tolerance
        )
        if direction == "BUY":
            recovery = (
                bullish_rejection
                or (
                    last_close > float(closes[last - 1])
                    and refinement_direction == "BUY"
                )
                or _momentum_direction(indicators, "BUY")
            )
        else:
            recovery = (
                bearish_rejection
                or (
                    last_close < float(closes[last - 1])
                    and refinement_direction == "SELL"
                )
                or _momentum_direction(indicators, "SELL")
            )
        if near_level and recovery:
            invalidation = structure.latest_low if direction == "BUY" else structure.latest_high
            if invalidation is None:
                invalidation = min(float(value) for value in lows[-5:]) if direction == "BUY" else max(float(value) for value in highs[-5:])
            add(
                direction,
                "TREND_PULLBACK",
                reference,
                invalidation,
                0.78 if (bullish_rejection or bearish_rejection) else 0.65,
                0.82,
                0.78 if (bullish_rejection or bearish_rejection) else 0.62,
                [
                    f"Price returned to {zone.kind if zone else 'EMA20 context'} in the {direction.lower()} trend.",
                    "Rejection, momentum recovery, or a refinement-timeframe shift is developing.",
                ],
            )

    # E. Support/resistance reaction: require a meaningful retest and rejection,
    # and do not fade a directional breakout regime.
    if regime in ("RANGE_BOUND", "TRANSITIONING_OR_UNCLEAR"):
        for direction, zones, rejection in (
            ("BUY", structure.supports, bullish_rejection),
            ("SELL", structure.resistances, bearish_rejection),
        ):
            zone = _nearest_zone(zones, price, direction, tolerance)
            if zone is None or not rejection:
                continue
            touched = (
                abs(low - zone.center) <= tolerance
                if direction == "BUY"
                else abs(high - zone.center) <= tolerance
            )
            if not touched:
                continue
            if zone.touches < 2 and not _momentum_direction(indicators, direction):
                continue
            invalidation = low if direction == "BUY" else high
            add(
                direction,
                "SUPPORT_RESISTANCE_REACTION",
                zone.center,
                invalidation,
                0.76,
                0.90,
                0.90,
                [f"Price tested a repeated {zone.kind} zone and closed with rejection."],
                extension=abs(price - zone.center) / atr,
            )

    # Duplicate detections at the same level are common when one candle is both
    # a sweep and a reaction; retain distinct setup types but cap the set.
    candidates.sort(
        key=lambda candidate: (
            candidate.trigger_strength + candidate.location_strength
            + candidate.price_action_strength
        ),
        reverse=True,
    )
    return candidates[:8]

