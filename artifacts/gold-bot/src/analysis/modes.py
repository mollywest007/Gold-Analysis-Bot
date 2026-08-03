"""
Analysis Modes — defines the four trading personas the bot can adopt.

Each mode completely changes how the engine analyses the market:
  - which timeframes are scanned for auto-alerts
  - how tight/wide stops and targets are
  - how many indicator votes are required before a signal fires
  - how strict the HTF confirmation gate is
  - what confidence level is required

Adding a new mode:  add an entry to MODES dict with a ModeConfig object.
The rest of the system picks it up automatically.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class ModeConfig:
    # ── Identity ───────────────────────────────────────────────────────────────
    name:        str        # internal key  e.g. "scalp"
    label:       str        # display name  e.g. "Scalp"
    emoji:       str        # e.g. "⚡"
    description: str        # one-line summary shown in /mode

    # ── Alert scanning ─────────────────────────────────────────────────────────
    scan_timeframes: List[str]   # timeframes the background scanner watches

    # ── Signal sensitivity ─────────────────────────────────────────────────────
    min_votes:           int     # indicator votes needed outside kill zones
    min_votes_kill_zone: int     # votes needed during London/NY kill zones
    confidence_threshold: int    # min % before a signal is considered valid

    # ── Risk / reward ──────────────────────────────────────────────────────────
    # ATR multiplier for SL distance — keyed by timeframe; missing TFs get default
    sl_mult_override: Dict[str, float] = field(default_factory=dict)
    # TP distances as SL multiples: (TP1, TP2, TP3)
    tp_mult: Tuple[float, float, float] = (2.0, 3.5, 4.5)

    # ── HTF gate ───────────────────────────────────────────────────────────────
    # True  = strong counter-trend HTF bias blocks the signal outright (swing/position)
    # False = counter-trend only dents confidence (scalp/intraday, current behaviour)
    htf_gate_strict: bool = False

    # ── Trade type label injected into MarketAnalysis.trade_type ───────────────
    trade_type_label: str = "Intraday"

    # ── Tip shown after mode switch ────────────────────────────────────────────
    tip: str = ""


# ─── Mode definitions ─────────────────────────────────────────────────────────

MODES: Dict[str, ModeConfig] = {

    "scalp": ModeConfig(
        name        = "scalp",
        label       = "Scalp",
        emoji       = "⚡",
        description = "Quick entries on M5/M15. Tight stops, fast targets. Pure momentum.",
        scan_timeframes = ["M5", "M15"],
        min_votes           = 2,   # fire faster — scalp windows close quickly
        min_votes_kill_zone = 2,
        confidence_threshold = 70,
        sl_mult_override = {
            "M5":  1.3,   # very tight — scalpers cut losses fast
            "M15": 1.6,
        },
        tp_mult = (1.5, 2.5, 3.5),   # quick, realistic scalping targets
        htf_gate_strict  = False,     # ignore macro trend; trade the micro move
        trade_type_label = "Scalp",
        tip = (
            "⚡ <b>Scalp Mode active.</b>\n"
            "Signals fire on M5 and M15 only. Stops are tight — "
            "monitor the trade closely and be ready to exit quickly."
        ),
    ),

    "intraday": ModeConfig(
        name        = "intraday",
        label       = "Intraday",
        emoji       = "📊",
        description = "Day-trade setups on M15/M30/H1. Balanced risk, same-day exits.",
        scan_timeframes = ["M15", "M30", "H1"],
        min_votes           = 4,   # current engine default
        min_votes_kill_zone = 3,
        confidence_threshold = 75,
        sl_mult_override = {},     # use engine defaults (2.2–2.5×)
        tp_mult = (2.0, 3.5, 4.5),
        htf_gate_strict  = False,
        trade_type_label = "Intraday",
        tip = (
            "📊 <b>Intraday Mode active.</b>\n"
            "Scanning M15, M30 and H1. Standard risk management. "
            "Targets are realistic for same-session trades."
        ),
    ),

    "swing": ModeConfig(
        name        = "swing",
        label       = "Swing",
        emoji       = "🌊",
        description = "Multi-day setups on H4/D1. Wider stops, bigger targets, fewer signals.",
        scan_timeframes = ["H4", "D1"],
        min_votes           = 4,
        min_votes_kill_zone = 4,   # kill zones less relevant on H4+
        confidence_threshold = 78,
        sl_mult_override = {
            "H4": 3.0,   # swing candles have large wicks — needs more room
            "D1": 2.5,
        },
        tp_mult = (3.0, 5.0, 7.0),   # hold for the full move
        htf_gate_strict  = True,      # strong counter-trend D1 blocks H4 signals
        trade_type_label = "Swing",
        tip = (
            "🌊 <b>Swing Mode active.</b>\n"
            "Scanning H4 and D1. Wider stops, larger targets. "
            "Expect fewer signals — only high-quality setups fire."
        ),
    ),

    "position": ModeConfig(
        name        = "position",
        label       = "Position",
        emoji       = "🏛️",
        description = "Long-term trades on D1. Maximum confirmation, macro trend focus.",
        scan_timeframes = ["D1"],
        min_votes           = 5,   # near-unanimous agreement required
        min_votes_kill_zone = 5,
        confidence_threshold = 82,
        sl_mult_override = {
            "D1": 2.5,   # ATR on D1 is already large; 2.5× is plenty of room
        },
        tp_mult = (5.0, 8.0, 12.0),  # hold weeks/months
        htf_gate_strict  = True,
        trade_type_label = "Position",
        tip = (
            "🏛️ <b>Position Mode active.</b>\n"
            "Scanning D1 only. Very few signals — only macro-confirmed moves. "
            "Stops are wide; targets are large. This is a long-term strategy."
        ),
    ),
}

DEFAULT_MODE = "intraday"
