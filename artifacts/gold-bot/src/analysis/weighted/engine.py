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


def _build_entry_confirmation_levels(
    *,
    candidate: SetupCandidate | None,
    setup_data,
    price: float,
    atr: float,
    profile,
    breakout_lookback: int,
    category: str,
    action: str,
    rejection_reasons: list[str] | None = None,
    data_is_valid: bool = False,
) -> dict:
    """Expose exact live levels and stages from the active weighted setup rules."""
    result = {
        "available": False,
        "direction": "WAIT",
        "current_price": float(price or 0.0) or None,
        "setup_type": "",
        "breakout_level": None,
        "retest_level": None,
        "retest_tolerance": None,
        "confirmation_level": None,
        "confirmation_rule": "Verified completed setup candles are unavailable.",
        "breakout_required": False,
        "watches": {},
        "steps": [],
        "entry_status": "WAIT — verified live setup data unavailable",
        "next_action": "Wait for verified live spot data, then refresh Analysis.",
    }
    if not data_is_valid or setup_data is None or price <= 0 or atr <= 0:
        return result

    opens = list(getattr(setup_data, "opens", ()) or ())
    highs = list(getattr(setup_data, "highs", ()) or ())
    lows = list(getattr(setup_data, "lows", ()) or ())
    closes = list(getattr(setup_data, "closes", ()) or ())
    size = min(len(opens), len(highs), len(lows), len(closes))
    if size < 20:
        return result
    highs, lows, closes = highs[:size], lows[:size], closes[:size]
    range_lookback = min(max(int(breakout_lookback), 5), size - 1)
    prior_high = max(float(value) for value in highs[-range_lookback - 1:-1])
    prior_low = min(float(value) for value in lows[-range_lookback - 1:-1])
    breakout_buffer = max(
        float(atr) * float(profile.confirmation_break_atr), 0.01
    )
    retest_tolerance = max(
        float(atr) * float(profile.level_tolerance_atr), 0.01
    )

    def price_text(value: float) -> str:
        return f"{float(value):,.4f}".rstrip("0").rstrip(".")

    def watch(side: str, boundary: float) -> dict:
        breakout_trigger = (
            boundary + breakout_buffer
            if side == "BUY"
            else boundary - breakout_buffer
        )
        return {
            "breakout_level": boundary,
            "breakout_trigger_level": breakout_trigger,
            "retest_level": boundary,
            "retest_tolerance": retest_tolerance,
            "confirmation_level": boundary,
        }

    result.update(
        available=True,
        current_price=float(price),
        watches={
            "BUY": watch("BUY", prior_high),
            "SELL": watch("SELL", prior_low),
        },
    )
    if candidate is None:
        result.update(
            steps=[
                (
                    f"1. Breakout: BUY completed close above "
                    f"{price_text(prior_high + breakout_buffer)}; SELL below "
                    f"{price_text(prior_low - breakout_buffer)}."
                ),
                (
                    f"2. Pullback/retest: revisit {price_text(prior_high)} or "
                    f"{price_text(prior_low)} within ±{price_text(retest_tolerance)} "
                    "and hold or reject."
                ),
                (
                    f"3. Retest confirmation: close back above the BUY level or "
                    "below the SELL level; a contained breakout may qualify "
                    "without a retest. Stay WAIT for all mode gates."
                ),
            ],
            entry_status="WAIT — no qualifying setup selected",
            next_action=(
                "Wait for completed-candle price action and refresh Analysis "
                "to recalculate these levels."
            ),
        )
        return result

    direction = str(candidate.direction).upper()
    setup_type = str(candidate.setup_type).upper()
    candidate_level = float(candidate.level)
    breakout_setups = {
        "BREAKOUT",
        "BREAKOUT_RETEST",
        "FAILED_BREAKOUT",
        "LIQUIDITY_SWEEP",
    }
    breakout_level = (
        candidate_level
        if setup_type in breakout_setups
        else prior_high if direction == "BUY" else prior_low
    )
    retest_level = candidate_level
    confirmation_level = None
    if setup_type == "BREAKOUT":
        confirmation_level = (
            breakout_level + breakout_buffer
            if direction == "BUY"
            else breakout_level - breakout_buffer
        )
        confirmation_rule = (
            "Completed candle close beyond the breakout level plus the mode ATR "
            "buffer; configured candle-shape limits also apply."
        )
    elif setup_type == "BREAKOUT_RETEST":
        confirmation_level = candidate_level
        confirmation_rule = (
            f"After the buffered break, retest {price_text(candidate_level)} within "
            f"±{price_text(retest_tolerance)} and close back on the {direction.lower()} "
            "side with a directional candle or rejection."
        )
    elif setup_type == "FAILED_BREAKOUT":
        confirmation_level = candidate_level
        confirmation_rule = (
            f"After the break, a completed candle must re-enter the range across "
            f"{price_text(candidate_level)} with rejection, structure-shift, or momentum evidence."
        )
    elif setup_type == "LIQUIDITY_SWEEP":
        confirmation_level = candidate_level
        confirmation_rule = (
            f"Sweep the prior boundary at {price_text(candidate_level)}, then close back "
            "inside the range with the strategy's penetration and close-position checks."
        )
    elif setup_type == "TREND_PULLBACK":
        confirmation_rule = (
            f"Retest {price_text(candidate_level)} within tolerance, then show a "
            "direction-aligned rejection, momentum recovery, or refinement shift; "
            "there is no single fixed confirmation price."
        )
    elif setup_type == "SUPPORT_RESISTANCE_REACTION":
        confirmation_rule = (
            f"Test the repeated zone at {price_text(candidate_level)} and close with a "
            "directional rejection; there is no separate fixed breakout price."
        )
    else:
        confirmation_rule = (
            "The selected setup uses a completed-candle pattern rather than a "
            "fixed numeric confirmation threshold."
        )

    rejections = list(rejection_reasons or [])
    if action in ("BUY", "SELL"):
        entry_status = f"ENTRY CONFIRMED — {action}"
        gate_step = (
            "Weighted context, score, and risk gates passed; no broker order is placed."
        )
        next_action = (
            "Strategy conditions are confirmed. This analysis does not place a broker order."
        )
    elif str(category).upper() == "EARLY":
        entry_status = f"DEVELOPING — {direction}, NOT CONFIRMED"
        gate_step = (
            "Still WAIT: EARLY is provisional; MODERATE/STRONG and validation are required."
        )
        next_action = (
            "Wait for MODERATE/STRONG and all selected-mode validation gates; do not enter yet."
        )
    else:
        entry_status = f"WAIT — {direction} setup has not passed final gates"
        gate_step = (
            "Still WAIT: "
            + (
                str(rejections[0])[:150]
                if rejections
                else "selected-mode context, score, and risk checks are incomplete."
            )
        )
        next_action = (
            str(rejections[0])[:180]
            if rejections
            else "Wait for the selected-mode context, score, and risk checks; do not enter."
        )

    side_word = "above" if direction == "BUY" else "below"
    if setup_type == "BREAKOUT":
        steps = [
            (
                f"1. Breakout: completed close {side_word} "
                f"{price_text(confirmation_level)} (detected)."
            ),
            (
                f"2. Retest: {price_text(retest_level)} ± {price_text(retest_tolerance)} "
                "is optional for this contained-breakout path."
            ),
            f"3. Final entry gate: {gate_step}",
        ]
    elif setup_type == "BREAKOUT_RETEST":
        steps = [
            (
                f"1. Breakout: completed close {side_word} "
                f"{price_text(breakout_level + breakout_buffer if direction == 'BUY' else breakout_level - breakout_buffer)} "
                "(detected)."
            ),
            (
                f"2. Retest: tested {price_text(retest_level)} within "
                f"±{price_text(retest_tolerance)} and held (detected)."
            ),
            (
                f"3. Confirmation: close {side_word} {price_text(confirmation_level)} "
                f"with candle recovery/rejection; {gate_step}"
            ),
        ]
    elif setup_type in ("FAILED_BREAKOUT", "LIQUIDITY_SWEEP"):
        steps = [
            (
                f"1. Price swept/broke the boundary at {price_text(breakout_level)} "
                "(detected)."
            ),
            (
                f"2. Completed close returned across {price_text(confirmation_level)} "
                "into the range (detected)."
            ),
            f"3. Reversal evidence: {confirmation_rule} {gate_step}",
        ]
    elif setup_type == "TREND_PULLBACK":
        steps = [
            (
                f"1. Pullback tested {price_text(retest_level)} within "
                f"±{price_text(retest_tolerance)} (detected)."
            ),
            f"2. Confirmation: {confirmation_rule}",
            f"3. Final entry gate: {gate_step}",
        ]
    elif setup_type == "SUPPORT_RESISTANCE_REACTION":
        steps = [
            (
                f"1. Price tested the zone at {price_text(retest_level)} within "
                f"±{price_text(retest_tolerance)} (detected)."
            ),
            f"2. Confirmation: {confirmation_rule}",
            f"3. Final entry gate: {gate_step}",
        ]
    else:
        steps = [
            f"1. Setup level: {price_text(retest_level)}.",
            f"2. Confirmation: {confirmation_rule}",
            f"3. Final entry gate: {gate_step}",
        ]

    result.update(
        direction=direction,
        setup_type=setup_type,
        breakout_level=breakout_level,
        retest_level=retest_level,
        retest_tolerance=retest_tolerance,
        confirmation_level=confirmation_level,
        confirmation_rule=confirmation_rule,
        breakout_required=setup_type in breakout_setups,
        steps=steps,
        entry_status=entry_status,
        next_action=next_action,
    )
    return result


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
        and (
            candidate.price_action_strength > 0
            or breakdown.get("momentum_trend", 0) > 0
        )
    ):
        return "EARLY"
    return "WAIT"


