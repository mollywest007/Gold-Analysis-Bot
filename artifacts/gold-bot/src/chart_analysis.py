"""
Google Gemini Vision — institutional XAU/USD chart analysis.

Follows the full institutional framework:
  - HTF (H4/D1) vs LTF (H1/M30/M15) trend separation
  - Market structure: HH/HL/LH/LL, BOS, CHoCH
  - Liquidity sweeps, FVG, Order Blocks
  - Candlestick behaviour analysis
  - Buying vs selling pressure
  - Open-trade validity check (when trade context provided)
  - Risk Assessment + Final Summary with scenarios
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from src.analysis.modes import MODES, resolve_momentum_pullback_profile

logger = logging.getLogger(__name__)

# Keep the vision model configurable because Google retires model aliases.
# The current default is the model Google returned from the live API migration
# error for the previously configured gemini-2.0-flash endpoint.
_GEMINI_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.6-flash")
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI_MODEL}:generateContent"
)


def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_AI_KEY", "")
    if not key:
        raise RuntimeError(
            "GOOGLE_AI_KEY is not set. "
            "Get a free key at aistudio.google.com/app/apikey"
        )
    return key


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChartAnalysisResult:
    # Core direction
    bias: str                           # "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING"
    trend: str                          # "UPTREND" | "DOWNTREND" | "SIDEWAYS"
    htf_trend: str                      # Higher TF (H4/D1) trend
    ltf_trend: str                      # Lower TF (H1/M30/M15) trend
    market_structure: str               # "HH_HL" | "LH_LL" | "RANGING" | "TRANSITION"
    timeframe: str

    # Confidence & probability
    confidence: int                     # 0–100 — overall setup quality
    win_probability: int                # 0–100 — estimated win rate
    bullish_probability: int            # 0–100 — probability bulls win
    bearish_probability: int            # 0–100 — probability bears win

    # Patterns
    chart_patterns: list[str]
    candlestick_pattern: str
    candlestick_behavior: str           # narrative: "Rejection wick at resistance…"
    momentum: str                       # "STRONG" | "MODERATE" | "WEAK" | "DIVERGING"

    # Structure events
    bos_detected: bool
    choch_detected: bool
    liquidity_sweep: str                # description or ""

    # Key levels
    key_support: list[float]
    key_resistance: list[float]
    order_block: Optional[float]
    fair_value_gap: Optional[float]

    # Pressure
    buying_pressure: str                # narrative description
    selling_pressure: str               # narrative description
    pressure_advantage: str             # "BUYERS" | "SELLERS" | "NEUTRAL"

    # Trade setup
    entry_type: str                     # "ENTRY" | "BREAKOUT" | "RETEST" | "REVERSAL" | "WAIT"
    setup_status: str                   # WAIT | DEVELOPING | MODERATE ENTRY | MISSED | INVALID
    current_confirmation: str           # what has already happened on the chart
    moderate_entry_low: Optional[float]
    moderate_entry_high: Optional[float]
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    invalidation: Optional[float]
    rr_ratio: Optional[float]
    trade_quality: str                  # "Excellent" | "Good" | "Average" | "Poor"
    risk_level: str                     # "Low" | "Medium" | "High"

    # Confluence & reasoning
    confluence_factors: list[str]
    reasons: list[str]                  # reasons supporting the bias
    bullish_scenario: str
    bearish_scenario: str
    entry_reason: str                   # why the direct entry is valid, or what remains
    summary: str                        # 3–4 sentence professional assessment

    # Open trade assessment (populated when trade context was passed)
    open_trade_valid: Optional[bool]    # None = no trade passed
    open_trade_notes: str               # detailed trade-validity narrative

    raw: dict = field(default_factory=dict, repr=False)
    analysis_mode: str = "intraday"
    direction: str = "NEUTRAL"
    setup_type: str = ""
    skip_reason: str = ""
    skip_detail: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT = """\
You are an expert institutional market analyst specializing in XAU/USD (Gold).
Your job is to analyze this chart objectively using price action and market structure.
Never guess or claim certainty. Every conclusion must be supported by evidence from the chart.

ACTIVE MODE RULES — OVERRIDE ANY GENERIC EXAMPLES BELOW
{mode_instructions}

