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

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-2.0-flash"
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
    entry_type: str                     # "EARLY_ENTRY" | "BREAKOUT" | "RETEST" | "REVERSAL" | "WAIT"
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
    early_entry_reason: str
    summary: str                        # 3–4 sentence professional assessment

    # Open trade assessment (populated when trade context was passed)
    open_trade_valid: Optional[bool]    # None = no trade passed
    open_trade_notes: str               # detailed trade-validity narrative

    raw: dict = field(default_factory=dict, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT = """\
You are an expert institutional market analyst specialising in XAU/USD (Gold).
Your job is to analyse this chart objectively using price action and market structure.
Never guess or claim certainty. Every conclusion must be supported by evidence from the chart.

Work through the following analysis steps IN ORDER:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — TREND IDENTIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Higher timeframe (H4 / D1): is the dominant structure BULLISH, BEARISH, or NEUTRAL?
  Evidence: look at the leftmost portion of the chart for the macro direction.
• Lower timeframe (H1 / M30 / M15): what is the current near-term trend?
  Evidence: recent swing highs and lows from the last 20–30 candles.
Set htf_trend and ltf_trend accordingly.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — MARKET STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Identify the VISIBLE swing structure on the chart:
• Higher Highs (HH) + Higher Lows (HL) → HH_HL (bullish)
• Lower Highs (LH) + Lower Lows (LL) → LH_LL (bearish)
• No clear sequence → RANGING
• Structure mid-break → TRANSITION
Detect and flag:
• Break of Structure (BOS): has price closed beyond a prior swing point in the TREND direction?
• Change of Character (CHoCH): has price closed beyond a prior swing point AGAINST the trend?
  (CHoCH = potential reversal; BOS = trend continuation)
• Liquidity Sweeps: sharp wicks that take out obvious highs/lows before reversing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — KEY LEVELS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mark the most significant levels visible on the chart:
• Support zones: prior swing lows, demand areas, previous highs turned support
• Resistance zones: prior swing highs, supply areas, previous lows turned resistance
• Order Block (OB): the last up/down candle before a strong impulse move away from that level
• Fair Value Gap (FVG): a 3-candle imbalance where the middle candle's body doesn't overlap the wicks of candles 1 and 3
Read prices DIRECTLY from the Y-axis — do not estimate.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — CANDLESTICK BEHAVIOUR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Analyse the most recent 1–5 candles:
• Are there rejections (long wicks) at key levels?
• Strong momentum candles (large bodies, small wicks)?
• Engulfing candles (body fully engulfs prior candle)?
• Doji (near equal open/close — indecision)?
• Pin bars / Hammers / Shooting Stars (small body, large wick)?
Describe the behaviour narrative in candlestick_behavior.
Name the most significant pattern in candlestick_pattern.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 5 — BUYING vs SELLING PRESSURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Evaluate who currently has the advantage and WHY:
• Buying pressure: evidence of demand (bounces off support, bullish candles, higher lows)
• Selling pressure: evidence of supply (rejections at resistance, bearish candles, lower highs)
• State clearly which side has the advantage and the primary reason.
Do NOT say "price will rise/fall." Instead say:
  "Probability currently favours [buyers/sellers] because [evidence]. However, [counter-evidence]
   means this is not confirmed until [condition]."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 6 — CONFLUENCE SCORING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
List every factor supporting the trade direction. High-probability setups need 4+ confluences:
- HTF and LTF trend alignment
- Price at a key S/R level (not in the middle of nowhere)
- Confirmed BOS or CHoCH
- Candlestick confirmation at the level
- Order Block / FVG alignment
- Fibonacci retracement level (38.2%, 50%, 61.8%)
- EMA confluence (price above/below key EMAs if visible)
- Liquidity sweep before the move
- Session timing (London / NY overlap = higher probability)
- Volume spike or momentum divergence (if visible)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 7 — TRADE LEVELS & EARLY ENTRY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Only suggest a trade if win_probability >= 65.
If the setup is unclear or insufficient, set entry_type to WAIT.

EARLY ENTRY (highest priority — use these when available, they give the best R:R):
  Prefer EARLY_ENTRY over RETEST/BREAKOUT whenever a structural confluence zone is available.
  Use this waterfall in order:
    1. Order Block (OB): enter at the OB zone LOW (buy) or HIGH (sell) — tightest SL, best R:R
    2. Fair Value Gap (FVG): enter at the FVG base (buy) or top (sell) — institutions fill gaps
    3. OTE zone (Fib 61.8%): Optimal Trade Entry — deepest pullback before move resumes
    4. Fib 50%: equilibrium entry — mid-range balance point
    5. Fib 38.2%: shallow pullback entry — safer but lower R:R
  The entry price should be INSIDE the identified zone, not at market.
  Set entry_type = "EARLY_ENTRY" and explain the zone in early_entry_reason.

• Entry: precise price inside the best available confluence zone (OB / FVG / OTE / Fib)
• Stop Loss: BELOW the structural low of the entry zone (BUY) or ABOVE the structural high (SELL)
  — For OB entries: SL goes BEYOND the OB low/high with a small buffer
  — For FVG entries: SL goes just beyond the nearest swing low/high
  — SL must be at a STRUCTURAL level, not an arbitrary ATR distance
• TP1: first liquidity pocket / minor S/R (minimum 1:1.5 R:R)
• TP2: next major S/R level (minimum 1:2.5 R:R)
• TP3: measured move / key HTF level / PDH or PDL (minimum 1:3.5 R:R)
• Invalidation: the candle CLOSE that definitively cancels the setup
• Trade Quality: Excellent (4+ confluence, A+ structure, early entry at OB/FVG/OTE),
  Good (3 confluence, clean S/R, structural entry), Average (2 confluence, some ambiguity),
  Poor (<2 confluence or choppy structure)
• Risk Level: Low (OB/FVG entry + HTF aligned + SL at structural level),
  Medium (2 of 3 conditions), High (choppy, counter-trend, or SL ambiguous)

{open_trade_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 8 — FINAL SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Write:
• reasons: 3–5 bullet points explaining WHY the bias is what it is
• bullish_scenario: what specifically needs to happen for buyers to win (candle close, level break, etc.)
• bearish_scenario: what specifically needs to happen for sellers to win
• summary: 3–4 sentence professional assessment covering structure, setup quality, and execution plan

Assign confidence (0–100): how clean, clear, and well-supported is this entire analysis?
  90–100: textbook setup, multiple confirming timeframes, crystal-clear structure
  70–89:  solid setup with most confluence factors present
  50–69:  workable setup but with notable ambiguities
  Below 50: do not suggest a trade

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return ONLY a single valid JSON object — no markdown fences, no explanation, no extra text:

{
  "bias":                 "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING",
  "htf_trend":            "BULLISH" | "BEARISH" | "NEUTRAL",
  "ltf_trend":            "BULLISH" | "BEARISH" | "NEUTRAL",
  "trend":                "UPTREND" | "DOWNTREND" | "SIDEWAYS",
  "market_structure":     "HH_HL" | "LH_LL" | "RANGING" | "TRANSITION",
  "timeframe":            "<read from chart label, e.g. M15, H1, H4 — or 'Unknown'>",
  "confidence":           <integer 0-100>,
  "win_probability":      <integer 0-100>,
  "bullish_probability":  <integer 0-100>,
  "bearish_probability":  <integer 0-100>,
  "chart_patterns":       ["<pattern 1>", "<pattern 2>"],
  "candlestick_pattern":  "<most significant recent pattern or 'None'>",
  "candlestick_behavior": "<narrative: what recent candles are doing and what it means>",
  "momentum":             "STRONG" | "MODERATE" | "WEAK" | "DIVERGING",
  "bos_detected":         true | false,
  "choch_detected":       true | false,
  "liquidity_sweep":      "<description of any liquidity sweep visible, or ''>",
  "key_support":          [<up to 3 float prices — read from Y-axis>],
  "key_resistance":       [<up to 3 float prices — read from Y-axis>],
  "order_block":          <nearest OB price as float, or null>,
  "fair_value_gap":       <nearest FVG midpoint as float, or null>,
  "buying_pressure":      "<evidence of buying pressure or 'No clear buying pressure'>",
  "selling_pressure":     "<evidence of selling pressure or 'No clear selling pressure'>",
  "pressure_advantage":   "BUYERS" | "SELLERS" | "NEUTRAL",
  "entry_type":           "EARLY_ENTRY" | "BREAKOUT" | "RETEST" | "REVERSAL" | "WAIT",
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
  "bullish_scenario":     "<what needs to happen for bulls to win>",
  "bearish_scenario":     "<what needs to happen for bears to win>",
  "early_entry_reason":   "<why this is an early entry, or why to wait>",
  "summary":              "<3-4 sentence professional assessment>",
  "open_trade_valid":     true | false | null,
  "open_trade_notes":     "<trade validity analysis, or '' if no trade was provided>"
}

Critical rules:
- Gold (XAU/USD) currently trades around 3200–3500. Read EXACT prices from the Y-axis.
- Only suggest a trade if win_probability >= 65. If unclear, set entry_type to WAIT.
- Stop loss must be placed at a STRUCTURAL level, not an arbitrary distance.
- bullish_probability + bearish_probability should sum to approximately 100.
- Use probabilistic language throughout: not "price will go up" but "probability favours buyers because…"
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

async def analyse_chart_bytes(
    img_bytes: bytes,
    *,
    open_trade: Optional[dict] = None,
    timeout: int = 90,
) -> ChartAnalysisResult:
    """
    Send img_bytes to Gemini Vision and return a ChartAnalysisResult.

    open_trade: optional dict with keys direction, entry, sl, tp1, tp2, tp3,
                timeframe, confidence — passed to the prompt for trade-validity analysis.
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

    prompt = _PROMPT.format(open_trade_section=open_section)

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
        early_entry_reason=str(parsed.get("early_entry_reason", "")),
        summary=str(parsed.get("summary", "")),
        open_trade_valid=ot_valid,
        open_trade_notes=str(parsed.get("open_trade_notes", "")),
        raw=parsed,
    )
