"""
Google Gemini Vision — professional XAU/USD chart analysis.

Multi-pattern recognition, early entry detection, confluence scoring,
market structure analysis, and high-probability setup identification.
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
    market_structure: str               # "HH_HL" | "LH_LL" | "RANGING" | "TRANSITION"
    timeframe: str

    # Confidence & probability
    confidence: int                     # 0–100 — overall setup quality
    win_probability: int                # 0–100 — estimated win rate for this specific setup

    # Patterns (multiple)
    chart_patterns: list[str]           # e.g. ["Bull Flag", "Ascending Triangle"]
    candlestick_pattern: str            # most recent candle signal
    momentum: str                       # "STRONG" | "MODERATE" | "WEAK" | "DIVERGING"

    # Key levels
    key_support: list[float]
    key_resistance: list[float]
    order_block: Optional[float]        # nearest institutional order block
    fair_value_gap: Optional[float]     # nearest unfilled FVG

    # Trade setup
    entry_type: str                     # "EARLY_ENTRY" | "BREAKOUT" | "RETEST" | "REVERSAL" | "WAIT"
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    invalidation: Optional[float]       # price that kills the setup
    rr_ratio: Optional[float]           # risk-reward ratio

    # Confluence & reasoning
    confluence_factors: list[str]       # e.g. ["EMA crossover", "Support retest", "Bullish engulfing"]
    early_entry_reason: str             # why this qualifies as an early entry (or why to wait)
    summary: str                        # 3–4 sentence professional assessment

    raw: dict = field(default_factory=dict, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT = """\
You are an elite institutional gold trader and technical analyst with 20+ years on the COMEX floor.
You are looking at a XAU/USD candlestick chart. Your job is to produce a precise, actionable trade setup
that targets an 80%+ win rate by only taking setups with strong confluence and early entry precision.

Analyse every visible element of the chart:

STEP 1 — MARKET STRUCTURE
Identify the dominant market structure: Higher Highs / Higher Lows (HH_HL = bullish), 
Lower Highs / Lower Lows (LH_LL = bearish), RANGING, or TRANSITION (structure breaking).

STEP 2 — MULTI-PATTERN RECOGNITION
Identify ALL visible chart patterns simultaneously:
- Continuation: Bull Flag, Bear Flag, Pennant, Wedge, Channel, Cup & Handle
- Reversal: Head & Shoulders, Inverse H&S, Double Top, Double Bottom, Triple Top/Bottom
- Breakout: Ascending/Descending/Symmetrical Triangle, Rectangle Box
- Institutional: Order Block retest, Fair Value Gap fill, Liquidity sweep, Breaker block
- Harmonic: Gartley, Bat, Butterfly, Crab, ABCD pattern
List all patterns you see, ranked by reliability.

STEP 3 — CANDLESTICK CONFLUENCE
Identify the most recent 1–3 candle pattern and whether it confirms or contradicts the chart pattern.
High-value signals: Bullish/Bearish Engulfing, Pin Bar (Hammer/Shooting Star), 
Morning/Evening Star, Doji at key level, Inside Bar, Marubozu.

STEP 4 — EARLY ENTRY IDENTIFICATION
Find the EARLIEST possible entry that still has a protected stop loss:
- Pre-breakout entry inside a pattern (e.g. buying the handle of Cup & Handle before breakout)
- Order block / demand zone entry (institutional accumulation areas)
- Fibonacci retracement entry (38.2%, 50%, 61.8% of last swing)
- Liquidity grab entry (false break below support / above resistance that reverses)
- EMA bounce entry (price rejecting EMA20 or EMA50 in trend direction)
The early entry should give a BETTER risk:reward than waiting for a confirmed breakout.

STEP 5 — CONFLUENCE SCORING
List every factor that supports the trade. High-probability setups (80%+ win rate) need 4+ confluences:
- Trend alignment (setup in direction of higher timeframe trend)
- Key level (support/resistance, round numbers, previous high/low)
- Pattern completion
- Candlestick confirmation
- EMA confluence (price above/below key EMAs)
- Momentum alignment (RSI, MACD direction if visible)
- Volume spike or divergence (if visible)
- Session timing (London/NY overlap = highest probability)
- Order block / FVG alignment
- Fibonacci level confluence

STEP 6 — PRECISE TRADE LEVELS
Calculate exact entry, stop loss, and THREE take profit targets:
- Entry: the precise price for the early entry (limit order level)
- Stop Loss: BELOW the nearest structural low (for buys) or ABOVE structural high (for sells)
  — stop must be BEYOND the invalidation level, not at it
- TP1: first liquidity pocket / minor resistance (1:1 R:R minimum)  
- TP2: next major S/R level (1:2 R:R minimum)
- TP3: measured move target / 1:3+ R:R
- Invalidation: the price level that definitively cancels this setup