Work through the following analysis steps IN ORDER:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — TREND IDENTIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Read the chart's displayed timeframe and analyze only that chart's local swing
structure. Do not infer or use another timeframe as confirmation. Identify recent
HH/HL, LH/LL, or unclear structure; do not wait for a complete long-term trend.
The legacy htf_trend and ltf_trend fields must not imply that other timeframe
data was analyzed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — MARKET STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mark major support and resistance zones. Identify market structure:
• Higher Highs (HH) + Higher Lows (HL) → HH_HL (bullish)
• Lower Highs (LH) + Lower Lows (LL) → LH_LL (bearish)
• No clear sequence → RANGING
• Structure mid-break → TRANSITION

Detect and flag:
• Break of Structure (BOS): price closes beyond a prior swing point IN the trend direction (continuation)
• Change of Character (CHoCH): price closes beyond a prior swing point AGAINST the trend (potential reversal)
• Liquidity Sweeps: sharp wicks that take out obvious swing highs/lows before reversing — institutions
  clearing retail stop clusters before the real move begins.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — KEY LEVELS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mark the most significant levels visible on the chart:
• Support zones: prior swing lows, demand areas, previous highs turned support
• Resistance zones: prior swing highs, supply areas, previous lows turned resistance
• Order Block (OB): the last up/down candle before a strong impulse move away from that level —
  these are areas where institutions placed large orders; price frequently returns to them
• Fair Value Gap (FVG): a 3-candle imbalance where the middle candle's body doesn't overlap
  the wicks of candles 1 and 3 — institutions fill these gaps on retraces
Read prices DIRECTLY from the Y-axis — do not estimate.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — CANDLESTICK BEHAVIOUR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Analyze the most recent 1–5 candles:
• Rejections: long wicks at key levels — shows price was pushed back from that zone
• Strong momentum candles: large bodies, small wicks — directional conviction
• Engulfing candles: body fully engulfs prior candle — reversal signal
• Doji: near equal open/close — indecision at a level
• Pin bars / Hammers / Shooting Stars: small body, large wick — rejection / stop hunt complete

Describe the behaviour narrative in candlestick_behavior.
Name the most significant pattern in candlestick_pattern.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 5 — BUYING vs SELLING PRESSURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Evaluate who currently has the advantage and WHY:
• Buying pressure: evidence of demand (bounces off support, bullish candles, higher lows)
• Selling pressure: evidence of supply (rejections at resistance, bearish candles, lower highs)
• Explain why buyers or sellers currently have the advantage.

WRITING STYLE — always use probabilistic language:
  ✅ "The probability currently favors buyers because price is forming higher lows while holding above
      support. However, resistance remains overhead, so bullish continuation is not confirmed until a
      candle closes above that level."
  ❌ "Gold will go up." — NEVER say this.

  ✅ "If price closes below support with strong bearish momentum, sellers would gain the advantage.
      Until then, the current move may simply be a pullback."
  ❌ "Sell now." — NEVER say this.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 6 — CONFLUENCE SCORING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
List every factor supporting the trade direction. Record confluence clearly,
but do not turn a fixed confluence count into a mechanical entry gate:
- Selected-timeframe swing structure
- Pullback to a nearby swing, EMA20 context, or prior local level
- Rejection and minor-swing close that complete the entry sequence
- Visible volume or momentum behavior as supporting context

Entry speed is relative to the selected mode and timeframe. Use ONE strong
price-action sequence: a controlled pullback, rejection at a nearby level, and
a close beyond the relevant minor swing. Do not force globally fast behavior,
add extra indicators, or require higher-timeframe alignment.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 7 — TRADE LEVELS & DIRECT ENTRY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Do not enter only because price touched a level. Do not wait for a complete
trend or multiple confirmations. Set setup_status to one of:
WAIT — unclear or insufficient confirmation;
DEVELOPING — directional idea is forming but needs a little more evidence;
MODERATE ENTRY — enough evidence supports a timely entry and price remains
close to the setup area;
MISSED — confirmation happened but price has moved too far, so wait for a
pullback/retest and do not chase;
INVALID — the setup thesis has failed.

Before using MODERATE ENTRY, verify the selected mode's local swing, controlled
pullback, nearby-level rejection, and minor-swing close are visible. One strong
price-action sequence is enough; do not wait for extra indicators or timeframes.
If the pullback or rejection is still forming, use DEVELOPING. If the sequence
is complete but price is extended, use MISSED and wait for a mode-appropriate
retest. If screenshot quality prevents reliable price or structure reading, use
WAIT and record insufficient directional structure.

