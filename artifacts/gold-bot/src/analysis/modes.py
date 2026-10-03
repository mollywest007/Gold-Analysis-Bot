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

from dataclasses import dataclass, field
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

    # ── Risk / reward ──────────────────────────────────────────────────────────
    # ATR multiplier for SL distance — keyed by timeframe; missing TFs get default
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
        stop_buffer_atr=0.08,
        minimum_stop_distance_atr=0.25,
        maximum_stop_distance_atr=1.75,
        target_buffer_atr=0.08,
        minimum_target_room_atr=0.60,
        entry_zone_atr=0.08,
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
        stop_buffer_atr=0.10,
        minimum_stop_distance_atr=0.35,
        maximum_stop_distance_atr=2.00,
        target_buffer_atr=0.10,
        minimum_target_room_atr=0.75,
        entry_zone_atr=0.12,
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
        stop_buffer_atr=0.12,
        minimum_stop_distance_atr=0.45,
        maximum_stop_distance_atr=2.25,
        target_buffer_atr=0.12,
        minimum_target_room_atr=0.90,
        entry_zone_atr=0.16,
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
        stop_buffer_atr=0.15,
        minimum_stop_distance_atr=0.50,
        maximum_stop_distance_atr=2.50,
        target_buffer_atr=0.15,
        minimum_target_room_atr=1.00,
        entry_zone_atr=0.20,
    ),
}


# ─── Mode definitions ─────────────────────────────────────────────────────────

MODES: Dict[str, ModeConfig] = {

    "scalp": ModeConfig(
        name        = "scalp",
        label       = "Scalp",
        emoji       = "⚡",
        description = "Quick entries on M5/M15. Tight stops, fast targets. Pure momentum.",
        scan_timeframes = ["M1", "M3", "M5", "M15"],
        # M15 is the stable default for a new Scalp session. M1/M3/M5 remain
        # available in Settings for traders who want faster execution.
        preferred_timeframe = "M15",
        confirmation_map = {"M1": "M15", "M3": "M15", "M5": "M30", "M15": "H1"},
        context_timeframes = ["M15", "M30", "H1"],
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
            "Signals fire on M1, M3, M5 and M15. Stops are tight — "
            "monitor the trade closely and be ready to exit quickly."
        ),
        risk_note = "Tight volatility stop; TP1 is a quick 1.5R target.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["scalp"],
    ),

    "intraday": ModeConfig(
        name        = "intraday",
        label       = "Intraday",
        emoji       = "📊",
        description = "Day-trade setups on M15/M30/H1. Balanced risk, same-day exits.",
        scan_timeframes = ["M15", "M30", "H1"],
        preferred_timeframe = "H1",
        confirmation_map = {"M15": "H1", "M30": "H1", "H1": "H4"},
        context_timeframes = ["H1", "H4", "D1"],
        min_votes           = 4,   # current engine default
        min_votes_kill_zone = 3,
        confidence_threshold = 75,
        volume_spike_threshold = 1.5,
        breakout_lookback = 20,
        liquidity_lookback = 15,
        min_rr_ratio = 2.0,
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
        risk_note = "Balanced intraday stop; TP1 targets 2R and TP2 extends the day move.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["intraday"],
    ),

    "swing": ModeConfig(
        name        = "swing",
        label       = "Swing",
        emoji       = "🌊",
        description = "Multi-day setups on H4/D1/W1. Wider stops, bigger targets, fewer signals.",
        scan_timeframes = ["H4", "D1", "W1"],
        preferred_timeframe = "H4",
        confirmation_map = {"H4": "D1", "D1": "W1", "W1": "MN1"},
        context_timeframes = ["H4", "D1", "W1", "MN1"],
        min_votes           = 4,
        min_votes_kill_zone = 4,   # kill zones less relevant on H4+
        confidence_threshold = 78,
        volume_spike_threshold = 1.6,
        breakout_lookback = 30,
        liquidity_lookback = 25,
        min_rr_ratio = 2.5,
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
            "H4": 2.0,
            "D1": 2.2,
            "W1": 2.4,
        },
        tp_mult = (3.0, 5.0, 7.0),   # hold for the full move
        htf_gate_required = True,      # strong counter-trend D1 blocks H4 signals
        trade_type_label = "Swing",
        tip = (
            "🌊 <b>Swing Mode active.</b>\n"
            "Scanning H4, D1 and W1. Wider stops, larger targets. "
            "Expect fewer signals — only high-quality setups fire."
        ),
        risk_note = "Structural swing stop with room for normal 4H/D1 noise; targets seek 2.5R+.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["swing"],
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
        min_rr_ratio = 3.0,
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
        risk_note = "Macro ATR/structure stop; targets are deliberately wide for multi-week moves.",
        momentum_pullback = MOMENTUM_PULLBACK_PROFILES["position"],
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
    ),
}

DEFAULT_MODE = "intraday"
