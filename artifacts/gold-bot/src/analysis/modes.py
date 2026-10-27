"""
Analysis Modes — defines the trading personas the bot can adopt.

Each mode completely changes how the engine analyses its selected market:
  - which timeframes are scanned for auto-alerts
  - how tight/wide stops and targets are
  - how many selected-timeframe indicator votes are required before a signal fires
  - what confidence level is required

Adding a new mode:  add an entry to MODES dict with a ModeConfig object.
The rest of the system picks it up automatically.
"""

from dataclasses import dataclass, field, replace
import math
import os
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class MomentumPullbackProfile:
    """Entry and risk thresholds for one selected-timeframe pullback strategy."""

    swing_change_atr: float
    pullback_candles: int
    minimum_pullback_depth_atr: float
    maximum_pullback_depth_atr: float
    structure_hold_tolerance_atr: float
    level_tolerance_atr: float
    rejection_wick_min_atr: float
    rejection_wick_body_ratio: float
    chop_lookback_candles: int
    chop_minimum_range_atr: float
    chop_max_alternations: int
    chop_max_drift_atr: float
    impulse_lookback_candles: int
    maximum_breakout_range_atr: float
    maximum_breakout_body_atr: float
    maximum_ema_distance_atr: float
    buy_rsi_veto_below: float
    sell_rsi_veto_above: float
    stop_buffer_atr: float
    minimum_stop_distance_atr: float
    maximum_stop_distance_atr: float
    target_buffer_atr: float
    minimum_target_room_atr: float
    entry_zone_atr: float
    range_trap_lookback_candles: int
    range_trap_max_width_atr: float
    structure_pivot_radius: int = 1
    structure_lookback_candles: int = 48
    confirmation_break_atr: float = 0.03
    range_trap_minimum_touches: int = 2
    expected_holding_bars: int = 24
    minimum_target_rr: float = 1.5
    preferred_target_rr: float = 2.0
    atr_period: int = 14
    breakout_enabled: bool = True
    # Maximum validated reference-market spread relative to ATR14. These are
    # conservative policy caps, not values optimized from historical results.
    maximum_spread_atr: float = 0.20
    rejection_close_position: float = 0.58
    breakout_close_position: float = 0.68
    breakout_minimum_body_ratio: float = 0.35
    sweep_penetration_atr: float = 0.04
    # Last-mile guard between a validated plan and the current spot quote.
    maximum_live_entry_distance_atr: float = 0.35
    # Optional broker execution-cost estimates. None means the value is
    # unknown, not zero; configured values are used in the net R:R screen.
    commission_rate: float | None = None
    slippage_per_oz: float | None = None

    def __post_init__(self) -> None:
        """Reject incomplete or unsafe per-mode execution profiles at startup."""
        positive_fields = (
            "maximum_pullback_depth_atr",
            "level_tolerance_atr",
            "chop_minimum_range_atr",
            "maximum_breakout_range_atr",
            "maximum_breakout_body_atr",
            "maximum_ema_distance_atr",
            "minimum_stop_distance_atr",
            "maximum_stop_distance_atr",
            "minimum_target_room_atr",
            "range_trap_max_width_atr",
            "minimum_target_rr",
            "preferred_target_rr",
            "maximum_spread_atr",
            "maximum_live_entry_distance_atr",
        )
        nonnegative_fields = (
            "swing_change_atr",
            "minimum_pullback_depth_atr",
            "structure_hold_tolerance_atr",
            "rejection_wick_min_atr",
            "rejection_wick_body_ratio",
            "chop_max_drift_atr",
            "buy_rsi_veto_below",
            "sell_rsi_veto_above",
            "stop_buffer_atr",
            "target_buffer_atr",
            "entry_zone_atr",
            "confirmation_break_atr",
            "sweep_penetration_atr",
        )
        for name in (*positive_fields, *nonnegative_fields):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or (value <= 0 if name in positive_fields else value < 0)
            ):
                raise ValueError(
                    f"Momentum-pullback setting '{name}' must be "
                    f"{'positive' if name in positive_fields else 'non-negative'} and finite."
                )

        integer_fields = (
            "pullback_candles",
            "chop_lookback_candles",
            "impulse_lookback_candles",
            "range_trap_lookback_candles",
            "structure_pivot_radius",
            "structure_lookback_candles",
            "range_trap_minimum_touches",
            "expected_holding_bars",
            "atr_period",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"Momentum-pullback setting '{name}' must be a positive integer.")
        if not 2 <= self.atr_period <= 100:
            raise ValueError("Momentum-pullback ATR period must be between 2 and 100.")
        if type(self.chop_max_alternations) is not int or self.chop_max_alternations < 0:
            raise ValueError("chop_max_alternations must be a non-negative integer.")

        for name in (
            "buy_rsi_veto_below",
            "sell_rsi_veto_above",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"Momentum-pullback setting '{name}' must be between 0 and 100.")
        for name in (
            "rejection_close_position",
            "breakout_close_position",
            "breakout_minimum_body_ratio",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"Momentum-pullback setting '{name}' must be between 0 and 1.")
        if self.minimum_pullback_depth_atr > self.maximum_pullback_depth_atr:
            raise ValueError("Minimum pullback depth cannot exceed maximum depth.")
        if self.minimum_stop_distance_atr >= self.maximum_stop_distance_atr:
            raise ValueError("Maximum stop distance must exceed the minimum stop distance.")
        if self.preferred_target_rr < self.minimum_target_rr:
            raise ValueError("Preferred target R:R cannot be below the mode minimum.")
        if self.commission_rate is not None and (
            isinstance(self.commission_rate, bool)
            or not math.isfinite(float(self.commission_rate))
            or not 0 <= float(self.commission_rate) < 1
        ):
            raise ValueError("Commission rate must be finite and in [0, 1).")
        if self.slippage_per_oz is not None and (
            isinstance(self.slippage_per_oz, bool)
            or not math.isfinite(float(self.slippage_per_oz))
            or float(self.slippage_per_oz) < 0
        ):
            raise ValueError("Slippage per ounce must be finite and non-negative.")