Whenever setup_status is WAIT, DEVELOPING, MISSED, or INVALID, record a primary
skip_reason from this exact list: Insufficient TP room; Strong opposing
resistance/support; Ranging/choppy market; Pullback destroyed structure; RSI
strongly disagrees; Breakout too extended; Invalid SL structure; Insufficient
directional structure; No valid rejection; No structure break; Setup already
extended. Also record the active mode, chart timeframe, direction, setup type,
and a short skip_detail. Never return a bare WAIT without its reason.

• Entry: current price after the selected timeframe's pullback rejection and minor swing break
• Oversized breakout: do not enter its extreme; wait for one controlled retest
  appropriate to mode/timeframe. Enter promptly after a valid scalp retest;
  Intraday/Swing may wait longer for a meaningful retest. No second long sequence.
• Chop: require repeated alternation/compression and weak progress, or repeatedly
  tested nearby boundaries. Do not call ordinary higher-timeframe consolidation
  chop based only on several sideways candles.
• Stop Loss: BUY below the relevant pullback swing low; SELL above its swing
  high. ATR14 is only a volatility sanity check. If the structural stop is too
  tight or wider than the profile cap, skip; never widen it away from structure.
• TP1: a real opposing swing/liquidity level on this chart, with at least 1.5R
  realistic room. If opposing structure blocks it, use WAIT rather than invent
  or extend a target.
• TP2 / TP3: only farther, distinct opposing structural levels visible on this chart.
  Never invent measured-move, higher-timeframe, or fixed-R targets.
• Invalidation: the specific candle CLOSE that definitively cancels the setup thesis
• Trade Quality:
     Excellent — clear selected-timeframe structure and complete entry sequence
    Good      — valid sequence with ordinary uncertainty
    Average   — setup is developing or evidence is incomplete
    Poor      — choppy/ranging structure or ambiguous levels
• Risk Level:
     Low    — clear pullback swing and structural stop within the mode's risk limit
    Medium — structural plan is valid with moderate volatility
    High   — choppy structure, excessive risk, or ambiguous stop placement

