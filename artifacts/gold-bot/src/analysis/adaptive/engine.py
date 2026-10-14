"""Deterministic multi-timeframe XAU/USD signal evaluation."""

from dataclasses import replace
from .config import PROFILES, resolve_timeframes
from .indicators import atr, ema, macd_histogram, rsi, valid_series
from .regime import classify_regime
from .scoring import classify_signal, score_directions
from .setups import detect_setups
from .structure import analyze_structure
from .validation import validate_plan


def evaluate(mode, setup_timeframe, frame_data, spread=None):
    profile = PROFILES[mode]
    roles = resolve_timeframes(mode, setup_timeframe)
    context = frame_data.get(roles["context"]) if roles["context"] else None
    setup = frame_data.get(roles["setup"])
    refinement = frame_data.get(roles["refinement"]) if roles["refinement"] else None
    result = {
        "mode": mode, "timeframe": roles["setup"], "timeframes": roles,
        "action": "NO TRADE", "direction": "WAIT", "category": "WAIT",
        "regime": "TRANSITIONING / UNCLEAR", "buy_score": 0, "sell_score": 0,
        "score_breakdown": {"BUY": {}, "SELL": {}}, "entry": 0.0, "stop": 0.0,
        "target": 0.0, "rr": 0.0, "setup_type": "", "reasoning": "",
        "rejection_reasons": [], "invalidation_conditions": [],
        "supporting_evidence": [], "data_valid": False, "spread_status": "unavailable",
        "rsi": None, "ema20": None, "ema50": None, "macd": None, "atr": None,
        "structure": "UNCLEAR", "support": None, "resistance": None,
        "support_next": None, "resistance_next": None, "context_structure": None,
    }
    if not roles["context"]:
        result["rejection_reasons"].append("No higher-timeframe context is configured for this selected setup timeframe")
        return result
    if not valid_series(setup) or (roles["context"] and not valid_series(context)):
        result["rejection_reasons"].append("Missing, malformed, simulated, or insufficient completed setup/context candles")
        return result
    if getattr(setup, "is_simulated", False) or getattr(context, "is_simulated", False):
        result["rejection_reasons"].append("Simulated market data cannot produce a trade signal")
        return result
    price = float(setup.price or setup.closes[-1])
    if not price > 0:
        result["rejection_reasons"].append("No valid live XAU/USD spot price")
        return result
    if refinement is not None and (not valid_series(refinement) or getattr(refinement, "is_simulated", False)):
        refinement = None  # refinement is optional and missing data earns no points

    a = atr(setup.highs, setup.lows, setup.closes)
    if not a or a <= 0:
        result["rejection_reasons"].append("ATR14 unavailable")
        return result
    if abs(price-float(setup.closes[-1])) > a*3.0:
        result["rejection_reasons"].append("Live spot price is materially detached from the latest completed candle")
        return result
    e20, e50 = ema(setup.closes,20), ema(setup.closes,50)
    rsi_v, macd_v = rsi(setup.closes), macd_histogram(setup.closes)
    setup_structure = analyze_structure(setup, a, profile.pivot_radius, profile.structure_lookback)
    context_atr = atr(context.highs, context.lows, context.closes) if context else None
    context_structure = analyze_structure(context, context_atr, profile.pivot_radius, profile.structure_lookback) if context else None
    refine_structure = None
    if refinement is not None:
        refine_atr = atr(refinement.highs, refinement.lows, refinement.closes)
        if refine_atr:
            refine_structure = analyze_structure(refinement, refine_atr, 1, min(profile.structure_lookback,48))
    regime = classify_regime(setup, setup_structure, a)
    if a / price > profile.maximum_atr_fraction:
        regime = "HIGH-VOLATILITY / UNSTABLE"
    selected_profile = replace(profile, setup_timeframe=roles["setup"])
    candidates = detect_setups(setup, setup_structure, context_structure, e20, e50, a, selected_profile)
    features = {"last":float(setup.closes[-1]),"ema20":e20,"ema50":e50,"rsi":rsi_v,"macd":macd_v,"atr":a}
    scores, breakdown, counts = score_directions(
        context_structure, setup_structure, refine_structure, candidates,
        features, regime, spread, profile,
    )
    direction = "BUY" if scores["BUY"] > scores["SELL"] else "SELL" if scores["SELL"] > scores["BUY"] else "WAIT"
    best = scores.get(direction,0)
    margin = abs(scores["BUY"]-scores["SELL"])
    matches = [x for x in candidates if x["direction"] == direction]
    result.update({
        "data_valid": True, "entry": round(price,2), "atr":round(a,4),
        "ema20":round(e20,4) if e20 is not None else None,
        "ema50":round(e50,4) if e50 is not None else None,
        "rsi":round(rsi_v,2) if rsi_v is not None else None,
        "macd":round(macd_v,6) if macd_v is not None else None,
        "structure":setup_structure["trend"], "regime":regime,
        "context_structure":context_structure["trend"] if context_structure else None,
        "support":setup_structure["support"],"support_next":setup_structure["support_next"],
        "resistance":setup_structure["resistance"],"resistance_next":setup_structure["resistance_next"],
        "buy_score":scores["BUY"], "sell_score":scores["SELL"],
        "score_breakdown":breakdown,
        "setup_type":", ".join(dict.fromkeys(x["type"] for x in matches)),
        "supporting_evidence":[x["trigger"] for x in matches],
        "spread_status":"available" if spread is not None else "unavailable",
    })
    if direction == "WAIT" or margin < profile.minimum_score_margin:
        result["rejection_reasons"].append("Directional scores are tied or materially conflicted")
        return result
    if regime == "HIGH-VOLATILITY / UNSTABLE":
        result["rejection_reasons"].append("High-volatility regime is unsuitable for this setup")
        return result
    if not matches:
        result["rejection_reasons"].append("No credible price-action setup at a meaningful level")
        return result
    category = classify_signal(best, counts[direction], profile, bool(matches))
    if category == "WAIT":
        result["rejection_reasons"].append(f"Directional score {best} is below the configured early threshold {profile.early_threshold}")
        return result

    # Do not chase a move already extended from its setup-timeframe EMA20.
    if e20 is not None and abs(price-e20) > a*profile.maximum_extension_atr and not any(x["type"] in ("breakout_retest","support_resistance_reaction") for x in matches):
        result["rejection_reasons"].append("Entry has extended beyond the mode's ATR limit")
        return result

    plan = validate_plan(
        direction, price, a, setup_structure, context_structure, profile, spread
    )
    if not plan["valid"]:
        result["rejection_reasons"].extend(plan["rejection_reasons"])
        return result

    result.update({
        "action":direction, "direction":direction, "category":category,
        "stop":plan["stop"], "target":plan["target"], "rr":plan["rr"],
        "reference_spread_rr":plan["reference_spread_rr"],
        "invalidation_conditions":plan["invalidation_conditions"],
        "reasoning":f"{category} {direction}: {result['setup_type']} on {roles['setup']}; context {roles['context']} is {context_structure['trend'] if context_structure else 'unavailable'}, regime is {regime}.",
    })
    return result