@dataclass
class ModeConfig:
    # ── Identity ───────────────────────────────────────────────────────────────
    name:        str        # internal key  e.g. "scalp"
    label:       str        # display name  e.g. "Scalp"
    emoji:       str        # e.g. "⚡"
    description: str        # one-line summary shown in /mode

    # ── Alert scanning ─────────────────────────────────────────────────────────
    scan_timeframes: List[str]   # timeframes the background scanner watches
    preferred_timeframe: str     # default chart used by /signal and /recommend
    # Retained as metadata for older settings/API clients. It is not consulted
    # by the single-timeframe entry path.
    confirmation_map: Dict[str, str]
    # Retained as metadata for explicit diagnostic reports only.
    context_timeframes: List[str]

    # ── Signal sensitivity ─────────────────────────────────────────────────────
    min_votes:           int     # indicator votes needed outside kill zones
    min_votes_kill_zone: int     # votes needed during London/NY kill zones
    confidence_threshold: int    # min % before a signal is considered valid
    indicator_weights: Dict[str, float] = field(default_factory=dict)
    # Extra strategy knobs keep modes distinct beyond timeframe selection.
    volume_spike_threshold: float = 1.5
    breakout_lookback: int = 20
    liquidity_lookback: int = 15
    min_rr_ratio: float = 2.0
    # Directional evidence multipliers applied after the shared indicator vote.
    # These make the personas distinct without adding mode-specific branches
    # throughout the engine.
    feature_weights: Dict[str, float] = field(default_factory=dict)
    # Automatic alert quality gates. The engine handles directional gating;
    # these settings control which completed plans are worth notifying about.
    alert_min_win_probability: int = 62
    alert_min_grades: Tuple[str, ...] = ("A+", "A")
    confluence_min_tfs: int = 3
    # Mode-specific trade-plan policy.  These are deliberately explicit so a
    # new mode can tune the strategy without adding branches to the engine.
    risk_note: str = ""
    momentum_pullback: Optional[MomentumPullbackProfile] = None
    # Background entry-analysis cadence. Combined mode uses the interval
    # belonging to each independently resolved stream, not this coordinator.
    analysis_scan_interval_seconds: int = 15
    # Weighted multi-timeframe analysis defaults. The user's selected chart
    # timeframe remains the setup timeframe; context/refinement roles are
    # resolved relative to these mode defaults.
    analysis_context_timeframe: str = ""
    analysis_setup_timeframe: str = ""
    analysis_refinement_timeframe: str = ""
    analysis_min_score: int = 60
    analysis_early_score: int = 55
    analysis_strong_score: int = 82
    analysis_context_weight: float = 0.35
    analysis_setup_weight: float = 0.50
    analysis_refinement_weight: float = 0.15
    analysis_atr_expansion_limit: float = 2.25
    analysis_max_candle_age_bars: float = 3.0
    preferred_rr_ratio: float = 2.0
    analysis_score_weights: Dict[str, float] = field(default_factory=lambda: {
        "market_structure": 25.0,
        "entry_location": 20.0,
        "price_action": 15.0,
        "momentum_trend": 15.0,
        "liquidity_breakout_retest": 15.0,
        "volatility_execution": 10.0,
    })

    # ── Risk / reward ──────────────────────────────────────────────────────────
    # Legacy single-timeframe ATR multipliers. Normal live analysis uses the
    # weighted momentum_pullback profile's structural stop rules instead.
    sl_mult_override: Dict[str, float] = field(default_factory=dict)
    # TP distances as SL multiples: (TP1, TP2, TP3)
    tp_mult: Tuple[float, float, float] = (2.0, 3.5, 4.5)

    # ── Legacy compatibility ────────────────────────────────────────────────────
    # Kept for settings/API compatibility; single-timeframe entries never use it.
    htf_gate_required: bool = False

    # ── Trade type label injected into MarketAnalysis.trade_type ───────────────
    trade_type_label: str = "Intraday"

    # ── Tip shown after mode switch ────────────────────────────────────────────
    tip: str = ""

    def __post_init__(self) -> None:
        if not (
            0 <= self.analysis_early_score
            < self.analysis_min_score
            < self.analysis_strong_score
            <= 100
        ):
            raise ValueError(
                f"{self.name}: weighted score thresholds must satisfy "
                "0 <= early < moderate < strong <= 100."
            )
        if (
            not math.isfinite(float(self.min_rr_ratio))
            or not math.isfinite(float(self.preferred_rr_ratio))
            or self.min_rr_ratio <= 0
            or self.preferred_rr_ratio < self.min_rr_ratio
        ):
            raise ValueError(
                f"{self.name}: preferred R:R must be at least the positive minimum."
            )
        if not self.scan_timeframes or len(set(self.scan_timeframes)) != len(self.scan_timeframes):
            raise ValueError(f"{self.name}: scan timeframes must be non-empty and unique.")
        if self.preferred_timeframe not in self.scan_timeframes:
            raise ValueError(f"{self.name}: preferred timeframe must be selectable.")
        known_timeframes = {
            "M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"
        }
        if any(timeframe not in known_timeframes for timeframe in self.scan_timeframes):
            raise ValueError(f"{self.name}: scan timeframe is unsupported.")

        positive_settings = {
            "volume_spike_threshold": self.volume_spike_threshold,
            "analysis_atr_expansion_limit": self.analysis_atr_expansion_limit,
            "analysis_max_candle_age_bars": self.analysis_max_candle_age_bars,
            "analysis_scan_interval_seconds": self.analysis_scan_interval_seconds,
            "breakout_lookback": self.breakout_lookback,
            "liquidity_lookback": self.liquidity_lookback,
            "min_votes": self.min_votes,
            "min_votes_kill_zone": self.min_votes_kill_zone,
            "confluence_min_tfs": self.confluence_min_tfs,
        }
        for name, value in positive_settings.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{self.name}: {name} must be positive and finite.")
        for name, value in (
            ("analysis_scan_interval_seconds", self.analysis_scan_interval_seconds),
            ("breakout_lookback", self.breakout_lookback),
            ("liquidity_lookback", self.liquidity_lookback),
            ("min_votes", self.min_votes),
            ("min_votes_kill_zone", self.min_votes_kill_zone),
            ("confluence_min_tfs", self.confluence_min_tfs),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{self.name}: {name} must be a positive integer.")
        for name, value in (
            ("confidence_threshold", self.confidence_threshold),
            ("alert_min_win_probability", self.alert_min_win_probability),
        ):
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError(f"{self.name}: {name} must be an integer from 0 to 100.")

        timeframe_weights = (
            self.analysis_context_weight,
            self.analysis_setup_weight,
            self.analysis_refinement_weight,
        )
        if (
            any(not math.isfinite(float(weight)) or weight < 0 for weight in timeframe_weights)
            or abs(sum(timeframe_weights) - 1.0) > 1e-6
        ):
            raise ValueError(f"{self.name}: timeframe weights must be non-negative and sum to 1.")
        if self.analysis_score_weights:
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
                for value in self.analysis_score_weights.values()
            ):
                raise ValueError(f"{self.name}: score weights must be non-negative.")
            if abs(sum(self.analysis_score_weights.values()) - 100.0) > 1e-6:
                raise ValueError(f"{self.name}: score weights must sum to 100.")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            for value in (*self.feature_weights.values(), *self.indicator_weights.values())
        ):
            raise ValueError(f"{self.name}: confluence weights must be non-negative and finite.")
        if self.indicator_weights and abs(sum(self.indicator_weights.values()) - 1.0) > 1e-6:
            raise ValueError(f"{self.name}: indicator weights must sum to 1.")
        expected_score_keys = {
            "market_structure",
            "entry_location",
            "price_action",
            "momentum_trend",
            "liquidity_breakout_retest",
            "volatility_execution",
        }
        if self.analysis_score_weights and set(self.analysis_score_weights) != expected_score_keys:
            raise ValueError(f"{self.name}: weighted score groups are incomplete or unsupported.")
        if len(self.tp_mult) != 3 or any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in self.tp_mult
        ) or not self.tp_mult[0] < self.tp_mult[1] < self.tp_mult[2]:
            raise ValueError(f"{self.name}: target multipliers must be positive and increasing.")
        if any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in self.sl_mult_override.values()
        ):
            raise ValueError(f"{self.name}: stop multipliers must be positive and finite.")
        if self.momentum_pullback is not None:
            self.momentum_pullback = replace(
                self.momentum_pullback,
                minimum_target_rr=self.min_rr_ratio,
                preferred_target_rr=self.preferred_rr_ratio,
            )
        role_timeframes = (
            self.analysis_context_timeframe,
            self.analysis_setup_timeframe,
            self.analysis_refinement_timeframe,
        )
        if any(role_timeframes) and any(
            timeframe not in known_timeframes for timeframe in role_timeframes
        ):
            raise ValueError(f"{self.name}: analysis timeframe role is unsupported.")