{open_trade_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 8 — RISK ASSESSMENT & FINAL SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Always include:
  Current Bias: Bullish / Bearish / Neutral
  Directional estimate (not a backtest): Bullish XX% / Bearish XX%
  Key Resistance: [level]
  Key Support: [level]
  Trade Quality: Excellent / Good / Average / Poor
  Risk Level: Low / Medium / High

Write:
• reasons: 3–5 bullet points explaining WHY the bias is what it is (evidence-based, no guarantees)
• bullish_scenario: what specifically needs to happen for buyers to win (e.g. "candle close above X")
• bearish_scenario: what specifically needs to happen for sellers to win
• summary: 3–4 sentence professional assessment covering structure, setup quality, and execution plan

End with: "This analysis is based solely on current price action and cannot guarantee future market
movement. Always use proper risk management and stop losses."

Assign confidence (0–100): how clean, clear, and well-supported is the entire analysis?
  90–100: unusually clear selected-timeframe structure and entry evidence
  70–89:  clear local setup with the active mode's entry sequence
  50–69:  workable setup but with notable ambiguities
  Below 50: do not suggest a trade

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return ONLY a single valid JSON object — no markdown fences, no explanation, no extra text:

{{
  "bias":                 "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING",
  "htf_trend":            "BULLISH" | "BEARISH" | "NEUTRAL",
  "ltf_trend":            "BULLISH" | "BEARISH" | "NEUTRAL",
  "trend":                "UPTREND" | "DOWNTREND" | "SIDEWAYS",
  "market_structure":     "HH_HL" | "LH_LL" | "RANGING" | "TRANSITION",
  "mode":                 "<active selected mode>",
  "timeframe":            "<read from chart label, e.g. M15, H1, H4 — or 'Unknown'>",
  "direction":            "BUY" | "SELL" | "NEUTRAL",
  "setup_type":           "<short pullback/retest structure label, or 'Unclassified'>",
  "skip_reason":          "<one exact skip category, or empty for a confirmed entry>",
  "skip_detail":          "<short explanation of the primary skip reason>",
  "confidence":           <integer 0-100>,
  "win_probability":      <integer 0-100>,
  "bullish_probability":  <integer 0-100>,
  "bearish_probability":  <integer 0-100>,
  "chart_patterns":       ["<pattern 1>", "<pattern 2>"],
  "candlestick_pattern":  "<most significant recent pattern or 'None'>",
  "candlestick_behavior": "<narrative: what recent candles are doing and what it means — probabilistic language only>",
  "momentum":             "STRONG" | "MODERATE" | "WEAK" | "DIVERGING",
  "bos_detected":         true | false,
  "choch_detected":       true | false,
  "liquidity_sweep":      "<description of any liquidity sweep visible, or ''>",
  "key_support":          [<up to 3 float prices — read from Y-axis>],
  "key_resistance":       [<up to 3 float prices — read from Y-axis>],
  "order_block":          <nearest OB price as float, or null>,
  "fair_value_gap":       <nearest FVG midpoint as float, or null>,
  "buying_pressure":      "<evidence of buying pressure — probabilistic language>",
  "selling_pressure":     "<evidence of selling pressure — probabilistic language>",
  "pressure_advantage":   "BUYERS" | "SELLERS" | "NEUTRAL",
  "entry_type":           "ENTRY" | "BREAKOUT" | "RETEST" | "REVERSAL" | "WAIT",
  "setup_status":         "WAIT" | "DEVELOPING" | "MODERATE ENTRY" | "MISSED" | "INVALID",
  "current_confirmation": "<exact confirmation already visible, or what is still missing>",
  "moderate_entry_low":   <float or null>,
  "moderate_entry_high":  <float or null>,
  "entry":                <float or null>,
  "stop_loss":            <float or null>,
  "take_profit_1":        <float or null>,
  "take_profit_2":        <float or null>,
  "take_profit_3":        <float or null>,
  "invalidation":         <float or null>,
  "rr_ratio":             <float or null>,
  "trade_quality":        "Excellent" | "Good" | "Average" | "Poor",
  "risk_level":           "Low" | "Medium" | "High",
  "confluence_factors":   ["<factor 1>", "<factor 2>"],
  "reasons":              ["<reason 1>", "<reason 2>", "<reason 3>"],
  "bullish_scenario":     "<what specifically needs to happen for bulls to win>",
  "bearish_scenario":     "<what specifically needs to happen for bears to win>",
  "entry_reason":         "<specific zone and reason for the direct entry, or why to wait>",
  "summary":              "<3-4 sentence professional assessment — end with the risk warning>",
  "open_trade_valid":     true | false | null,
  "open_trade_notes":     "<trade validity analysis, or '' if no trade was provided>"
}}

Critical rules:
- Gold (XAU/USD) currently trades around 3200–3500. Read EXACT prices from the Y-axis.
- Never force a trade. Require the selected mode's pullback-rejection-minor-break
  sequence and one strong price-action confirmation; do not substitute a stack
  of weaker confirmations.
- Keep the stop beyond the structural pullback swing. ATR14 is a volatility
  sanity check only; reject too-tight or profile-cap-exceeding structure rather
  than widening a stop away from its swing.
- Targets must be real opposing levels from this chart. Never claim profitability
  or present an untested win probability as a historical statistic.
- For any non-entry status, populate mode, timeframe, direction, setup_type,
  skip_reason, and skip_detail. Never return only "WAIT" without a primary reason.
- bullish_probability + bearish_probability should sum to approximately 100.
- ALWAYS use probabilistic language: never "price will go up/down", always "probability favors X because…"
- Never tell the user to close an open trade simply because it is in drawdown — assess the STRUCTURE.
- Output ONLY the JSON object. Absolutely nothing else.
"""

_OPEN_TRADE_SECTION = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 7b — OPEN TRADE ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The user has the following open trade. Analyse it SEPARATELY from the new setup:

{trade_details}

For this trade, report in open_trade_notes:
• Entry price and current price — how far away and in which direction
• Is the trade still technically valid? (Is the original setup thesis intact?)
• Key support/resistance zones nearby that are relevant to this trade
• What would strengthen this trade (confirm it's working)
• What would invalidate this trade (structural reason to reconsider, NOT just being in loss)
• Do NOT recommend closing simply because the trade is in a drawdown.
  A trade in loss is not automatically invalid — assess the STRUCTURE.
Set open_trade_valid = true if the original thesis is still intact, false if structure has broken against it.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────────────────────────────────────

def _profile_instructions(mode_name: str, timeframe: str) -> str:
    config = MODES.get(mode_name)
    if config is None:
        raise ValueError(
            f"Mode '{mode_name}' does not have a standalone analysis profile."
        )
    timeframe = timeframe or config.preferred_timeframe
    profile = resolve_momentum_pullback_profile(config, timeframe)
    mode_guidance = {
        "scalp": (
            "Use recent, relatively small structure and a controlled shallow "
            "pullback; confirm promptly with rejection and the minor pullback "
            "swing break. On charts above M15, widen the structure to the "
            "selected chart instead of reusing M1/M5 assumptions."
        ),
        "intraday": (
            "Be more selective than Scalp but remain responsive. Use selected-"
            "chart intraday swings, a moderate pullback, and a meaningful "
            "minor/intermediate break; avoid obvious ranges."
        ),
        "swing": (
            "Wait for broader selected-chart swing structure and a deeper "
            "pullback. Ignore small intraday fluctuations; require a stronger "
            "selected-chart structure break before confirming. Do not wait for "
            "a complete trend—confirm when the mode-appropriate structural "
            "sequence is complete."
        ),
        "position": (
            "Prioritize major selected-chart swing highs/lows and deeper "
            "pullbacks. Ignore insignificant fluctuations; a small rejection "
            "or intraday break is not position confirmation."
        ),
    }.get(mode_name, "")
    return (
        f"Selected mode: {config.label}; selected timeframe: "
        f"{timeframe}. {mode_guidance} Direction must come from this timeframe's "
        f"HH/HL or LH/LL structure and price momentum, not EMA20/EMA50 alignment. "
        f"Use confirmed pivots with {profile.structure_pivot_radius} candle(s) "
        f"on each side over the recent {profile.structure_lookback_candles}-bar "
        f"structure window. Allow {profile.pullback_candles} completed pullback "
        f"bar(s), with ATR-relative depth "
        f"{profile.minimum_pullback_depth_atr:g}–"
        f"{profile.maximum_pullback_depth_atr:g} ATR while the protected swing "
        f"holds. A simple rejection and close beyond the relevant pullback swing "
        f"by at least {profile.confirmation_break_atr:g} ATR is sufficient; do "
        "not require multiple candle patterns or three confirmation candles. "
        f"Chop checks use {profile.chop_lookback_candles} candles and "
        f"{profile.range_trap_minimum_touches} repeated touches; sideways candles "
        "alone do not make higher-timeframe consolidation chop. If an unusually "
        "large breakout candle appears, do not enter its extreme: wait for one "
        "controlled mode/timeframe-sized retest that holds, then resume promptly "
        "on the single relevant rejection/structure confirmation—do not add a "
        "second long confirmation sequence. "
        f"Do not chase a breakout range over "
        f"{profile.maximum_breakout_range_atr:g} ATR or price more than "
        f"{profile.maximum_ema_distance_atr:g} ATR from EMA20. RSI is soft: "
        "prefer BUY above 45 and rising, SELL below 55 and falling; skip only "
        f"for strong disagreement (BUY below {profile.buy_rsi_veto_below:g} or "
        f"SELL above {profile.sell_rsi_veto_above:g}), never for missing a 50 "
        f"cross. Put BUY SL below the pullback swing low or SELL SL above the "
        f"pullback swing high. ATR14 is only a volatility sanity check: if the "
        f"structural stop is tighter than {profile.minimum_stop_distance_atr:g} "
        f"ATR or wider than {profile.maximum_stop_distance_atr:g} ATR, skip; "
        "never widen it away from structure. TP1 must be a real opposing "
        f"selected-timeframe level with at least 1:{profile.minimum_target_rr:g} "
        f"R:R and {profile.minimum_target_room_atr:g} ATR of room; if the "
        "structure cannot support that target, skip instead of inventing one. "
        f"Expected holding window: about {profile.expected_holding_bars} "
        "selected-timeframe candles. Entry pace is relative to the selected "
        "mode and timeframe; use no extra indicators as confirmation. Do not "
        "use fixed pip/point distances or require higher-timeframe confirmation."
    )


def _mode_prompt_instructions(
    analysis_mode: str,
    selected_timeframe: str | None,
    allowed_streams: list[tuple[str, str]] | None,
) -> str:
    if analysis_mode != "scalp_interval":
        return _profile_instructions(analysis_mode, selected_timeframe or "")
    if not allowed_streams:
        raise ValueError(
            "Combined chart analysis requires the selected Scalp and Intra-hour streams."
        )
    stream_rules = [
        _profile_instructions(mode, timeframe)
        for mode, timeframe in allowed_streams
    ]
    return (
        "Combined mode is a coordinator, not a third strategy. Read the chart's "
        "displayed timeframe and use only its matching stream. Never mix "
        "thresholds. If the timeframe is unreadable or matches neither stream, "
        "return WAIT and provide no entry, stop, or targets.\n"
        + "\n".join(f"- {rule}" for rule in stream_rules)
    )


def _canonical_timeframe(value: str) -> str:
    normalized = str(value or "").upper().replace(" ", "")
    aliases = {
        "1H": "H1",
        "60M": "H1",
        "4H": "H4",
        "240M": "H4",
        "1D": "D1",
        "1W": "W1",
        "1MO": "MN1",
        "15M": "M15",
        "30M": "M30",
        "5M": "M5",
        "3M": "M3",
    }
    return aliases.get(normalized, normalized)


def _resolve_chart_scope(
    analysis_mode: str,
    reported_timeframe: str,
    selected_timeframe: str | None,
    allowed_streams: list[tuple[str, str]] | None,
) -> tuple[str, str | None]:
    reported = _canonical_timeframe(reported_timeframe)
    unknown = reported in ("", "UNKNOWN", "N/A", "NOT VISIBLE")
    if analysis_mode == "scalp_interval":
        matches = [
            mode
            for mode, timeframe in (allowed_streams or [])
            if not unknown and _canonical_timeframe(timeframe) == reported
        ]
        if matches:
            return ("scalp" if "scalp" in matches else matches[0]), None
        return analysis_mode, (
            "The chart timeframe could not be matched to a selected combined-mode "
            "stream. No trade setup is issued."
        )
    if analysis_mode not in MODES or MODES[analysis_mode].momentum_pullback is None:
        raise ValueError(f"Invalid standalone analysis mode '{analysis_mode}'.")
    expected = _canonical_timeframe(selected_timeframe or "")
    if expected and not unknown and reported != expected:
        return analysis_mode, (
            f"The chart shows {reported}, but the selected timeframe is {expected}. "
            "No trade setup is issued."
        )
    return analysis_mode, None


def _suppress_unscoped_setup(parsed: dict, reason: str) -> None:
    parsed.update(
        {
            "setup_status": "WAIT",
            "entry_type": "WAIT",
            "current_confirmation": reason,
            "entry_reason": reason,
            "entry": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "take_profit_3": None,
            "invalidation": None,
            "rr_ratio": None,
            "moderate_entry_low": None,
            "moderate_entry_high": None,
        }
    )


async def analyse_chart_bytes(
    img_bytes: bytes,
    *,
    open_trade: Optional[dict] = None,
    timeout: int = 90,
    analysis_mode: str = "intraday",
    selected_timeframe: str | None = None,
    allowed_streams: list[tuple[str, str]] | None = None,
) -> ChartAnalysisResult:
    """
    Send img_bytes to Gemini Vision and return a ChartAnalysisResult.

    open_trade: optional dict with keys direction, entry, sl, tp1, tp2, tp3,
                timeframe, confidence — passed to the prompt for trade-validity analysis.
    Combined mode requires allowed_streams as (analysis mode, timeframe) pairs.
    """
    mime = "image/jpeg"
    if img_bytes[:4] == b"\x89PNG":
        mime = "image/png"

    b64_image = base64.b64encode(img_bytes).decode()
    api_key = _get_api_key()

    # Build open-trade context section if a trade was supplied
    if open_trade:
        d          = open_trade.get("direction", "?")
        entry      = open_trade.get("entry", 0)
        sl         = open_trade.get("sl", 0)
        tp1        = open_trade.get("tp1", 0)
        tp2        = open_trade.get("tp2", 0)
        tp3        = open_trade.get("tp3")
        tf         = open_trade.get("timeframe", "?")
        conf       = open_trade.get("confidence", 0)
        tp3_line   = f"\n  TP3      : {tp3:,.2f}" if tp3 else ""
        trade_str  = (
            f"  Direction: {d}\n"
            f"  Timeframe: {tf}\n"
            f"  Entry    : {entry:,.2f}\n"
            f"  Stop Loss: {sl:,.2f}\n"
            f"  TP1      : {tp1:,.2f}\n"
            f"  TP2      : {tp2:,.2f}{tp3_line}\n"
            f"  Confidence at open: {conf}%"
        )
        open_section = _OPEN_TRADE_SECTION.format(trade_details=trade_str)
    else:
        open_section = ""

    mode_instructions = _mode_prompt_instructions(
        analysis_mode, selected_timeframe, allowed_streams
    )
    prompt = _PROMPT.format(
        mode_instructions=mode_instructions,
        open_trade_section=open_section,
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": b64_image}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
        },
    }

    logger.info("Sending chart to Gemini Vision (institutional analysis)…")
    last_err: Exception | None = None
    for attempt in range(3):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _GEMINI_URL,
                params={"key": api_key},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    break
                body = await resp.text()
                if resp.status == 429:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"Gemini 429 quota — waiting {wait}s (attempt {attempt+1}/3)")
                    import asyncio as _aio
                    await _aio.sleep(wait)
                    last_err = RuntimeError("Gemini API quota exceeded (429). Try again in a few minutes.")
                    continue
                raise RuntimeError(f"Gemini API error {resp.status}: {body[:300]}")
    else:
        raise last_err

    # Extract text — scan all parts for the one that contains JSON
    try:
        parts = data["candidates"][0]["content"]["parts"]
        raw_text = next(
            (p["text"] for p in parts if p.get("text", "").strip().startswith("{")),
            parts[-1].get("text", ""),
        )
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response shape: {data}") from e

    logger.info(f"Gemini raw response: {raw_text[:400]}")

    # Strip accidental markdown fences
    json_text = raw_text.strip()
    json_text = re.sub(r"^```[a-z]*\n?", "", json_text)
    json_text = re.sub(r"\n?```$",       "", json_text)

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        m = re.search(r"\{.*\}", json_text, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
        else:
            raise ValueError(f"Gemini did not return valid JSON: {e}\n---\n{raw_text}") from e

    def _f(key: str) -> Optional[float]:
        v = parsed.get(key)
        return float(v) if v is not None else None

    def _fl(key: str) -> list[float]:
        raw = parsed.get(key, [])
        return [float(x) for x in raw if x is not None] if isinstance(raw, list) else []

    def _sl(key: str) -> list[str]:
        raw = parsed.get(key, [])
        return [str(x) for x in raw if x] if isinstance(raw, list) else []

    def _b(key: str, default: bool = False) -> bool:
        v = parsed.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("true", "1", "yes")

    resolved_mode, scope_issue = _resolve_chart_scope(
        analysis_mode,
        str(parsed.get("timeframe", "Unknown")),
        selected_timeframe,
        allowed_streams,
    )
    if scope_issue:
        _suppress_unscoped_setup(parsed, scope_issue)
    parsed["analysis_mode"] = resolved_mode
    parsed["mode"] = resolved_mode
    if selected_timeframe:
        parsed["timeframe"] = selected_timeframe

    raw_status = str(parsed.get("setup_status", "WAIT")).upper().strip()
    status_aliases = {
        "MODERATE": "MODERATE ENTRY",
        "ENTRY": "MODERATE ENTRY",
        "CONFIRMED": "MODERATE ENTRY",
        "NO TRADE": "WAIT",
        "WATCH": "DEVELOPING",
    }
    setup_status = status_aliases.get(raw_status, raw_status)
    if setup_status not in {"WAIT", "DEVELOPING", "MODERATE ENTRY", "MISSED", "INVALID"}:
        setup_status = "WAIT"

    bias = str(parsed.get("bias", "NEUTRAL")).upper()
    raw_direction = str(parsed.get("direction", "")).upper()
    direction = (
        raw_direction if raw_direction in {"BUY", "SELL", "NEUTRAL"}
        else "BUY" if bias == "BULLISH"
        else "SELL" if bias == "BEARISH"
        else "NEUTRAL"
    )
    setup_type = str(
        parsed.get("setup_type") or parsed.get("entry_type") or "Unclassified"
    )
    skip_reason = str(parsed.get("skip_reason") or "").strip()
    skip_detail = str(
        parsed.get("skip_detail")
        or parsed.get("entry_reason")
        or parsed.get("current_confirmation")
        or "The selected setup conditions are incomplete."
    ).strip()
    valid_skip_reasons = {
        "Insufficient TP room",
        "Strong opposing resistance/support",
        "Ranging/choppy market",
        "Pullback destroyed structure",
        "RSI strongly disagrees",
        "Breakout too extended",
        "Invalid SL structure",
        "Insufficient directional structure",
        "No valid rejection",
        "No structure break",
        "Setup already extended",
    }
    if setup_status == "MODERATE ENTRY":
        skip_reason = ""
        skip_detail = ""
    elif skip_reason not in valid_skip_reasons:
        reason_text = " ".join(
            (
                str(parsed.get("entry_reason") or ""),
                str(parsed.get("current_confirmation") or ""),
            )
        ).lower()
        if "chop" in reason_text or "rang" in reason_text or "boxed" in reason_text:
            skip_reason = "Ranging/choppy market"
        elif "rsi" in reason_text:
            skip_reason = "RSI strongly disagrees"
        elif "breakout" in reason_text and ("large" in reason_text or "extend" in reason_text):
            skip_reason = "Breakout too extended"
        elif "extended" in reason_text or "chase" in reason_text:
            skip_reason = "Setup already extended"
        elif any(word in reason_text for word in ("resistance", "support", "opposing structure")):
            skip_reason = "Strong opposing resistance/support"
        elif any(word in reason_text for word in ("stop", "sl", "risk")):
            skip_reason = "Invalid SL structure"
        elif any(word in reason_text for word in ("target", "room", "reward")):
            skip_reason = "Insufficient TP room"
        elif "destroy" in reason_text or "invalidated" in reason_text:
            skip_reason = "Pullback destroyed structure"
        elif "rejection" in reason_text:
            skip_reason = "No valid rejection"
        elif "break" in reason_text:
            skip_reason = "No structure break"
        else:
            skip_reason = "Insufficient directional structure"
    parsed["direction"] = direction
    parsed["setup_type"] = setup_type
    parsed["skip_reason"] = skip_reason
    parsed["skip_detail"] = skip_detail
    if setup_status != "MODERATE ENTRY":
        parsed["entry_reason"] = (
            f"MODE: {resolved_mode.upper()} | "
            f"TIMEFRAME: {parsed.get('timeframe', 'Unknown')} | "
            f"DIRECTION: {direction} | SETUP TYPE: {setup_type} | "
            f"SKIP REASON: {skip_reason} | {skip_detail}"
        )

    ot_valid_raw = parsed.get("open_trade_valid")
    if ot_valid_raw is None:
        ot_valid: Optional[bool] = None
    else:
        ot_valid = bool(ot_valid_raw)

    return ChartAnalysisResult(
        bias=str(parsed.get("bias", "NEUTRAL")).upper(),
        trend=str(parsed.get("trend", "SIDEWAYS")).upper(),
        htf_trend=str(parsed.get("htf_trend", "NEUTRAL")).upper(),
        ltf_trend=str(parsed.get("ltf_trend", "NEUTRAL")).upper(),
        market_structure=str(parsed.get("market_structure", "RANGING")).upper(),
        timeframe=str(parsed.get("timeframe", "Unknown")),
        confidence=int(parsed.get("confidence", 50)),
        win_probability=int(parsed.get("win_probability", 50)),
        bullish_probability=int(parsed.get("bullish_probability", 50)),
        bearish_probability=int(parsed.get("bearish_probability", 50)),
        chart_patterns=_sl("chart_patterns"),
        candlestick_pattern=str(parsed.get("candlestick_pattern", "None")),
        candlestick_behavior=str(parsed.get("candlestick_behavior", "")),
        momentum=str(parsed.get("momentum", "MODERATE")).upper(),
        bos_detected=_b("bos_detected"),
        choch_detected=_b("choch_detected"),
        liquidity_sweep=str(parsed.get("liquidity_sweep", "")),
        key_support=_fl("key_support"),
        key_resistance=_fl("key_resistance"),
        order_block=_f("order_block"),
        fair_value_gap=_f("fair_value_gap"),
        buying_pressure=str(parsed.get("buying_pressure", "")),
        selling_pressure=str(parsed.get("selling_pressure", "")),
        pressure_advantage=str(parsed.get("pressure_advantage", "NEUTRAL")).upper(),
        entry_type=str(parsed.get("entry_type", "WAIT")).upper(),
        setup_status=setup_status,
        current_confirmation=str(parsed.get("current_confirmation", "")),
        moderate_entry_low=_f("moderate_entry_low"),
        moderate_entry_high=_f("moderate_entry_high"),
        entry=_f("entry"),
        stop_loss=_f("stop_loss"),
        take_profit_1=_f("take_profit_1"),
        take_profit_2=_f("take_profit_2"),
        take_profit_3=_f("take_profit_3"),
        invalidation=_f("invalidation"),
        rr_ratio=_f("rr_ratio"),
        trade_quality=str(parsed.get("trade_quality", "Average")),
        risk_level=str(parsed.get("risk_level", "Medium")),
        confluence_factors=_sl("confluence_factors"),
        reasons=_sl("reasons"),
        bullish_scenario=str(parsed.get("bullish_scenario", "")),
        bearish_scenario=str(parsed.get("bearish_scenario", "")),
         entry_reason=str(parsed.get("entry_reason", "")),
        summary=str(parsed.get("summary", "")),
        open_trade_valid=ot_valid,
        open_trade_notes=str(parsed.get("open_trade_notes", "")),
        raw=parsed,
        analysis_mode=resolved_mode,
        direction=direction,
        setup_type=setup_type,
        skip_reason=skip_reason,
        skip_detail=skip_detail,
    )
