"""Weighted, mode-specific multi-timeframe XAU/USD decision engine."""
from __future__ import annotations

from dataclasses import asdict
from typing import Mapping

from .config import ResolvedAnalysisConfig
from .indicators import IndicatorSnapshot, calculate_indicators
from .market import prepare_completed_candles
from .regime import classify_regime
from .scoring import score_directions
from .setups import SetupCandidate, detect_setups
from .structure import StructureSnapshot, analyze_structure
from .validation import validate_candidate


def _data_is_spot_normalized(data) -> bool:
    return (
        data is not None
        and not bool(getattr(data, "is_simulated", False))
        and getattr(data, "price_source", "") == "spot"
        and getattr(data, "candle_source", "") == "spot_normalized_futures"
    )


def _snapshot_report(
    structure: StructureSnapshot | None,
    indicators: IndicatorSnapshot | None,
    latest_timestamp: float | None,
) -> dict:
    if structure is None:
        return {"available": False, "direction": "WAIT"}
    return {
        "available": True,
        "direction": structure.direction,
        "market_structure": structure.label,
        "structure_break": structure.break_direction,
        "swing_highs": [level for _, level in structure.swing_highs[-6:]],
        "swing_lows": [level for _, level in structure.swing_lows[-6:]],
        "supports": [asdict(zone) for zone in structure.supports[-4:]],
        "resistances": [asdict(zone) for zone in structure.resistances[-4:]],
        "ema20": indicators.ema20 if indicators else None,
        "ema50": indicators.ema50 if indicators else None,
        "rsi14": indicators.rsi14 if indicators else None,
        "macd_histogram": indicators.macd_hist if indicators else None,
        "atr14": indicators.atr14 if indicators else None,
        "atr_expansion": indicators.atr_expansion if indicators else None,
        "latest_completed_candle": latest_timestamp,
    }


def _candidate_reason(candidate: SetupCandidate | None) -> str:
    if candidate is None:
        return "No credible entry pattern was detected."
    return " ".join(candidate.evidence)


def _category_for_score(
    score: int,
    breakdown: dict,
    candidate: SetupCandidate,
    *,
    config: ResolvedAnalysisConfig,
    major_conflict: bool,
) -> str:
    if major_conflict:
        return "WAIT"
    supported_categories = sum(value > 0 for value in breakdown.values())
    if (
        score >= config.strong_score
        and supported_categories >= 4
    ):
        return "STRONG"
    if (
        score >= config.moderate_score
        and candidate.location_strength > 0
        and candidate.price_action_strength > 0
        and (
            candidate.direction
            in ("BUY", "SELL")
        )
    ):
        return "MODERATE"
    if (
        score >= config.early_score
        and candidate.trigger_strength > 0
        and candidate.location_strength > 0
        and supported_categories >= 2
    ):
        return "EARLY"
    return "WAIT"


