"""Editable per-mode strategy parameters for the adaptive XAU/USD engine."""

from dataclasses import dataclass


TIMEFRAMES = ("M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")
_RANK = {timeframe: index for index, timeframe in enumerate(TIMEFRAMES)}


@dataclass(frozen=True)
class StrategyProfile:
    context_timeframe: str
    setup_timeframe: str
    refinement_timeframe: str
    score_threshold: int
    early_threshold: int
    strong_threshold: int
    minimum_rr: float
    preferred_rr: float
    stop_buffer_atr: float
    minimum_stop_atr: float
    maximum_stop_atr: float
    maximum_extension_atr: float
    maximum_atr_fraction: float
    maximum_spread_atr: float
    structure_lookback: int
    pivot_radius: int
    minimum_score_margin: int
    weights: tuple[int, int, int, int, int, int]


PROFILES = {
    "scalp": StrategyProfile("M15", "M5", "M1", 55, 42, 75, 1.3, 1.5, .12, .25, 1.8, 1.7, .006, .12, 40, 1, 7, (25, 20, 15, 15, 15, 10)),
    "intraday": StrategyProfile("H1", "M15", "M5", 58, 46, 78, 1.5, 1.8, .14, .35, 2.1, 1.8, .012, .18, 56, 2, 8, (25, 20, 15, 15, 15, 10)),
    "swing": StrategyProfile("D1", "H4", "H1", 62, 52, 82, 2.0, 2.4, .18, .45, 2.6, 2.2, .025, .25, 72, 2, 9, (25, 20, 15, 15, 15, 10)),
    "position": StrategyProfile("W1", "D1", "H4", 65, 55, 85, 2.0, 3.0, .22, .60, 3.0, 2.6, .05, .30, 96, 3, 10, (25, 20, 15, 15, 15, 10)),
}


def resolve_timeframes(mode: str, selected_setup: str | None = None) -> dict[str, str | None]:
    """Use the configured role triplet, adapting it when the UI selects another chart."""
    profile = PROFILES[mode]
    setup = selected_setup or profile.setup_timeframe
    if setup not in _RANK or setup == profile.setup_timeframe:
        return {
            "context": profile.context_timeframe,
            "setup": setup,
            "refinement": profile.refinement_timeframe,
        }
    rank = _RANK[setup]
    context_offset = _RANK[profile.context_timeframe] - _RANK[profile.setup_timeframe]
    refinement_offset = _RANK[profile.refinement_timeframe] - _RANK[profile.setup_timeframe]
    context_rank = max(_RANK[profile.context_timeframe], rank + context_offset)
    refinement_rank = rank + refinement_offset
    return {
        "context": TIMEFRAMES[context_rank] if context_rank < len(TIMEFRAMES) and context_rank != rank else None,
        "setup": setup,
        "refinement": TIMEFRAMES[refinement_rank] if 0 <= refinement_rank < len(TIMEFRAMES) and refinement_rank != rank else None,
    }
