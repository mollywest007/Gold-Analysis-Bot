"""Weighted directional scores with correlated indicators grouped together."""
from __future__ import annotations

from .indicators import IndicatorSnapshot
from .setups import SetupCandidate
from .structure import StructureSnapshot


SCORE_WEIGHTS = {
    "market_structure": 25,
    "entry_location": 20,
    "price_action": 15,
    "momentum_trend": 15,
    "liquidity_breakout_retest": 15,
    "volatility_execution": 10,
}


def _momentum_support(
    direction: str,
    indicators: IndicatorSnapshot | None,
    price: float,
) -> float:
    """EMA/RSI/MACD form one 15-point category, never three independent votes."""
    if indicators is None:
        return 0.0
    supports: list[float] = []
    if indicators.ema20 is not None and indicators.ema50 is not None:
        if direction == "BUY":
            supports.append(1.0 if indicators.ema20 > indicators.ema50 and price >= indicators.ema20
                            else 0.5 if indicators.ema20 > indicators.ema50 or price >= indicators.ema20
                            else -1.0)
        else:
            supports.append(1.0 if indicators.ema20 < indicators.ema50 and price <= indicators.ema20
                            else 0.5 if indicators.ema20 < indicators.ema50 or price <= indicators.ema20
                            else -1.0)
    if indicators.rsi14 is not None and indicators.rsi14_previous is not None:
        change = indicators.rsi14 - indicators.rsi14_previous
        if direction == "BUY":
            supports.append(1.0 if indicators.rsi14 >= 52 else 0.5 if change > 0 and indicators.rsi14 >= 40 else -0.5 if indicators.rsi14 < 30 else 0.0)
        else:
            supports.append(1.0 if indicators.rsi14 <= 48 else 0.5 if change < 0 and indicators.rsi14 <= 60 else -0.5 if indicators.rsi14 > 70 else 0.0)
    if indicators.macd_hist is not None and indicators.macd_hist_previous is not None:
        rising = indicators.macd_hist > indicators.macd_hist_previous
        if direction == "BUY":
            supports.append(1.0 if indicators.macd_hist > 0 and rising else 0.5 if rising else -0.5 if indicators.macd_hist < 0 and not rising else 0.0)
        else:
            supports.append(1.0 if indicators.macd_hist < 0 and not rising else 0.5 if not rising else -0.5 if indicators.macd_hist > 0 and rising else 0.0)
    # Divide by the full three-indicator set so missing values cannot boost score.
    return max(0.0, min(1.0, sum(supports) / 3.0))


def _pattern_strength(candidate: SetupCandidate | None) -> float:
    if candidate is None:
        return 0.0
    base = {
        "LIQUIDITY_SWEEP": 1.0,
        "BREAKOUT_RETEST": 0.92,
        "BREAKOUT": 0.82,
        "TREND_PULLBACK": 0.75,
        "SUPPORT_RESISTANCE_REACTION": 0.68,
    }.get(candidate.setup_type, 0.0)
    return max(0.0, min(1.0, base * candidate.trigger_strength))


def score_directions(
    *,
    structures: dict[str, StructureSnapshot | None],
    indicators: dict[str, IndicatorSnapshot | None],
    candidates: list[SetupCandidate],
    regime: str,
    price: float,
    atr: float | None,
    spread: float | None,
    profile,
    config,
) -> dict:
    candidate_by_direction = {
        direction: max(
            (candidate for candidate in candidates if candidate.direction == direction),
            key=lambda candidate: candidate.trigger_strength + candidate.location_strength,
            default=None,
        )
        for direction in ("BUY", "SELL")
    }
    frame_weights = {
        "context": config.context_weight,
        "setup": config.setup_weight,
        "refinement": config.refinement_weight,
    }
    output = {}
    for direction in ("BUY", "SELL"):
        breakdown = {}
        structure_points = 0.0
        for frame, weight in frame_weights.items():
            snapshot = structures.get(frame)
            if snapshot is not None and snapshot.direction == direction:
                structure_points += SCORE_WEIGHTS["market_structure"] * weight
            if (
                snapshot is not None
                and snapshot.break_direction == direction
                and frame in ("context", "setup")
            ):
                structure_points += 1.0 if frame == "setup" else 0.5
        breakdown["market_structure"] = min(
            SCORE_WEIGHTS["market_structure"], structure_points
        )

        candidate = candidate_by_direction[direction]
        breakdown["entry_location"] = (
            SCORE_WEIGHTS["entry_location"] * candidate.location_strength
            if candidate else 0.0
        )
        breakdown["price_action"] = (
            SCORE_WEIGHTS["price_action"] * candidate.price_action_strength
            if candidate else 0.0
        )
        breakdown["momentum_trend"] = (
            SCORE_WEIGHTS["momentum_trend"]
            * _momentum_support(direction, indicators.get("setup"), price)
        )
        breakdown["liquidity_breakout_retest"] = (
            SCORE_WEIGHTS["liquidity_breakout_retest"]
            * _pattern_strength(candidate)
        )

        volatility_points = 0.0
        setup_indicators = indicators.get("setup")
        if (
            atr is not None
            and atr > 0
            and setup_indicators is not None
            and setup_indicators.atr_expansion is not None
            and setup_indicators.atr_expansion < config.atr_expansion_limit
        ):
            volatility_points += 5.0
        if spread is not None and atr is not None and atr > 0:
            if spread <= atr * float(profile.maximum_spread_atr):
                volatility_points += 5.0
        breakdown["volatility_execution"] = min(10.0, volatility_points)

        penalties = []
        context = structures.get("context")
        setup = structures.get("setup")
        refinement = structures.get("refinement")
        if context is not None and context.direction in ("BUY", "SELL") and context.direction != direction:
            penalties.append(("opposing_context_structure", 6.0))
        if setup is not None and setup.direction in ("BUY", "SELL") and setup.direction != direction:
            penalties.append(("opposing_setup_structure", 7.0))
        if refinement is not None and refinement.direction in ("BUY", "SELL") and refinement.direction != direction:
            penalties.append(("opposing_refinement_structure", 2.0))
        if regime == "TRANSITIONING_OR_UNCLEAR":
            penalties.append(("unclear_regime", 3.0))
        if regime == "HIGH_VOLATILITY":
            penalties.append(("high_volatility", 8.0))

        raw_score = sum(breakdown.values())
        score = max(0, min(100, round(raw_score - sum(value for _, value in penalties))))
        output[direction] = {
            "score": score,
            "breakdown": {
                key: round(value, 1) for key, value in breakdown.items()
            },
            "penalties": [
                {"reason": reason, "points": value}
                for reason, value in penalties
            ],
            "candidate": candidate,
        }
    return output