def analyze_weighted_market(
    *,
    config: ResolvedAnalysisConfig,
    raw_data: Mapping[str, object],
    spread: float | None,
    now: float | None = None,
) -> dict:
    """Analyze the selected setup timeframe with mode-resolved MTF context.

    Context and setup candles must be real, fresh, completed OHLCV. Refinement
    candles are optional, so the Scalp entry can remain usable when M1 data is
    unavailable; missing refinement evidence contributes no score.
    """
    role_timeframes = {
        "context": config.context_timeframe,
        "setup": config.setup_timeframe,
        "refinement": config.refinement_timeframe,
    }
    prepared_by_tf = {}
    statuses = {}
    for timeframe in dict.fromkeys(role_timeframes.values()):
        raw = raw_data.get(timeframe)
        # Use the setup mode's explicit freshness window for every role; it is
        # expressed in each role's own timeframe bars.
        prepared = prepare_completed_candles(
            raw,
            timeframe,
            now=now,
            max_age_bars=config.max_candle_age_bars,
        )
        prepared_by_tf[timeframe] = prepared
        statuses[timeframe] = prepared

    role_data = {}
    for role, timeframe in role_timeframes.items():
        prepared = prepared_by_tf.get(timeframe)
        data = prepared.data if prepared else None
        # Unverified and simulated feeds remain visible in the quality report,
        # but are excluded from every positive score, including optional refine.
        role_data[role] = data if _data_is_spot_normalized(data) else None
    role_status = {
        role: statuses[timeframe]
        for role, timeframe in role_timeframes.items()
    }
    setup_data = role_data["setup"]
    context_data = role_data["context"]
    invalid_data_reasons = []
    for role in ("context", "setup"):
        status = role_status[role]
        if status.data is None:
            invalid_data_reasons.append(
                f"{role.title()} {role_timeframes[role]} data {status.status}: {status.reason}"
            )
        elif not _data_is_spot_normalized(status.data):
            invalid_data_reasons.append(
                f"{role.title()} {role_timeframes[role]} is not validated spot-normalized OHLCV."
            )

    def build_role(role: str, pivot_delta: int):
        data = role_data[role]
        status = role_status[role]
        if data is None or len(data.closes) < 30:
            return None, None
        profile = config.profile
        lookback = int(profile.structure_lookback_candles)
        if role == "context":
            lookback = max(lookback, int(lookback * 1.25))
        elif role == "refinement":
            lookback = max(40, int(lookback * 0.75))
        indicators = calculate_indicators(
            data.highs,
            data.lows,
            data.closes,
            atr_period=14,
        )
        structure = analyze_structure(
            data.highs,
            data.lows,
            data.closes,
            atr=indicators.atr14,
            lookback=lookback,
            pivot_radius=max(1, int(profile.structure_pivot_radius) + pivot_delta),
            zone_tolerance_atr=float(profile.level_tolerance_atr),
        )
        return structure, indicators

    context_structure, context_indicators = build_role("context", 1)
    setup_structure, setup_indicators = build_role("setup", 0)
    refinement_structure, refinement_indicators = build_role("refinement", -1)
    structures = {
        "context": context_structure,
        "setup": setup_structure,
        "refinement": refinement_structure,
    }
    indicators = {
        "context": context_indicators,
        "setup": setup_indicators,
        "refinement": refinement_indicators,
    }

    price = (
        float(setup_data.price or setup_data.closes[-1])
        if setup_data is not None and setup_data.closes
        else 0.0
    )
    atr_value = (
        float(setup_indicators.atr14)
        if setup_indicators and setup_indicators.atr14 is not None
        else 0.0
    )
    latest_setup_ts = (
        float(setup_data.timestamps[-1])
        if setup_data is not None and setup_data.timestamps
        else None
    )
    regime_snapshot = classify_regime(
        setup_structure or StructureSnapshot(
            "TRANSITION", "WAIT", "NONE", (), (), (), (), None, None
        ),
        setup_indicators or IndicatorSnapshot(None, None, None, None, None, None, None, None, None),
        price=price,
        candle_timestamp=latest_setup_ts,
        volatility_limit=config.atr_expansion_limit,
    )
    candidates: list[SetupCandidate] = []
    if (
        setup_data is not None
        and setup_structure is not None
        and setup_indicators is not None
        and atr_value > 0
    ):
        candidates = detect_setups(
            setup_data.opens,
            setup_data.highs,
            setup_data.lows,
            setup_data.closes,
            price=price,
            atr=atr_value,
            indicators=setup_indicators,
            structure=setup_structure,
            context_direction=(
                context_structure.direction if context_structure else "WAIT"
            ),
            refinement_direction=(
                refinement_structure.direction if refinement_structure else "WAIT"
            ),
            regime=regime_snapshot.label,
            profile=config.profile,
            breakout_lookback=config.breakout_lookback,
            sweep_lookback=config.sweep_lookback,
        )

    scores = score_directions(
        structures=structures,
        indicators=indicators,
        candidates=candidates,
        regime=regime_snapshot.label,
        price=price,
        atr=atr_value if atr_value > 0 else None,
        spread=spread,
        profile=config.profile,
        config=config,
    )

    best_direction = max(
        ("BUY", "SELL"),
        key=lambda side: (
            scores[side]["candidate"] is not None,
            scores[side]["score"],
        ),
    )
    chosen = scores[best_direction]["candidate"]
    other_score = scores["SELL" if best_direction == "BUY" else "BUY"]["score"]
    directional_rejections = []
    if chosen is not None and abs(scores[best_direction]["score"] - other_score) < 5:
        chosen = None
        directional_rejections.append(
            "BUY and SELL evidence are too close to establish a directional edge."
        )
    if (
        chosen is not None
        and context_structure is not None
        and setup_structure is not None
        and context_structure.direction == setup_structure.direction
        and context_structure.direction in ("BUY", "SELL")
        and context_structure.direction != chosen.direction
    ):
        major_conflict = True
    else:
        major_conflict = False

    chosen_score = scores[best_direction]
    validation = (
        validate_candidate(
            chosen,
            price=price,
            atr=atr_value,
            structure=setup_structure,
            profile=config.profile,
            spread=spread,
            preferred_rr=config.preferred_rr,
        )
        if chosen is not None and setup_structure is not None
        else {"valid": False, "rejection_reasons": []}
    )
    category = (
        _category_for_score(
            chosen_score["score"],
            chosen_score["breakdown"],
            chosen,
            config=config,
            major_conflict=major_conflict,
        )
        if chosen is not None
        else "WAIT"
    )
    rejection_reasons = list(invalid_data_reasons)
    if chosen is None:
        rejection_reasons.append("No setup has sufficient, non-conflicting evidence.")
    if chosen is not None and major_conflict:
        rejection_reasons.append(
            "Context and setup structures both materially oppose the candidate direction."
        )
    if chosen is not None and not validation.get("valid"):
        rejection_reasons.extend(validation.get("rejection_reasons", []))
    if regime_snapshot.label == "HIGH_VOLATILITY":
        rejection_reasons.append(
            "ATR expansion is above this mode's volatility limit; setup is not actionable."
        )
    if not atr_value:
        rejection_reasons.append("ATR14 is unavailable; volatility and stop validation failed.")

    required_data_valid = not invalid_data_reasons
    if (
        not required_data_valid
        or not validation.get("valid")
        or regime_snapshot.label == "HIGH_VOLATILITY"
        or not atr_value
    ):
        category = "WAIT"

    direction = chosen.direction if chosen is not None else "WAIT"
    signal_label = f"{category} {direction}" if category != "WAIT" else "WAIT"
    is_actionable = category in ("MODERATE", "STRONG")
    action = direction if is_actionable else "WAIT"
    data_is_simulated = bool(
        (
            role_status["setup"].data
            and role_status["setup"].data.is_simulated
        )
        or (
            role_status["context"].data
            and role_status["context"].data.is_simulated
        )
    )
    setup_source_valid = bool(setup_data and _data_is_spot_normalized(setup_data))
    data_quality = (
        "SIMULATED"
        if data_is_simulated
        else "REAL_OHLCV"
        if setup_source_valid and context_data and _data_is_spot_normalized(context_data)
        else "PARTIAL_OR_UNVERIFIED"
    )
    bias_direction = (
        direction
        if direction in ("BUY", "SELL")
        else (
            setup_structure.direction
            if setup_structure and setup_structure.direction in ("BUY", "SELL")
            else context_structure.direction
            if context_structure and context_structure.direction in ("BUY", "SELL")
            else "WAIT"
        )
    )
    bias = {"BUY": "Bullish", "SELL": "Bearish", "WAIT": "Unclear"}[bias_direction]
    if chosen is not None and not validation.get("valid"):
        rejection_reasons.extend(
            reason for reason in validation.get("rejection_reasons", [])
            if reason not in rejection_reasons
        )
    if category == "WAIT" and not rejection_reasons:
        rejection_reasons.append("Evidence did not reach the configured mode threshold.")

    plan = validation if validation.get("valid") else {}
    timeframe_reports = {
        role: {
            "timeframe": role_timeframes[role],
            "data_status": role_status[role].status,
            "data_reason": role_status[role].reason,
            **_snapshot_report(
                structures[role],
                indicators[role],
                (
                    float(role_data[role].timestamps[-1])
                    if role_data[role] is not None and role_data[role].timestamps
                    else role_status[role].latest_completed_timestamp
                ),
            ),
        }
        for role in role_timeframes
    }
    evidence = list(chosen.evidence) if chosen else []
    if chosen is not None and regime_snapshot.label:
        evidence.append(f"Regime: {regime_snapshot.label.replace('_', ' ').lower()}.")
    if context_structure and context_structure.direction == direction and direction != "WAIT":
        evidence.append(
            f"{role_timeframes['context']} context structure supports {direction}."
        )
    if setup_structure and setup_structure.direction == direction and direction != "WAIT":
        evidence.append(
            f"{role_timeframes['setup']} setup structure supports {direction}."
        )
    if spread is None:
        execution_note = (
            "No fresh validated bid/ask was available; spread suitability received "
            "no positive score. Historical commissions and slippage are unavailable."
        )
    else:
        execution_note = (
            "A validated reference spread was screened; broker-specific commission "
            "and slippage are unavailable."
        )

    return {
        "mode": config.mode,
        "timeframe": config.setup_timeframe,
        "context_timeframe": config.context_timeframe,
        "setup_timeframe": config.setup_timeframe,
        "refinement_timeframe": config.refinement_timeframe,
        "market_bias": bias,
        "direction": direction,
        "action": action,
        "signal_category": category,
        "signal_label": signal_label,
        "setup_status": (
            "DEVELOPING" if category == "EARLY"
            else "MODERATE ENTRY" if category in ("MODERATE", "STRONG")
            else "WAIT"
        ),
        "setup_type": chosen.setup_type if chosen else "",
        "setup_reason": _candidate_reason(chosen),
        "regime": regime_snapshot.label,
        "session": regime_snapshot.session,
        "price": round(price, 2) if price > 0 else 0.0,
        "atr14": round(atr_value, 5) if atr_value else None,
        "ema20": setup_indicators.ema20 if setup_indicators else None,
        "ema50": setup_indicators.ema50 if setup_indicators else None,
        "rsi14": setup_indicators.rsi14 if setup_indicators else None,
        "macd_histogram": setup_indicators.macd_hist if setup_indicators else None,
        "buy_score": scores["BUY"]["score"],
        "sell_score": scores["SELL"]["score"],
        "score_breakdown": {
            "BUY": scores["BUY"]["breakdown"],
            "SELL": scores["SELL"]["breakdown"],
            "weights": dict(config.score_weights),
            "penalties": {
                "BUY": scores["BUY"]["penalties"],
                "SELL": scores["SELL"]["penalties"],
            },
        },
        "confidence_score": (
            scores[best_direction]["score"]
            if chosen is not None
            else max(scores["BUY"]["score"], scores["SELL"]["score"])
        ),
        "thresholds": {
            "early": config.early_score,
            "moderate": config.moderate_score,
            "strong": config.strong_score,
        },
        "entry": plan.get("entry", 0.0),
        "entry_zone_low": plan.get("entry_zone_low", 0.0),
        "entry_zone_high": plan.get("entry_zone_high", 0.0),
        "stop_loss": plan.get("stop_loss", 0.0),
        "invalidation": plan.get("invalidation_level", 0.0),
        "take_profit": plan.get("take_profit", 0.0),
        "rr": plan.get("rr", 0.0),
        "reference_spread_rr": plan.get("reference_spread_rr"),
        "spread_status": validation.get("spread_status", "unavailable"),
        "spread_atr": validation.get("spread_atr"),
        "spread": spread,
        "preferred_rr": config.preferred_rr,
        "minimum_rr": float(config.profile.minimum_target_rr),
        "stop_buffer_atr": float(config.profile.stop_buffer_atr),
        "minimum_stop_atr": float(config.profile.minimum_stop_distance_atr),
        "maximum_stop_atr": float(config.profile.maximum_stop_distance_atr),
        "maximum_ema_distance_atr": float(config.profile.maximum_ema_distance_atr),
        "structure_lookback": int(config.profile.structure_lookback_candles),
        "breakout_lookback": config.breakout_lookback,
        "sweep_lookback": config.sweep_lookback,
        "atr_expansion_limit": config.atr_expansion_limit,
        "maximum_spread_atr": float(config.profile.maximum_spread_atr),
        "expected_holding_bars": int(config.profile.expected_holding_bars),
        "setup_candidate": chosen,
        "setup_evidence": evidence,
        "rejection_reasons": list(dict.fromkeys(rejection_reasons + directional_rejections)),
        "invalidation_conditions": (
            [
                f"{direction} thesis invalidates on a completed "
                f"{config.setup_timeframe} close beyond "
                f"{plan['invalidation_level']:.2f}."
            ]
            if plan else ["No validated active setup; remain flat."]
        ),
        "market_structure": setup_structure.label if setup_structure else "UNAVAILABLE",
        "structure_detail": (
            setup_structure.label if setup_structure else "Setup structure unavailable."
        ),
        "timeframe_reports": timeframe_reports,
        "latest_candle_timestamp": latest_setup_ts or 0.0,
        "data_quality": data_quality,
        "is_simulated": data_is_simulated,
        "required_data_valid": required_data_valid,
        "execution_note": execution_note,
        "major_conflict": major_conflict,
    }