MOMENTUM_PULLBACK_PROFILES: Dict[str, MomentumPullbackProfile] = {
    "scalp": MomentumPullbackProfile(
        swing_change_atr=0.015,
        pullback_candles=2,
        minimum_pullback_depth_atr=0.03,
        maximum_pullback_depth_atr=0.95,
        structure_hold_tolerance_atr=0.08,
        level_tolerance_atr=0.65,
        rejection_wick_min_atr=0.04,
        rejection_wick_body_ratio=0.18,
        chop_lookback_candles=6,
        chop_minimum_range_atr=0.95,
        chop_max_alternations=4,
        chop_max_drift_atr=0.75,
        impulse_lookback_candles=4,
        maximum_breakout_range_atr=1.90,
        maximum_breakout_body_atr=1.20,
        maximum_ema_distance_atr=1.75,
        buy_rsi_veto_below=40.0,
        sell_rsi_veto_above=60.0,
        stop_buffer_atr=0.10,
        minimum_stop_distance_atr=1.00,
        maximum_stop_distance_atr=1.75,
        target_buffer_atr=0.08,
        minimum_target_room_atr=0.60,
        entry_zone_atr=0.08,
        range_trap_lookback_candles=16,
        range_trap_max_width_atr=2.25,
        maximum_spread_atr=0.12,
        minimum_target_rr=1.5,
        preferred_target_rr=2.0,
    ),
    "intraday": MomentumPullbackProfile(
        swing_change_atr=0.03,
        pullback_candles=3,
        minimum_pullback_depth_atr=0.08,
        maximum_pullback_depth_atr=1.15,
        structure_hold_tolerance_atr=0.10,
        level_tolerance_atr=0.55,
        rejection_wick_min_atr=0.08,
        rejection_wick_body_ratio=0.25,
        chop_lookback_candles=8,
        chop_minimum_range_atr=1.15,
        chop_max_alternations=5,
        chop_max_drift_atr=0.65,
        impulse_lookback_candles=7,
        maximum_breakout_range_atr=1.65,
        maximum_breakout_body_atr=1.15,
        maximum_ema_distance_atr=1.60,
        buy_rsi_veto_below=44.0,
        sell_rsi_veto_above=56.0,
        stop_buffer_atr=0.15,
        minimum_stop_distance_atr=1.10,
        maximum_stop_distance_atr=2.00,
        target_buffer_atr=0.10,
        minimum_target_room_atr=0.75,
        entry_zone_atr=0.12,
        range_trap_lookback_candles=20,
        range_trap_max_width_atr=2.50,
        maximum_spread_atr=0.18,
        minimum_target_rr=1.5,
        preferred_target_rr=2.0,
    ),
    "swing": MomentumPullbackProfile(
        swing_change_atr=0.05,
        pullback_candles=3,
        minimum_pullback_depth_atr=0.10,
        maximum_pullback_depth_atr=1.25,
        structure_hold_tolerance_atr=0.12,
        level_tolerance_atr=0.50,
        rejection_wick_min_atr=0.10,
        rejection_wick_body_ratio=0.30,
        chop_lookback_candles=10,
        chop_minimum_range_atr=0.95,
        chop_max_alternations=5,
        chop_max_drift_atr=0.75,
        impulse_lookback_candles=10,
        maximum_breakout_range_atr=1.90,
        maximum_breakout_body_atr=1.40,
        maximum_ema_distance_atr=1.90,
        buy_rsi_veto_below=46.0,
        sell_rsi_veto_above=54.0,
        stop_buffer_atr=0.20,
        minimum_stop_distance_atr=1.25,
        maximum_stop_distance_atr=2.25,
        target_buffer_atr=0.12,
        minimum_target_room_atr=0.90,
        entry_zone_atr=0.16,
        range_trap_lookback_candles=24,
        range_trap_max_width_atr=2.75,
        maximum_spread_atr=0.25,
        minimum_target_rr=2.0,
        preferred_target_rr=2.0,
    ),
    "position": MomentumPullbackProfile(
        swing_change_atr=0.07,
        pullback_candles=4,
        minimum_pullback_depth_atr=0.12,
        maximum_pullback_depth_atr=1.35,
        structure_hold_tolerance_atr=0.15,
        level_tolerance_atr=0.45,
        rejection_wick_min_atr=0.12,
        rejection_wick_body_ratio=0.35,
        chop_lookback_candles=12,
        chop_minimum_range_atr=0.85,
        chop_max_alternations=4,
        chop_max_drift_atr=0.85,
        impulse_lookback_candles=12,
        maximum_breakout_range_atr=2.10,
        maximum_breakout_body_atr=1.50,
        maximum_ema_distance_atr=2.20,
        buy_rsi_veto_below=48.0,
        sell_rsi_veto_above=52.0,
        stop_buffer_atr=0.25,
        minimum_stop_distance_atr=1.50,
        maximum_stop_distance_atr=2.50,
        target_buffer_atr=0.15,
        minimum_target_room_atr=1.00,
        entry_zone_atr=0.20,
        range_trap_lookback_candles=30,
        range_trap_max_width_atr=3.00,
        maximum_spread_atr=0.30,
        minimum_target_rr=2.0,
        preferred_target_rr=3.0,
    ),
}


