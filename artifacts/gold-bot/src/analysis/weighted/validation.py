"""Non-negotiable market-data and structural plan validation."""
from __future__ import annotations

import math


def _zone_value(zone, key: str):
    if isinstance(zone, dict):
        return zone.get(key)
    return getattr(zone, key, None)


def _opposing_levels(structure, direction: str, price: float) -> list[float]:
    """Return confirmed pivots and zone centers ahead of the proposed entry."""
    if direction == "BUY":
        pivots = [level for _, level in structure.swing_highs]
        zones = structure.resistances
    else:
        pivots = [level for _, level in structure.swing_lows]
        zones = structure.supports

    levels = []
    for raw in pivots:
        try:
            level = float(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(level) and (
            level > price if direction == "BUY" else level < price
        ):
            levels.append(level)
    for zone in zones:
        try:
            level = float(_zone_value(zone, "center"))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(level) and (
            level > price if direction == "BUY" else level < price
        ):
            levels.append(level)

    levels.sort(reverse=direction == "SELL")
    unique = []
    for level in levels:
        if not unique or abs(level - unique[-1]) > max(abs(price) * 1e-6, 0.01):
            unique.append(level)
    return unique


def validate_candidate(
    candidate,
    *,
    price: float,
    atr: float,
    structure,
    profile,
    spread: float | None,
    preferred_rr: float | None = None,
) -> dict:
    direction = candidate.direction
    if direction not in ("BUY", "SELL"):
        return {"valid": False, "rejection_reasons": ["Candidate direction is invalid."]}
    if not math.isfinite(price) or price <= 0:
        return {"valid": False, "rejection_reasons": ["Invalid entry price."]}
    if not math.isfinite(atr) or atr <= 0:
        return {"valid": False, "rejection_reasons": ["ATR14 is unavailable or invalid."]}
    try:
        invalidation_level = float(candidate.invalidation_level)
        candidate_level = float(candidate.level)
    except (TypeError, ValueError, OverflowError):
        return {"valid": False, "rejection_reasons": ["Candidate levels are invalid."]}
    if not math.isfinite(invalidation_level) or not math.isfinite(candidate_level):
        return {"valid": False, "rejection_reasons": ["Candidate levels are non-finite."]}
    if (
        (direction == "BUY" and invalidation_level >= price)
        or (direction == "SELL" and invalidation_level <= price)
    ):
        return {
            "valid": False,
            "rejection_reasons": [
                "Structural invalidation is not beyond entry in the risk direction."
            ],
        }
    spread_value = None
    if spread is not None:
        try:
            spread_value = float(spread)
        except (TypeError, ValueError, OverflowError):
            return {"valid": False, "rejection_reasons": ["Reference spread is invalid."]}
        if not math.isfinite(spread_value) or spread_value < 0:
            return {"valid": False, "rejection_reasons": ["Reference spread is invalid."]}
    buffer = atr * float(profile.stop_buffer_atr)
    if spread_value is not None:
        buffer += spread_value / 2.0
    # If the candidate invalidation sits within a nearby support/resistance
    # zone, put the stop beyond the full zone rather than just its midpoint.
    relevant_zones = (
        structure.supports if direction == "BUY" else structure.resistances
    )
    zone_edges = []
    for zone in relevant_zones:
        try:
            low = float(_zone_value(zone, "low"))
            high = float(_zone_value(zone, "high"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(low) or not math.isfinite(high) or low > high:
            continue
        tolerance = atr * float(profile.level_tolerance_atr)
        if low - tolerance <= invalidation_level <= high + tolerance:
            zone_edges.append(low if direction == "BUY" else high)
    if zone_edges:
        invalidation_level = (
            min([invalidation_level, *zone_edges])
            if direction == "BUY"
            else max([invalidation_level, *zone_edges])
        )
    stop = (
        invalidation_level - buffer
        if direction == "BUY"
        else invalidation_level + buffer
    )
    if (direction == "BUY" and stop >= price) or (direction == "SELL" and stop <= price):
        return {
            "valid": False,
            "rejection_reasons": ["Structural invalidation is on the wrong side of entry."],
        }
    risk = abs(price - stop)
    min_risk = atr * float(profile.minimum_stop_distance_atr)
    max_risk = atr * float(profile.maximum_stop_distance_atr)
    stop_widened_to_atr_floor = False
    if risk < min_risk:
        # Keep the real invalidation level, but do not publish a stop inside
        # this mode's minimum volatility distance. Widening stays beyond the
        # invalidation; if it exceeds the mode cap, reject the plan below.
        stop = price - min_risk if direction == "BUY" else price + min_risk
        risk = abs(price - stop)
        stop_widened_to_atr_floor = True
        if (
            (direction == "BUY" and stop >= invalidation_level)
            or (direction == "SELL" and stop <= invalidation_level)
        ):
            return {
                "valid": False,
                "rejection_reasons": [
                    "Volatility-floor stop does not remain beyond structural invalidation."
                ],
            }
    if risk > max_risk:
        return {
            "valid": False,
            "rejection_reasons": ["Structural stop exceeds the mode's configured ATR risk cap."],
        }

    opposing_levels = _opposing_levels(structure, direction, price)
    if not opposing_levels:
        return {
            "valid": False,
            "rejection_reasons": ["No confirmed opposing structural target is available."],
        }
    target_buffer = max(
        atr * float(profile.target_buffer_atr),
        spread_value / 2.0 if spread_value is not None else 0.0,
        0.01,
    )
    structural_targets = [
        level - target_buffer if direction == "BUY" else level + target_buffer
        for level in opposing_levels
    ]
    structural_targets = [
        target for target in structural_targets
        if target > price if direction == "BUY"
    ] if direction == "BUY" else [
        target for target in structural_targets if target < price
    ]
    if not structural_targets:
        return {
            "valid": False,
            "rejection_reasons": [
                "The nearest opposing zone leaves no executable target room."
            ],
        }
    nearest_level = opposing_levels[0]
    structural_target = structural_targets[0]
    minimum_rr = float(profile.minimum_target_rr)
    target_rr = max(
        minimum_rr,
        float(preferred_rr)
        if preferred_rr is not None
        else float(getattr(profile, "preferred_target_rr", minimum_rr)),
    )
    preferred_target = (
        price + risk * target_rr
        if direction == "BUY"
        else price - risk * target_rr
    )
    # TP1 is capped at the mode's preferred R:R, but never placed beyond the
    # nearest opposing level. If spread costs require slightly more room, that
    # room must still exist before the same real structural level.
    if spread_value is not None:
        minimum_spread_reward = minimum_rr * (risk + spread_value / 2.0) + spread_value
        minimum_spread_target = (
            price + minimum_spread_reward
            if direction == "BUY"
            else price - minimum_spread_reward
        )
        target = (
            min(structural_target, max(preferred_target, minimum_spread_target))
            if direction == "BUY"
            else max(structural_target, min(preferred_target, minimum_spread_target))
        )
    else:
        target = (
            min(structural_target, preferred_target)
            if direction == "BUY"
            else max(structural_target, preferred_target)
        )
    reward = target - price if direction == "BUY" else price - target
    if reward <= 0:
        return {
            "valid": False,
            "rejection_reasons": ["The nearest structural target is not ahead of entry."],
        }
    rr = reward / risk
    if rr < minimum_rr:
        return {
            "valid": False,
            "rejection_reasons": [
                f"Nearest opposing structure leaves only 1:{rr:.2f} R:R; "
                f"mode minimum is 1:{minimum_rr:.2f}."
            ],
            "nearest_opposing_level": nearest_level,
            "rr": round(rr, 2),
        }

    spread_rr = None
    spread_status = "unavailable"
    if spread_value is not None:
        spread_status = "passed"
        spread_atr = spread_value / atr
        if spread_atr > float(profile.maximum_spread_atr):
            spread_status = "rejected"
            return {
                "valid": False,
                "rejection_reasons": [
                    f"Observed reference spread is {spread_atr:.2f} ATR; "
                    f"configured cap is {float(profile.maximum_spread_atr):.2f} ATR."
                ],
                "spread_status": spread_status,
                "spread_atr": round(spread_atr, 4),
            }
        spread_rr = (reward - spread_value) / (risk + spread_value / 2.0)
        if spread_rr + 1e-9 < minimum_rr:
            return {
                "valid": False,
                "rejection_reasons": [
                    f"Reference-spread-adjusted R:R is 1:{spread_rr:.2f}, "
                    f"below the 1:{minimum_rr:.2f} minimum."
                ],
                "spread_status": "rejected",
                "spread_atr": round(spread_atr, 4),
                "spread_rr": round(spread_rr, 2),
            }

    # Further targets must be distinct, confirmed opposing levels. Pivot
    # prices and resistance/support-zone centers often describe the same area;
    # do not present those near-duplicates as separate profit milestones.
    minimum_target_spacing = max(
        atr * float(profile.minimum_target_room_atr),
        target_buffer * 2.0,
        spread_value or 0.0,
        0.01,
    )
    later_targets = []
    last_target = target
    for level in structural_targets:
        spacing = (
            level - last_target
            if direction == "BUY"
            else last_target - level
        )
        if spacing + 1e-9 < minimum_target_spacing:
            continue
        later_targets.append(level)
        last_target = level
        if len(later_targets) == 2:
            break
    tp2 = later_targets[0] if later_targets else None
    tp3 = later_targets[1] if len(later_targets) > 1 else None

    # All plan prices are published and persisted to cents. Recompute risk and
    # reward from those exact prices so the displayed ratios describe the plan
    # the tracker actually receives, not higher-precision intermediate values.
    published_entry = round(price, 2)
    published_stop = round(stop, 2)
    published_invalidation = round(invalidation_level, 2)
    published_targets = {
        "TP1": round(target, 2),
        "TP2": round(tp2, 2) if tp2 is not None else None,
        "TP3": round(tp3, 2) if tp3 is not None else None,
    }
    published_risk = abs(published_entry - published_stop)
    if published_risk <= 0:
        return {
            "valid": False,
            "rejection_reasons": ["Rounded entry and stop do not define positive risk."],
        }
    if (
        (
            direction == "BUY"
            and not published_stop < published_invalidation < published_entry
            < published_targets["TP1"]
        )
        or (
            direction == "SELL"
            and not published_stop > published_invalidation > published_entry
            > published_targets["TP1"]
        )
    ):
        return {
            "valid": False,
            "rejection_reasons": [
                "Rounded stop, invalidation, entry, and TP1 do not preserve valid trade geometry."
            ],
        }

    target_rrs = {}
    spread_target_rrs = {}
    for label, target_value in published_targets.items():
        if target_value is None:
            continue
        reward_value = (
            target_value - published_entry
            if direction == "BUY"
            else published_entry - target_value
        )
        target_rrs[label] = round(reward_value / published_risk, 2)
        if spread_value is not None:
            spread_target_rrs[label] = round(
                (reward_value - spread_value)
                / (published_risk + spread_value / 2.0),
                2,
            )
    published_rr = target_rrs["TP1"]
    published_spread_rr = spread_target_rrs.get("TP1")
    if published_rr + 1e-9 < minimum_rr:
        return {
            "valid": False,
            "rejection_reasons": [
                f"Rounded TP1 leaves only 1:{published_rr:.2f} R:R; "
                f"mode minimum is 1:{minimum_rr:.2f}."
            ],
            "rr": published_rr,
        }
    if (
        published_spread_rr is not None
        and published_spread_rr + 1e-9 < minimum_rr
    ):
        return {
            "valid": False,
            "rejection_reasons": [
                f"Rounded TP1 leaves reference-spread-adjusted R:R of "
                f"1:{published_spread_rr:.2f}, below the 1:{minimum_rr:.2f} minimum."
            ],
            "spread_status": "rejected",
            "spread_rr": published_spread_rr,
        }

    zone_half_width = atr * float(profile.entry_zone_atr)
    return {
        "valid": True,
        "entry": published_entry,
        "entry_zone_low": round(price - zone_half_width, 2),
        "entry_zone_high": round(price + zone_half_width, 2),
        "stop_loss": published_stop,
        "invalidation_level": published_invalidation,
        "take_profit": published_targets["TP1"],
        # Keep the old MarketAnalysis naming alongside the explicit TP alias
        # so callers cannot accidentally drop the first target.
        "tp1": published_targets["TP1"],
        "tp2": published_targets["TP2"],
        "tp3": published_targets["TP3"],
        "risk": round(published_risk, 5),
        "reward": round(abs(published_targets["TP1"] - published_entry), 5),
        "stop_widened_to_atr_floor": stop_widened_to_atr_floor,
        "stop_distance_atr": round(published_risk / atr, 4),
        "rr": published_rr,
        "target_rrs": target_rrs,
        "reference_spread_target_rrs": spread_target_rrs,
        "preferred_rr": round(target_rr, 2),
        "reference_spread_rr": published_spread_rr,
        "spread_status": spread_status,
        "minimum_target_spacing": round(minimum_target_spacing, 5),
        "spread_atr": (
            round(spread_value / atr, 4)
            if spread_value is not None and atr > 0
            else None
        ),
        "nearest_opposing_level": round(nearest_level, 2),
        "rejection_reasons": [],
    }

