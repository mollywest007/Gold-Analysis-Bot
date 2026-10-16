"""Non-negotiable market-data and structural plan validation."""
from __future__ import annotations

import math


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
    if not math.isfinite(price) or price <= 0:
        return {"valid": False, "rejection_reasons": ["Invalid entry price."]}
    if not math.isfinite(atr) or atr <= 0:
        return {"valid": False, "rejection_reasons": ["ATR14 is unavailable or invalid."]}
    invalidation_level = float(candidate.invalidation_level)
    buffer = atr * float(profile.stop_buffer_atr)
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
    if risk < min_risk:
        return {
            "valid": False,
            "rejection_reasons": ["Structural stop is inside the configured volatility floor."],
        }
    if risk > max_risk:
        return {
            "valid": False,
            "rejection_reasons": ["Structural stop exceeds the mode's configured ATR risk cap."],
        }

    pivot_levels = (
        [level for _, level in structure.swing_highs if level > price]
        if direction == "BUY"
        else [level for _, level in structure.swing_lows if level < price]
    )
    if not pivot_levels:
        return {
            "valid": False,
            "rejection_reasons": ["No confirmed opposing structural target is available."],
        }
    target_buffer = max(atr * float(profile.target_buffer_atr), 0.01)
    nearest_level = min(pivot_levels) if direction == "BUY" else max(pivot_levels)
    structural_target = (
        nearest_level - target_buffer
        if direction == "BUY"
        else nearest_level + target_buffer
    )
    minimum_rr = float(profile.minimum_target_rr)
    target_rr = max(
        minimum_rr,
        float(preferred_rr) if preferred_rr is not None else minimum_rr,
    )
    preferred_target = (
        price + risk * target_rr
        if direction == "BUY"
        else price - risk * target_rr
    )
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
    if spread is not None and math.isfinite(float(spread)) and spread >= 0:
        spread_status = "passed"
        spread_atr = float(spread) / atr
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
        spread_rr = (reward - float(spread)) / (risk + float(spread) / 2.0)
        if spread_rr < minimum_rr:
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

    zone_half_width = atr * float(profile.entry_zone_atr)
    return {
        "valid": True,
        "entry": round(price, 2),
        "entry_zone_low": round(price - zone_half_width, 2),
        "entry_zone_high": round(price + zone_half_width, 2),
        "stop_loss": round(stop, 2),
        "invalidation_level": round(invalidation_level, 2),
        "take_profit": round(target, 2),
        "risk": round(risk, 5),
        "reward": round(reward, 5),
        "rr": round(rr, 2),
        "preferred_rr": round(target_rr, 2),
        "reference_spread_rr": round(spread_rr, 2) if spread_rr is not None else None,
        "spread_status": spread_status,
        "spread_atr": (
            round(float(spread) / atr, 4)
            if spread is not None and atr > 0
            else None
        ),
        "nearest_opposing_level": round(nearest_level, 2),
        "rejection_reasons": [],
    }