_TIMEFRAME_RANK = {
    "M1": 0,
    "M3": 0,
    "M5": 0,
    "M15": 1,
    "M30": 2,
    "H1": 3,
    "H4": 4,
    "D1": 5,
    "W1": 6,
    "MN1": 7,
}

_TIMEFRAME_PULLBACK_BARS = {
    "scalp": (2, 3, 3, 4, 5, 5, 6, 6),
    "intraday": (3, 3, 3, 4, 4, 5, 6, 6),
    "swing": (3, 3, 3, 3, 4, 5, 6, 7),
    "position": (4, 4, 4, 4, 4, 5, 6, 7),
}

_MODE_STRUCTURE_BASE = {
    "scalp": 24,
    "intraday": 32,
    "swing": 40,
    "position": 48,
}

_MODE_PIVOT_ANCHOR = {
    "scalp": 2,
    "intraday": 2,
    "swing": 3,
    "position": 4,
}

_MODE_TIMEFRAME_ANCHOR = {
    "scalp": 1,    # M15 is the normal scalp chart
    "intraday": 3, # H1
    "swing": 4,    # H4
    "position": 5, # D1
}

_MODE_HOLDING_BARS = {
    "scalp": (60, 40, 30, 16, 12, 8, 6, 4, 2, 2),
    "intraday": (120, 90, 60, 32, 24, 16, 10, 7, 5, 3),
    "swing": (180, 120, 96, 48, 36, 24, 24, 18, 12, 8),
    "position": (240, 180, 120, 72, 48, 36, 30, 24, 18, 12),
}


