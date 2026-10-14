"""Weighted directional confluence and non-indicator score validation."""


def score_directions(context_structure, setup_structure, refinement_structure,
                     setup_items, features, regime, spread, profile):
    weights = profile.weights
    scores = {"BUY": 0, "SELL": 0}
    detail = {"BUY": {}, "SELL": {}}
    evidence_count = {"BUY": 0, "SELL": 0}
    structure_trends = (
        context_structure["trend"] if context_structure else "UNCLEAR",
        setup_structure["trend"],
    )
    for direction in ("BUY", "SELL"):
        opposite = "SELL" if direction == "BUY" else "BUY"
        positive_trends = sum(
            trend == ("BULLISH" if direction == "BUY" else "BEARISH")
            for trend in structure_trends
        )
        structure_points = int(weights[0] * positive_trends / 2)
        if setup_structure["bos"] == direction:
            structure_points = min(weights[0], structure_points + 6)
        if any(t == ("BEARISH" if direction == "BUY" else "BULLISH") for t in structure_trends):
            structure_points = max(0, structure_points - 8)

        candidates = [x for x in setup_items if x["direction"] == direction]
        location_points = weights[1] if candidates else 0
        if candidates:
            location_points = int(weights[1] * (1.0 if any(x["level"] is not None for x in candidates) else .55))
        if refinement_structure:
            refine_trend = refinement_structure["trend"]
            if refine_trend in (("BULLISH" if direction=="BUY" else "BEARISH"), "RANGE"):
                location_points = min(weights[1], location_points + 2)
            elif refine_trend == ("BEARISH" if direction=="BUY" else "BULLISH"):
                location_points = max(0, location_points - 5)

        pa_points = 0
        if candidates and any(x["type"] in ("support_resistance_reaction","trend_pullback","liquidity_sweep","breakout_retest") for x in candidates):
            pa_points = weights[2]
        elif candidates:
            pa_points = int(weights[2] * .65)

        ema = features["ema20"]
        ema50 = features["ema50"]
        macd = features["macd"]
        rsi_value = features["rsi"]
        momentum_sign = 1 if direction == "BUY" else -1
        ema_support = ema is not None and ema50 is not None and features["last"] > ema and ema >= ema50 if direction == "BUY" else ema is not None and ema50 is not None and features["last"] < ema and ema <= ema50
        macd_support = macd is not None and macd * momentum_sign > 0
        rsi_support = rsi_value is not None and ((rsi_value >= 48 and direction=="BUY") or (rsi_value <= 52 and direction=="SELL"))
        # Correlated momentum signals share one capped category; they are not independent confirmations.
        momentum_points = int(weights[3] * (0.55*ema_support + 0.30*macd_support + 0.15*rsi_support))

        pattern_types = {x["type"] for x in candidates}
        liq_points = 0
        if "liquidity_sweep" in pattern_types:
            liq_points = weights[4]
        elif "breakout_retest" in pattern_types:
            liq_points = int(weights[4] * .95)
        elif "breakout" in pattern_types:
            liq_points = int(weights[4] * .8)

        # ATR suitability is measurable independently; unavailable spread
        # contributes zero rather than being treated as a favorable quote.
        volatility_points = int(weights[5] * .45)
        if spread is not None and regime != "HIGH-VOLATILITY / UNSTABLE":
            volatility_points += int(weights[5] * .55)
        penalty = 0
        if regime == "HIGH-VOLATILITY / UNSTABLE":
            penalty += 10
        if candidates and setup_structure["resistance" if direction=="BUY" else "support"] is not None:
            opposing = setup_structure["resistance"] if direction=="BUY" else setup_structure["support"]
            room = (opposing-features["last"]) if direction=="BUY" else (features["last"]-opposing)
            if 0 < room < features["atr"]*.65:
                penalty += 8

        parts = {
            "structure_bias": structure_points,
            "entry_location": location_points,
            "price_action": pa_points,
            "momentum_trend": momentum_points,
            "liquidity_breakout_retest": liq_points,
            "volatility_execution": volatility_points,
            "penalties": penalty,
        }
        scores[direction] = max(0, min(100, sum(v for k,v in parts.items() if k != "penalties")-penalty))
        detail[direction] = parts
        evidence_count[direction] = sum(parts[key] > 0 for key in (
            "structure_bias","entry_location","price_action","momentum_trend",
            "liquidity_breakout_retest","volatility_execution",
        ))
    return scores, detail, evidence_count


def classify_signal(score, evidence_count, profile, has_setup):
    if not has_setup or score < profile.early_threshold:
        return "WAIT"
    if score >= profile.strong_threshold and evidence_count >= 4:
        return "STRONG"
    if score >= profile.score_threshold and evidence_count >= 2:
        return "MODERATE"
    return "EARLY"
