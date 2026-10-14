"""Hard risk and execution screens; scores cannot override these checks."""


def validate_plan(direction, entry, atr_value, setup_structure,
                  context_structure, profile, spread=None):
    result = {
        "valid": False, "stop": 0.0, "target": 0.0, "rr": 0.0,
        "reference_spread_rr": None, "invalidation_conditions": [],
        "rejection_reasons": [],
    }
    if direction not in ("BUY", "SELL") or atr_value <= 0 or entry <= 0:
        result["rejection_reasons"].append("Invalid direction, entry, or ATR")
        return result

    swing_list = setup_structure["lows"] if direction == "BUY" else setup_structure["highs"]
    swings = [level for _, level in swing_list if level < entry] if direction == "BUY" else [level for _, level in swing_list if level > entry]
    if not swings:
        result["rejection_reasons"].append("No confirmed structural invalidation swing")
        return result
    swing = max(swings) if direction == "BUY" else min(swings)
    stop = swing-atr_value*profile.stop_buffer_atr if direction=="BUY" else swing+atr_value*profile.stop_buffer_atr
    risk = abs(entry-stop)
    if risk < atr_value*profile.minimum_stop_atr or risk > atr_value*profile.maximum_stop_atr:
        result["rejection_reasons"].append("Structural stop is outside this mode's reasonable ATR risk range")
        return result

    target_list = setup_structure["highs"] if direction=="BUY" else setup_structure["lows"]
    targets = [level for _,level in target_list if level > entry] if direction=="BUY" else [level for _,level in target_list if level < entry]
    if not targets and context_structure:
        target_list = context_structure["highs"] if direction=="BUY" else context_structure["lows"]
        targets = [level for _,level in target_list if level > entry] if direction=="BUY" else [level for _,level in target_list if level < entry]
    if not targets:
        result["rejection_reasons"].append("No observed opposing structural target ahead")
        return result
    level = min(targets) if direction=="BUY" else max(targets)
    target = level-atr_value*.08 if direction=="BUY" else level+atr_value*.08
    reward = target-entry if direction=="BUY" else entry-target
    rr = reward/risk if risk else 0.0
    if reward <= 0 or rr < profile.minimum_rr:
        result["rejection_reasons"].append(
            f"Nearest opposing structure is obstructed or below the configured 1:{profile.minimum_rr:g} R:R floor"
        )
        return result
    if spread is not None:
        if spread < 0 or spread/atr_value > profile.maximum_spread_atr:
            result["rejection_reasons"].append("Observed reference spread exceeds the mode's ATR limit")
            return result
        adjusted = (reward-spread)/(risk+spread/2) if risk+spread/2 else 0.0
        if adjusted < profile.minimum_rr:
            result["rejection_reasons"].append("Reference spread reduces the plan below its minimum R:R")
            return result
        result["reference_spread_rr"] = round(adjusted, 2)

    result.update({
        "valid": True, "stop": round(stop, 2), "target": round(target, 2),
        "rr": round(rr, 2),
        "invalidation_conditions": [
            f"{direction} thesis invalidates on a completed setup-timeframe close beyond {stop:.2f}"
        ],
    })
    return result
