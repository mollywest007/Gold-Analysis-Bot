"""Mode-specific settings and timeframe resolution for the weighted engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.analysis.modes import ModeConfig, resolve_momentum_pullback_profile


TIMEFRAME_ORDER = (
    "M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1",
)
TIMEFRAME_RANK = {timeframe: index for index, timeframe in enumerate(TIMEFRAME_ORDER)}


@dataclass(frozen=True)
class ResolvedAnalysisConfig:
    mode: str
    context_timeframe: str
    setup_timeframe: str
    refinement_timeframe: str
    context_weight: float
    setup_weight: float
    refinement_weight: float
    early_score: int
    moderate_score: int
    strong_score: int
    atr_expansion_limit: float
    max_candle_age_bars: float
    preferred_rr: float
    score_weights: dict[str, float]
    breakout_lookback: int
    sweep_lookback: int
    profile: Any

    @property
    def timeframes(self) -> tuple[str, str, str]:
        return (
            self.context_timeframe,
            self.setup_timeframe,
            self.refinement_timeframe,
        )


def _shift_timeframe(timeframe: str, offset: int) -> str:
    rank = TIMEFRAME_RANK[timeframe]
    return TIMEFRAME_ORDER[min(max(rank + offset, 0), len(TIMEFRAME_ORDER) - 1)]


def resolve_analysis_config(
    mode_cfg: ModeConfig,
    setup_timeframe: str | None = None,
    *,
    allow_unlisted_timeframe: bool = False,
) -> ResolvedAnalysisConfig:
    """Resolve mode thresholds and role timeframes around the selected setup TF.

    The four mode defaults match the requested context/setup/refinement ladder.
    A user-selected setup timeframe remains authoritative; context and
    refinement shift by the same rank offsets as that mode's default ladder.
    """
    if mode_cfg.momentum_pullback is None:
        raise ValueError(
            f"Mode '{mode_cfg.name}' has no standalone analysis profile; "
            "resolve a combined mode to its alert stream first."
        )

    default_setup = mode_cfg.analysis_setup_timeframe or mode_cfg.preferred_timeframe
    context_base = mode_cfg.analysis_context_timeframe
    refinement_base = mode_cfg.analysis_refinement_timeframe
    if not context_base or not refinement_base:
        raise ValueError(f"Mode '{mode_cfg.name}' is missing timeframe-role settings.")
    if any(tf not in TIMEFRAME_RANK for tf in (default_setup, context_base, refinement_base)):
        raise ValueError(f"Mode '{mode_cfg.name}' contains an unsupported analysis timeframe.")

    selected_setup = setup_timeframe or default_setup
    if selected_setup not in TIMEFRAME_RANK:
        raise ValueError(f"Unsupported analysis timeframe: {selected_setup}")
    if (
        not allow_unlisted_timeframe
        and selected_setup not in mode_cfg.scan_timeframes
    ):
        raise ValueError(
            f"{selected_setup} is not selectable in {mode_cfg.label} mode."
        )

    context_offset = TIMEFRAME_RANK[context_base] - TIMEFRAME_RANK[default_setup]
    refinement_offset = (
        TIMEFRAME_RANK[refinement_base] - TIMEFRAME_RANK[default_setup]
    )
    context_timeframe = _shift_timeframe(selected_setup, context_offset)
    refinement_timeframe = _shift_timeframe(selected_setup, refinement_offset)

    early_score = int(mode_cfg.analysis_early_score)
    moderate_score = int(mode_cfg.analysis_min_score)
    strong_score = int(mode_cfg.analysis_strong_score)
    if not 0 <= early_score < moderate_score < strong_score <= 100:
        raise ValueError(
            f"Mode '{mode_cfg.name}' has inconsistent early/moderate/strong score thresholds."
        )
    context_weight = float(mode_cfg.analysis_context_weight)
    setup_weight = float(mode_cfg.analysis_setup_weight)
    refinement_weight = float(mode_cfg.analysis_refinement_weight)
    # At the edges of a mode's selectable range, clamp the unavailable adjacent
    # role and transfer its share rather than counting the same candle set twice.
    if context_timeframe == selected_setup:
        setup_weight += context_weight
        context_weight = 0.0
    if refinement_timeframe == selected_setup:
        setup_weight += refinement_weight
        refinement_weight = 0.0
    if context_timeframe == refinement_timeframe:
        context_weight += refinement_weight
        refinement_weight = 0.0
    weights = (context_weight, setup_weight, refinement_weight)
    if any(weight < 0 for weight in weights) or abs(sum(weights) - 1.0) > 1e-6:
        raise ValueError(
            f"Mode '{mode_cfg.name}' timeframe weights must be non-negative and sum to 1."
        )

    score_weights = {
        str(key): float(value)
        for key, value in mode_cfg.analysis_score_weights.items()
    }
    expected_score_keys = {
        "market_structure",
        "entry_location",
        "price_action",
        "momentum_trend",
        "liquidity_breakout_retest",
        "volatility_execution",
    }
    if (
        set(score_weights) != expected_score_keys
        or any(value < 0 for value in score_weights.values())
        or abs(sum(score_weights.values()) - 100.0) > 1e-6
    ):
        raise ValueError(
            f"Mode '{mode_cfg.name}' score weights must cover all six categories "
            "and sum to 100."
        )

    profile = resolve_momentum_pullback_profile(mode_cfg, selected_setup)
    return ResolvedAnalysisConfig(
        mode=mode_cfg.name,
        context_timeframe=context_timeframe,
        setup_timeframe=selected_setup,
        refinement_timeframe=refinement_timeframe,
        context_weight=weights[0],
        setup_weight=weights[1],
        refinement_weight=weights[2],
        early_score=early_score,
        moderate_score=moderate_score,
        strong_score=strong_score,
        atr_expansion_limit=float(mode_cfg.analysis_atr_expansion_limit),
        max_candle_age_bars=float(mode_cfg.analysis_max_candle_age_bars),
        preferred_rr=float(profile.preferred_target_rr),
        score_weights=score_weights,
        breakout_lookback=max(5, int(mode_cfg.breakout_lookback)),
        sweep_lookback=max(2, int(mode_cfg.liquidity_lookback)),
        profile=profile,
    )