def resolve_momentum_pullback_profile(
    mode_cfg: "ModeConfig",
    timeframe: str,
) -> MomentumPullbackProfile:
    """Scale the existing mode profile to the selected chart's structure."""
    profile = mode_cfg.momentum_pullback
    if profile is None:
        raise ValueError(
            f"Mode '{getattr(mode_cfg, 'name', 'unknown')}' has no standalone "
            "momentum-pullback profile; resolve a combined mode to its stream first."
        )
    if timeframe not in _TIMEFRAME_RANK:
        raise ValueError(f"Unsupported momentum-pullback timeframe: {timeframe}")

    mode = mode_cfg.name
    rank = _TIMEFRAME_RANK[timeframe]
    baseline = _MODE_TIMEFRAME_ANCHOR.get(mode, 3)
    broader_steps = max(0, rank - baseline)
    pivot_steps = max(0, rank - _MODE_PIVOT_ANCHOR.get(mode, 2))

    # Scalp stays responsive on lower charts. Larger charts use broader,
    # confirmed pivots rather than treating every one-bar fluctuation as a swing.
    pivot_radius = min(3, 1 + (pivot_steps + 1) // 2)
    structure_lookback = (
        _MODE_STRUCTURE_BASE.get(mode, 32) + rank * 8
    )
    pullback_bars = _TIMEFRAME_PULLBACK_BARS.get(
        mode, _TIMEFRAME_PULLBACK_BARS["intraday"]
    )[rank]

    # All distances remain ATR-relative. Broader charts get more room for normal
    # retracements and structural stops, but never exceed the global 2.5 ATR cap.
    resolved = replace(
        profile,
        pullback_candles=pullback_bars,
        minimum_pullback_depth_atr=(
            profile.minimum_pullback_depth_atr + broader_steps * 0.01
        ),
        maximum_pullback_depth_atr=(
            profile.maximum_pullback_depth_atr
            + rank * 0.025
            + broader_steps * 0.08
        ),
        swing_change_atr=profile.swing_change_atr + pivot_steps * 0.01,
        rejection_wick_min_atr=(
            profile.rejection_wick_min_atr + pivot_steps * 0.01
        ),
        rejection_wick_body_ratio=(
            profile.rejection_wick_body_ratio + pivot_steps * 0.02
        ),
        chop_lookback_candles=(
            profile.chop_lookback_candles + broader_steps * 2 + rank // 2
        ),
        chop_minimum_range_atr=max(
            0.35, profile.chop_minimum_range_atr - rank * 0.08
        ),
        chop_max_alternations=(
            profile.chop_max_alternations + rank // 3
        ),
        chop_max_drift_atr=max(
            0.25, profile.chop_max_drift_atr - rank * 0.04
        ),
        stop_buffer_atr=(
            profile.stop_buffer_atr + rank * 0.005 + broader_steps * 0.01
        ),
        minimum_stop_distance_atr=(
            profile.minimum_stop_distance_atr
            + rank * 0.015
            + broader_steps * 0.025
        ),
        maximum_stop_distance_atr=min(
            2.5,
            profile.maximum_stop_distance_atr
            + rank * 0.04
            + broader_steps * 0.08,
        ),
        target_buffer_atr=(
            profile.target_buffer_atr + rank * 0.005 + broader_steps * 0.01
        ),
        minimum_target_room_atr=(
            profile.minimum_target_room_atr
            + rank * 0.02
            + broader_steps * 0.06
        ),
        maximum_ema_distance_atr=(
            profile.maximum_ema_distance_atr + broader_steps * 0.15
        ),
        maximum_breakout_range_atr=max(
            1.35,
            profile.maximum_breakout_range_atr
            - (0.35 if mode == "scalp" and rank == 0 else 0.20 if rank <= 1 else 0.0)
            + broader_steps * 0.05,
        ),
        maximum_breakout_body_atr=max(
            0.8,
            profile.maximum_breakout_body_atr
            - (0.20 if mode == "scalp" and rank == 0 else 0.10 if rank <= 1 else 0.0),
        ),
        # RSI is a soft momentum read. Only a clear conflict with price structure
        # blocks a setup; neutral readings and a missing 50-line cross do not.
        buy_rsi_veto_below=35.0,
        sell_rsi_veto_above=65.0,
        structure_pivot_radius=pivot_radius,
        structure_lookback_candles=structure_lookback,
        range_trap_lookback_candles=(
            profile.range_trap_lookback_candles + rank * 2
        ),
        range_trap_max_width_atr=max(
            1.5, profile.range_trap_max_width_atr - rank * 0.10
        ),
        range_trap_minimum_touches=2 + rank // 4,
        expected_holding_bars=_MODE_HOLDING_BARS.get(
            mode, _MODE_HOLDING_BARS["intraday"]
        )[rank],
        minimum_target_rr=max(1.5, mode_cfg.min_rr_ratio, profile.minimum_target_rr),
        preferred_target_rr=max(
            mode_cfg.preferred_rr_ratio, profile.preferred_target_rr
        ),
        atr_period=profile.atr_period,
        confirmation_break_atr=(
            {"scalp": 0.005, "intraday": 0.02, "swing": 0.05, "position": 0.08}
            .get(mode, 0.02)
            + rank * 0.005
            + pivot_steps * 0.015
        ),
    )
    # Optional mode-specific overrides let an operator tune risk policy without
    # editing strategy code. Timeframe scaling is applied first so these values
    # describe the final profile used by the selected chart.
    prefix = f"XAU_{mode.upper()}_"
    numeric_overrides = {
        "stop_buffer_atr": "STOP_BUFFER_ATR",
        "minimum_stop_distance_atr": "MINIMUM_STOP_ATR",
        "maximum_stop_distance_atr": "MAXIMUM_STOP_ATR",
        "minimum_target_rr": "MINIMUM_TARGET_RR",
        "preferred_target_rr": "PREFERRED_TARGET_RR",
    }
    overrides = {}
    for field_name, env_suffix in numeric_overrides.items():
        raw = os.getenv(prefix + env_suffix)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix + env_suffix} must be a number.") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{prefix + env_suffix} must be positive and finite.")
        if field_name == "minimum_target_rr":
            mode_floor = max(
                1.5,
                float(mode_cfg.min_rr_ratio),
                float(profile.minimum_target_rr),
            )
            if value < mode_floor:
                raise ValueError(
                    f"{prefix + env_suffix} must be at least this mode's "
                    f"minimum target R:R of {mode_floor:g}."
                )
        overrides[field_name] = value
    raw_period = os.getenv(prefix + "ATR_PERIOD")
    if raw_period is not None:
        try:
            period = int(raw_period)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}ATR_PERIOD must be an integer.") from exc
        if str(period) != raw_period.strip() or period < 2 or period > 100:
            raise ValueError(f"{prefix}ATR_PERIOD must be an integer from 2 to 100.")
        overrides["atr_period"] = period
    for field_name, env_suffix in (
        ("commission_rate", "COMMISSION_RATE"),
        ("slippage_per_oz", "SLIPPAGE_PER_OZ"),
    ):
        raw_value = os.getenv(prefix + env_suffix)
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix + env_suffix} must be a number.") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{prefix + env_suffix} must be finite and non-negative.")
        if field_name == "commission_rate" and value >= 1:
            raise ValueError(f"{prefix + env_suffix} must be less than 1.")
        overrides[field_name] = value
    if overrides:
        resolved = replace(resolved, **overrides)
    return resolved