Return ONLY a single valid JSON object — no markdown fences, no explanation, no extra text:

{
  "bias":               "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING",
  "trend":              "UPTREND" | "DOWNTREND" | "SIDEWAYS",
  "market_structure":   "HH_HL" | "LH_LL" | "RANGING" | "TRANSITION",
  "timeframe":          "<e.g. M15, H1, H4, D1 — read from chart label, or Unknown>",
  "confidence":         <integer 0-100 — how clean and clear is this setup overall>,
  "win_probability":    <integer 0-100 — estimated win rate for this specific trade setup>,
  "chart_patterns":     ["<pattern 1>", "<pattern 2>"],
  "candlestick_pattern": "<most recent candle signal or None>",
  "momentum":           "STRONG" | "MODERATE" | "WEAK" | "DIVERGING",
  "key_support":        [<up to 3 float support levels from Y-axis>],
  "key_resistance":     [<up to 3 float resistance levels from Y-axis>],
  "order_block":        <nearest order block price as float, or null>,
  "fair_value_gap":     <nearest FVG midpoint as float, or null>,
  "entry_type":         "EARLY_ENTRY" | "BREAKOUT" | "RETEST" | "REVERSAL" | "WAIT",
  "entry":              <precise entry price as float, or null>,
  "stop_loss":          <stop loss price as float, or null>,
  "take_profit_1":      <TP1 price as float, or null>,
  "take_profit_2":      <TP2 price as float, or null>,
  "take_profit_3":      <TP3 price as float, or null>,
  "invalidation":       <price that invalidates setup as float, or null>,
  "rr_ratio":           <risk-reward ratio as float e.g. 2.5, or null>,
  "confluence_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
  "early_entry_reason": "<why this is an early entry, or why to wait if entry_type is WAIT>",
  "summary":            "<3-4 sentence professional assessment covering structure, setup quality, and execution plan>"
}

Critical rules:
- Gold (XAU/USD) trades around 3200-3500 — use EXACT prices you read from the Y-axis, never estimate broadly.
- Only suggest a trade if win_probability >= 65. If setup is unclear or weak, set entry_type to WAIT.
- Stop loss must be placed at a STRUCTURAL level, not an arbitrary pip count.
- If win_probability >= 80, explain exactly WHY in confluence_factors (minimum 4 factors).
- Output ONLY the JSON object. Nothing else.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────────────────────────────────────

async def analyse_chart_bytes(
    img_bytes: bytes,
    *,
    timeout: int = 90,
) -> ChartAnalysisResult:
    """Send img_bytes to Gemini Vision and return a ChartAnalysisResult."""
    mime = "image/jpeg"
    if img_bytes[:4] == b"\x89PNG":
        mime = "image/png"

    b64_image = base64.b64encode(img_bytes).decode()
    api_key = _get_api_key()

    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": b64_image}},
                    {"text": _PROMPT},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
        },
    }

    logger.info("Sending chart to Gemini Vision (pro analysis)…")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            _GEMINI_URL,
            params={"key": api_key},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Gemini API error {resp.status}: {body[:300]}")
            data = await resp.json()

    # Extract text — gemini-2.5-flash may include a "thought" part before the
    # actual JSON output, so scan all parts for the one that contains JSON.
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
    json_text = re.sub(r"\n?```$", "", json_text)

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

    return ChartAnalysisResult(
        bias=str(parsed.get("bias", "NEUTRAL")).upper(),
        trend=str(parsed.get("trend", "SIDEWAYS")).upper(),
        market_structure=str(parsed.get("market_structure", "RANGING")).upper(),
        timeframe=str(parsed.get("timeframe", "Unknown")),
        confidence=int(parsed.get("confidence", 50)),
        win_probability=int(parsed.get("win_probability", 50)),
        chart_patterns=_sl("chart_patterns"),
        candlestick_pattern=str(parsed.get("candlestick_pattern", "None")),
        momentum=str(parsed.get("momentum", "MODERATE")).upper(),
        key_support=_fl("key_support"),
        key_resistance=_fl("key_resistance"),
        order_block=_f("order_block"),
        fair_value_gap=_f("fair_value_gap"),
        entry_type=str(parsed.get("entry_type", "WAIT")).upper(),
        entry=_f("entry"),
        stop_loss=_f("stop_loss"),
        take_profit_1=_f("take_profit_1"),
        take_profit_2=_f("take_profit_2"),
        take_profit_3=_f("take_profit_3"),
        invalidation=_f("invalidation"),
        rr_ratio=_f("rr_ratio"),
        confluence_factors=_sl("confluence_factors"),
        early_entry_reason=str(parsed.get("early_entry_reason", "")),
        summary=str(parsed.get("summary", "")),
        raw=parsed,
    )
