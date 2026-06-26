"""
Google Gemini Vision — chart screenshot analysis for XAU/USD.

Calls the Gemini REST API directly via aiohttp (no extra packages needed —
aiohttp is already used by the bot). Accepts raw image bytes and returns a
ChartAnalysisResult.
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
    bias: str                          # "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING"
    confidence: int                    # 0-100
    pattern: str                       # e.g. "Descending Channel"
    candlestick: str                   # e.g. "Doji" or "None"
    trend: str                         # "UPTREND" | "DOWNTREND" | "SIDEWAYS"
    key_support: list[float]
    key_resistance: list[float]
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    timeframe: str
    summary: str
    raw: dict = field(default_factory=dict, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT = """\
This is a XAU/USD (Gold vs US Dollar) candlestick chart screenshot from a trading platform (such as MetaTrader 5).
Analyse it as an expert gold technical analyst.

Return ONLY a single valid JSON object — no markdown fences, no extra text — with exactly these keys:

{
  "bias":           "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING",
  "confidence":     <integer 0-100>,
  "trend":          "UPTREND" | "DOWNTREND" | "SIDEWAYS",
  "pattern":        "<chart pattern, e.g. Descending Channel, Bull Flag, Double Top, or None>",
  "candlestick":    "<most recent candle pattern, e.g. Doji, Hammer, Engulfing, or None>",
  "timeframe":      "<timeframe label on chart, e.g. M15, H1, H4, D1, or Unknown>",
  "key_support":    [<up to 3 support levels as floats — read directly from the Y-axis>],
  "key_resistance": [<up to 3 resistance levels as floats — read directly from the Y-axis>],
  "entry":          <suggested entry price as float, or null>,
  "stop_loss":      <suggested stop loss price as float, or null>,
  "take_profit_1":  <first take profit as float, or null>,
  "take_profit_2":  <second take profit as float, or null>,
  "summary":        "<2-3 sentence plain-English technical assessment>"
}

Rules:
- Read all price values from the chart's Y-axis. Gold (XAU/USD) currently trades around 3500-4200 — use whatever prices appear on the chart.
- Read the timeframe from the chart label (e.g. "H1" shown in the toolbar).
- Use null or [] when a field is genuinely unclear rather than guessing.
- Output ONLY the JSON object. No explanation, no markdown.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────────────────────────────────────

async def analyse_chart_bytes(
    img_bytes: bytes,
    *,
    timeout: int = 60,
) -> ChartAnalysisResult:
    """Send *img_bytes* to Gemini Vision and return a ChartAnalysisResult."""
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
            "maxOutputTokens": 800,
        },
    }

    logger.info("Sending chart to Gemini Vision…")
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

    # Extract text from response
    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response shape: {data}") from e

    logger.info(f"Gemini raw response: {raw_text[:300]}")

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

    return ChartAnalysisResult(
        bias=str(parsed.get("bias", "NEUTRAL")).upper(),
        confidence=int(parsed.get("confidence", 50)),
        trend=str(parsed.get("trend", "SIDEWAYS")).upper(),
        pattern=str(parsed.get("pattern", "None")),
        candlestick=str(parsed.get("candlestick", "None")),
        timeframe=str(parsed.get("timeframe", "Unknown")),
        key_support=_fl("key_support"),
        key_resistance=_fl("key_resistance"),
        entry=_f("entry"),
        stop_loss=_f("stop_loss"),
        take_profit_1=_f("take_profit_1"),
        take_profit_2=_f("take_profit_2"),
        summary=str(parsed.get("summary", "")),
        raw=parsed,
    )