# ─── Mode definitions ─────────────────────────────────────────────────────────

MODES: Dict[str, ModeConfig] = {

    "scalp": ModeConfig(
        name        = "scalp",
        label       = "Scalp",
        emoji       = "⚡",
        description = "Quick entries from M1 through H1, scaled to the selected chart.",
        scan_timeframes = ["M1", "M3", "M5", "M15", "M30", "H1"],
        # M15 is the stable default for a new Scalp session. M1/M3/M5 remain
        # available for faster execution; M30/H1 use broader scaled profiles.
        preferred_timeframe = "M5",
        confirmation_map = {
            "M1": "M15", "M3": "M15", "M5": "M30", "M15": "H1",
            "M30": "H1", "H1": "H4",
        },
        context_timeframes = ["M15", "M30", "H1", "H4"],
        min_votes           = 2,   # fire faster — scalp windows close quickly
        min_votes_kill_zone = 2,
        confidence_threshold = 70,
        volume_spike_threshold = 1.25,
        breakout_lookback = 8,
        liquidity_lookback = 8,
        min_rr_ratio = 1.5,
        feature_weights = {
            "breakout": 0.12, "liquidity_sweep": 0.12, "volume_spike": 0.08,
            "momentum_shift": 0.10, "trend_regime": 0.06, "macro_alignment": 0.0,
        },
        alert_min_win_probability = 55,
        alert_min_grades = ("A+", "A", "B"),
        confluence_min_tfs = 3,
        indicator_weights = {
            "RSI(14)": 0.08, "MACD": 0.18, "EMA Stack": 0.20,
            "ADX DI": 0.22, "CCI(20)": 0.12, "BB %B": 0.12,
            "Williams%R": 0.05, "Supertrend": 0.03,
        },
        sl_mult_override = {
            "M1":  1.1,
            "M3":  1.2,
            "M5":  1.3,   # very tight — scalpers cut losses fast
            # M15 is the default scalp chart. Keep the stop responsive to the
            # setup instead of inheriting an intraday-sized risk distance.
            "M15": 1.25,
        },
        tp_mult = (1.5, 2.5, 3.5),   # quick, realistic scalping targets
        htf_gate_required = False,     # ignore macro trend; trade the micro move
        trade_type_label = "Scalp",
        tip = (
            "⚡ <b>Scalp Mode active.</b>\n"
            "Signals fire from M1 through H1. M30/H1 use broader structure "
            "than the lower-timeframe scalp rules."
        ),
        risk_note = "Volatility-buffered structural stop; 1.5R minimum, preferring 2R when structure permits.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["scalp"],
        analysis_scan_interval_seconds = 5,
        analysis_context_timeframe = "M15",
        analysis_setup_timeframe = "M5",
        analysis_refinement_timeframe = "M1",
        analysis_min_score = 55,
        analysis_early_score = 49,
        analysis_strong_score = 78,
        analysis_context_weight = 0.25,
        analysis_setup_weight = 0.55,
        analysis_refinement_weight = 0.20,
        analysis_atr_expansion_limit = 2.00,
        analysis_max_candle_age_bars = 2.0,
        preferred_rr_ratio = 2.0,
    ),

    "intraday": ModeConfig(
        name        = "intraday",
        label       = "Intraday",
        emoji       = "📊",
        description = "Day-trade setups on M15/M30/H1. Balanced risk, same-day exits.",
        scan_timeframes = ["M15", "M30", "H1"],
        preferred_timeframe = "M15",
        confirmation_map = {"M15": "H1", "M30": "H1", "H1": "H4"},
        context_timeframes = ["H1", "H4", "D1"],
        min_votes           = 4,   # current engine default
        min_votes_kill_zone = 3,
        confidence_threshold = 75,
        volume_spike_threshold = 1.5,
        breakout_lookback = 20,
        liquidity_lookback = 15,
        min_rr_ratio = 1.5,
        feature_weights = {
            "breakout": 0.08, "liquidity_sweep": 0.06, "volume_spike": 0.06,
            "momentum_shift": 0.05, "trend_regime": 0.08, "macro_alignment": 0.0,
        },
        alert_min_win_probability = 62,
        alert_min_grades = ("A+", "A"),
        confluence_min_tfs = 3,
        indicator_weights = {
            "RSI(14)": 0.14, "MACD": 0.19, "EMA Stack": 0.20,
            "ADX DI": 0.18, "CCI(20)": 0.11, "BB %B": 0.09,
            "Williams%R": 0.05, "Supertrend": 0.04,
        },
        sl_mult_override = {"M15": 1.5, "M30": 1.6, "H1": 1.8},
        tp_mult = (2.0, 3.5, 4.5),
        htf_gate_required = False,
        trade_type_label = "Intraday",
        tip = (
            "📊 <b>Intraday Mode active.</b>\n"
            "Scanning M15, M30 and H1. Standard risk management. "
            "Targets are realistic for same-session trades."
        ),
        risk_note = "Balanced intraday stop; TP1 needs 1.5R of clear structural room.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["intraday"],
        analysis_scan_interval_seconds = 15,
        analysis_context_timeframe = "H1",
        analysis_setup_timeframe = "M15",
        analysis_refinement_timeframe = "M5",
        analysis_min_score = 58,
        analysis_early_score = 52,
        analysis_strong_score = 81,
        analysis_context_weight = 0.35,
        analysis_setup_weight = 0.50,
        analysis_refinement_weight = 0.15,
        analysis_atr_expansion_limit = 2.10,
        analysis_max_candle_age_bars = 2.5,
        preferred_rr_ratio = 2.0,
    ),

    "swing": ModeConfig(
        name        = "swing",
        label       = "Swing",
        emoji       = "🌊",
        description = "Patient multi-day setups on H1/H4/D1 with broader structure.",
        scan_timeframes = ["H1", "H4", "D1"],
        preferred_timeframe = "H4",
        confirmation_map = {"H1": "H4", "H4": "D1", "D1": "W1"},
        context_timeframes = ["H1", "H4", "D1", "W1"],
        min_votes           = 4,
        min_votes_kill_zone = 4,   # kill zones less relevant on H4+
        confidence_threshold = 78,
        volume_spike_threshold = 1.6,
        breakout_lookback = 30,
        liquidity_lookback = 25,
        min_rr_ratio = 2.0,
        feature_weights = {
            "breakout": 0.04, "liquidity_sweep": 0.05, "volume_spike": 0.04,
            "momentum_shift": 0.02, "trend_regime": 0.12, "macro_alignment": 0.0,
        },
        alert_min_win_probability = 68,
        alert_min_grades = ("A+", "A"),
        confluence_min_tfs = 3,
        indicator_weights = {
            "RSI(14)": 0.08, "MACD": 0.20, "EMA Stack": 0.22,
            "ADX DI": 0.18, "CCI(20)": 0.10, "BB %B": 0.05,
            "Williams%R": 0.05, "Supertrend": 0.12,
        },
        sl_mult_override = {
            "H1": 1.8,
            "H4": 2.0,
            "D1": 2.2,
            "W1": 2.4,
        },
        tp_mult = (3.0, 5.0, 7.0),   # hold for the full move
        htf_gate_required = True,      # strong counter-trend D1 blocks H4 signals
        trade_type_label = "Swing",
        tip = (
            "🌊 <b>Swing Mode active.</b>\n"
            "Scanning H1, H4 and D1. Wider stops, larger targets. "
            "Expect fewer signals — only high-quality setups fire."
        ),
        risk_note = "Structural swing stop with room for higher-timeframe noise; minimum planned target is 2R.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["swing"],
        analysis_scan_interval_seconds = 60,
        analysis_context_timeframe = "D1",
        analysis_setup_timeframe = "H4",
        analysis_refinement_timeframe = "H1",
        analysis_min_score = 62,
        analysis_early_score = 56,
        analysis_strong_score = 84,
        analysis_context_weight = 0.45,
        analysis_setup_weight = 0.45,
        analysis_refinement_weight = 0.10,
        analysis_atr_expansion_limit = 2.25,
        analysis_max_candle_age_bars = 3.0,
        preferred_rr_ratio = 2.0,
    ),

    "position": ModeConfig(
        name        = "position",
        label       = "Position",
        emoji       = "🏛️",
        description = "Long-term trades on D1/W1/MN1. Maximum confirmation, macro trend focus.",
        scan_timeframes = ["D1", "W1", "MN1"],
        preferred_timeframe = "D1",
        confirmation_map = {"D1": "W1", "W1": "MN1", "MN1": "MN1"},
        context_timeframes = ["D1", "W1", "MN1"],
        min_votes           = 5,   # near-unanimous agreement required
        min_votes_kill_zone = 5,
        confidence_threshold = 82,
        volume_spike_threshold = 1.8,
        breakout_lookback = 50,
        liquidity_lookback = 40,
        min_rr_ratio = 2.0,
        feature_weights = {
            "breakout": 0.03, "liquidity_sweep": 0.03, "volume_spike": 0.03,
            "momentum_shift": 0.01, "trend_regime": 0.16, "macro_alignment": 0.0,
        },
        alert_min_win_probability = 72,
        alert_min_grades = ("A+",),
        confluence_min_tfs = 3,
        indicator_weights = {
            "RSI(14)": 0.07, "MACD": 0.18, "EMA Stack": 0.25,
            "ADX DI": 0.18, "CCI(20)": 0.10, "BB %B": 0.04,
            "Williams%R": 0.03, "Supertrend": 0.15,
        },
        sl_mult_override = {
            "D1": 2.0,
            "W1": 2.2,
            "MN1": 2.4,
        },
        tp_mult = (5.0, 8.0, 12.0),  # hold weeks/months
        htf_gate_required = True,
        trade_type_label = "Position",
        tip = (
            "🏛️ <b>Position Mode active.</b>\n"
            "Scanning D1, W1 and MN1. Very few signals — only macro-confirmed moves. "
            "Stops are wide; targets are large. This is a long-term strategy."
        ),
        risk_note = "Wide structural stop for longer holds; minimum target is 2R, preferring 3R when structure permits.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["position"],
        analysis_scan_interval_seconds = 300,
        analysis_context_timeframe = "W1",
        analysis_setup_timeframe = "D1",
        analysis_refinement_timeframe = "H4",
        analysis_min_score = 65,
        analysis_early_score = 59,
        analysis_strong_score = 87,
        analysis_context_weight = 0.55,
        analysis_setup_weight = 0.40,
        analysis_refinement_weight = 0.05,
        analysis_atr_expansion_limit = 2.50,
        analysis_max_candle_age_bars = 4.0,
        preferred_rr_ratio = 3.0,
    ),

    # This is a coordinator profile for the UI and persistence layer.  The
    # alert scanner deliberately runs the existing scalp and intraday profiles
    # separately rather than passing this profile to the analysis engine.
    "scalp_interval": ModeConfig(
        name        = "scalp_interval",
        label       = "Scalp / Intra-hour",
        emoji       = "⚡📊",
        description = "Run Scalp and Intra-hour alerts together with separate timeframes.",
        scan_timeframes = ["M15", "H1"],
        preferred_timeframe = "M15",
        confirmation_map = {"M15": "H1", "H1": "H4"},
        context_timeframes = ["M15", "H1", "H4"],
        min_votes           = 4,
        min_votes_kill_zone = 3,
        confidence_threshold = 75,
        volume_spike_threshold = 1.5,
        breakout_lookback = 20,
        liquidity_lookback = 15,
        min_rr_ratio = 2.0,
        feature_weights = {},
        alert_min_win_probability = 62,
        alert_min_grades = ("A+", "A"),
        confluence_min_tfs = 99,
        indicator_weights = {},
        sl_mult_override = {},
        tp_mult = (2.0, 3.5, 4.5),
        htf_gate_required = False,
        trade_type_label = "Scalp / Intra-hour",
        tip = (
            "⚡📊 <b>Scalp / Intra-hour Mode active.</b>\n"
            "The bot monitors one Scalp timeframe and one Intra-hour timeframe "
            "at the same time."
        ),
        risk_note = "Each alert uses the risk plan of its own Scalp or Intra-hour stream.",
        analysis_scan_interval_seconds = 5,
    ),
}

DEFAULT_MODE = "intraday"
