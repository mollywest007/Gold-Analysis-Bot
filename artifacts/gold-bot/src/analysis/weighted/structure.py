"""Market structure, confirmed pivots, zones, and liquidity evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float
    center: float
    touches: int
    kind: str


@dataclass(frozen=True)
class StructureSnapshot:
    label: str
    direction: str
    break_direction: str
    swing_highs: tuple[tuple[int, float], ...]
    swing_lows: tuple[tuple[int, float], ...]
    supports: tuple[PriceZone, ...]
    resistances: tuple[PriceZone, ...]
    latest_high: float | None
    latest_low: float | None


def confirmed_swings(
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    lookback: int,
    radius: int,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    size = min(len(highs), len(lows))
    radius = max(1, int(radius))
    start = max(radius, size - max(lookback, radius * 2 + 1))
    highs_found: list[tuple[int, float]] = []
    lows_found: list[tuple[int, float]] = []
    # Pivots too close to the latest candle are not confirmed.
    for index in range(start, max(start, size - radius)):
        left_h = [float(value) for value in highs[index - radius:index]]
        right_h = [float(value) for value in highs[index + 1:index + radius + 1]]
        left_l = [float(value) for value in lows[index - radius:index]]
        right_l = [float(value) for value in lows[index + 1:index + radius + 1]]
        if not left_h or not right_h or not left_l or not right_l:
            continue
        high = float(highs[index])
        low = float(lows[index])
        if high >= max(left_h + right_h) and high > max(left_h):
            highs_found.append((index, high))
        if low <= min(left_l + right_l) and low < min(left_l):
            lows_found.append((index, low))
    return highs_found, lows_found


def _cluster_zones(
    levels: Sequence[float],
    *,
    tolerance: float,
    kind: str,
) -> list[PriceZone]:
    sorted_levels = sorted(float(level) for level in levels)
    clusters: list[list[float]] = []
    for level in sorted_levels:
        if not clusters or level - clusters[-1][-1] > max(tolerance, 0.01):
            clusters.append([level])
        else:
            clusters[-1].append(level)
    return [
        PriceZone(
            low=round(cluster[0], 5),
            high=round(cluster[-1], 5),
            center=round(sum(cluster) / len(cluster), 5),
            touches=len(cluster),
            kind=kind,
        )
        for cluster in clusters
    ]


def analyze_structure(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    atr: float | None,
    lookback: int,
    pivot_radius: int,
    zone_tolerance_atr: float = 0.25,
) -> StructureSnapshot:
    swing_highs, swing_lows = confirmed_swings(
        highs, lows, lookback=lookback, radius=pivot_radius
    )
    threshold = max(float(atr or 0.0) * 0.03, 0.01)
    label = "TRANSITION"
    direction = "WAIT"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1][1] > swing_highs[-2][1] + threshold
        hl = swing_lows[-1][1] > swing_lows[-2][1] + threshold
        lh = swing_highs[-1][1] < swing_highs[-2][1] - threshold
        ll = swing_lows[-1][1] < swing_lows[-2][1] - threshold
        if hh and hl:
            label, direction = "HH_HL", "BUY"
        elif lh and ll:
            label, direction = "LH_LL", "SELL"
        elif not any((hh, hl, lh, ll)):
            label, direction = "RANGE", "WAIT"

    break_direction = "NONE"
    if closes and swing_highs and float(closes[-1]) > swing_highs[-1][1] + threshold:
        break_direction = "BUY"
    elif closes and swing_lows and float(closes[-1]) < swing_lows[-1][1] - threshold:
        break_direction = "SELL"

    price = float(closes[-1]) if closes else 0.0
    zone_tolerance = max(float(atr or 0.0), 0.01) * zone_tolerance_atr
    supports = _cluster_zones(
        [level for _, level in swing_lows if level <= price + zone_tolerance],
        tolerance=zone_tolerance,
        kind="support",
    )
    resistances = _cluster_zones(
        [level for _, level in swing_highs if level >= price - zone_tolerance],
        tolerance=zone_tolerance,
        kind="resistance",
    )
    return StructureSnapshot(
        label=label,
        direction=direction,
        break_direction=break_direction,
        swing_highs=tuple(swing_highs),
        swing_lows=tuple(swing_lows),
        supports=tuple(supports),
        resistances=tuple(resistances),
        latest_high=swing_highs[-1][1] if swing_highs else None,
        latest_low=swing_lows[-1][1] if swing_lows else None,
    )


def liquidity_sweeps(
    highs: Sequence[float],
    lows: Sequence[float],
    opens: Sequence[float],
    closes: Sequence[float],
    *,
    atr: float,
    lookback: int,
    minimum_penetration_atr: float = 0.04,
) -> list[dict]:
    """Detect a range excursion only when the completed candle closes back in."""
    size = min(len(highs), len(lows), len(opens), len(closes))
    if size < 3 or atr <= 0:
        return []
    last = size - 1
    start = max(0, last - max(2, int(lookback)))
    prior_high = max(float(value) for value in highs[start:last])
    prior_low = min(float(value) for value in lows[start:last])
    penetration = max(atr * minimum_penetration_atr, 0.01)
    candle_range = max(float(highs[last]) - float(lows[last]), 0.01)
    close_position = (float(closes[last]) - float(lows[last])) / candle_range
    found = []
    if (
        float(lows[last]) < prior_low - penetration
        and float(closes[last]) > prior_low
        and close_position >= 0.55
    ):
        found.append(
            {
                "direction": "BUY",
                "level": prior_low,
                "penetration": prior_low - float(lows[last]),
                "evidence": "Completed candle swept the prior low and reclaimed the range.",
            }
        )
    if (
        float(highs[last]) > prior_high + penetration
        and float(closes[last]) < prior_high
        and close_position <= 0.45
    ):
        found.append(
            {
                "direction": "SELL",
                "level": prior_high,
                "penetration": float(highs[last]) - prior_high,
                "evidence": "Completed candle swept the prior high and re-entered the range.",
            }
        )
    return found