def analyze_weighted_market(
    *,
    config: ResolvedAnalysisConfig,
    raw_data: Mapping[str, object],
    spread: float | None,
    now: float | None = None,
    event_risk: dict | None = None,
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
            atr_period=config.profile.atr_period,
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
    opposing_direction = "SELL" if best_direction == "BUY" else "BUY"
    if (
        chosen is not None
        and scores[opposing_direction]["candidate"] is not None
        and abs(scores[best_direction]["score"] - other_score) < 5
    ):
        chosen = None
        directional_rejections.append(
            "Credible BUY and SELL setups are both present and their evidence is too close."
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
    event_risk = event_risk or {
        "status": "UNAVAILABLE_NO_CALENDAR_FEED",
        "blocks_signal": False,
        "source": "unavailable",
        "event": None,
        "reason": "No economic-calendar feed or operator event schedule is configured.",
    }
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
    if event_risk.get("blocks_signal"):
        category = "WAIT"
        rejection_reasons.append(
            str(event_risk.get("reason") or "Configured event-risk filter blocks this setup.")
        )

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

    plan = (
        validation
        if validation.get("valid") and not event_risk.get("blocks_signal")
        else {}
    )
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
    entry_confirmation_levels = _build_entry_confirmation_levels(
        candidate=chosen,
        setup_data=setup_data,
        price=price,
        atr=atr_value,
        profile=config.profile,
        breakout_lookback=config.breakout_lookback,
        category=category,
        action=action,
        rejection_reasons=rejection_reasons,
        data_is_valid=required_data_valid,
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
        "stop_widened_to_atr_floor": plan.get(
            "stop_widened_to_atr_floor", False
        ),
        "stop_distance_atr": plan.get("stop_distance_atr"),
        "take_profit": plan.get("take_profit", 0.0),
        "tp2": plan.get("tp2"),
        "tp3": plan.get("tp3"),
        "rr": plan.get("rr", 0.0),
        "target_rrs": plan.get("target_rrs", {}),
        "reference_spread_target_rrs": plan.get(
            "reference_spread_target_rrs", {}
        ),
        "execution_cost_target_rrs": plan.get(
            "execution_cost_target_rrs", {}
        ),
        "execution_cost_status": plan.get(
            "execution_cost_status", "unavailable"
        ),
        "reference_spread_rr": plan.get("reference_spread_rr"),
        "minimum_target_spacing": plan.get("minimum_target_spacing"),
        "spread_status": validation.get("spread_status", "unavailable"),
        "spread_atr": validation.get("spread_atr"),
        "spread": spread,
        "event_risk": event_risk,
        "preferred_rr": config.preferred_rr,
        "minimum_rr": float(config.profile.minimum_target_rr),
        "stop_buffer_atr": float(config.profile.stop_buffer_atr),
        "atr_period": int(config.profile.atr_period),
        "minimum_stop_atr": float(config.profile.minimum_stop_distance_atr),
        "maximum_stop_atr": float(config.profile.maximum_stop_distance_atr),
        "maximum_ema_distance_atr": float(config.profile.maximum_ema_distance_atr),
        "maximum_live_entry_distance_atr": float(
            config.profile.maximum_live_entry_distance_atr
        ),
        "structure_lookback": int(config.profile.structure_lookback_candles),
        "breakout_lookback": config.breakout_lookback,
        "sweep_lookback": config.sweep_lookback,
        "atr_expansion_limit": config.atr_expansion_limit,
        "maximum_spread_atr": float(config.profile.maximum_spread_atr),
        "commission_rate": config.profile.commission_rate,
        "slippage_per_oz": config.profile.slippage_per_oz,
        "expected_holding_bars": int(config.profile.expected_holding_bars),
        "level_rationale": plan.get("level_rationale", {}),
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
        "entry_confirmation_levels": entry_confirmation_levels,
        "execution_note": execution_note,
        "major_conflict": major_conflict,
    }

