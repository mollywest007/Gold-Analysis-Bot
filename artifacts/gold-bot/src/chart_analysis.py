"""
GPT-4o Vision — chart screenshot analysis for XAU/USD.

Accepts raw image bytes, sends them to GPT-4o Vision, and returns a
ChartAnalysisResult.  The caller is responsible for downloading the image
(use python-telegram-bot's built-in download to avoid an extra HTTP round-trip
and to stay inside Telegram's file-URL trust boundary).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GOLD", "")
        if not api_key:
            raise RuntimeError("OpenAI API key is not set (checked OPENAI_API_KEY and GOLD).")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChartAnalysisResult:
    bias: str                          # "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING"
    confidence: int                    # 0-100
    pattern: str                       # detected chart pattern, e.g. "Bull Flag"
    candlestick: str                   # detected candle pattern or "None"
    trend: str                         # "UPTREND" | "DOWNTREND" | "SIDEWAYS"
    key_support: list[float]           # price levels
    key_resistance: list[float]        # price levels
    entry: Optional[float]             # suggested entry price
    stop_loss: Optional[float]         # suggested SL
    take_profit_1: Optional[float]     # TP1
    take_profit_2: Optional[float]     # TP2
    timeframe: str                     # detected or inferred timeframe
    summary: str                       # 2-3 sentence analysis
    raw: dict = field(default_factory=dict, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an expert gold (XAU/USD) technical analyst. "
    "When given a chart screenshot you analyse it thoroughly and respond ONLY with "
    "a single valid JSON object — no markdown, no explanation outside the JSON. "
    "All price levels must be floating-point numbers or null. "
    "Confidence is an integer 0-100."
)

_USER_PROMPT = """\
This is a XAU/USD (Gold vs US Dollar) chart screenshot from a trading platform such as MetaTrader.
Analyse it thoroughly as an expert gold technical analyst and return a JSON object with EXACTLY these keys:

{
  "bias":           "BULLISH" | "BEARISH" | "NEUTRAL" | "RANGING",
  "confidence":     <integer 0-100>,
  "trend":          "UPTREND" | "DOWNTREND" | "SIDEWAYS",
  "pattern":        "<chart pattern name, e.g. Bull Flag, Double Top, Descending Channel, or 'None'>",
  "candlestick":    "<candlestick pattern visible at the most recent candle(s), or 'None'>",
  "timeframe":      "<timeframe shown on the chart, e.g. M15, H1, H4, or 'Unknown'>",
  "key_support":    [<up to 3 support price levels visible on the chart as floats>],
  "key_resistance": [<up to 3 resistance price levels visible on the chart as floats>],
  "entry":          <suggested entry price based on the chart as float, or null>,
  "stop_loss":      <suggested stop loss price as float, or null>,
  "take_profit_1":  <first take profit target as float, or null>,
  "take_profit_2":  <second take profit target as float, or null>,
  "summary":        "<2-3 sentence technical assessment of the chart>"
}

Rules:
- Use the EXACT price values visible on the chart's Y-axis (right side). Gold (XAU/USD) can trade anywhere from 1500 to 5000+ — do not reject any prices in that range.
- Read the timeframe label directly from the chart image (e.g. "H1", "M15").
- If a feature is genuinely unclear, use null or [] rather than guessing.
- Do NOT output any text outside the JSON object.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────────────────────────────────────

async def analyse_chart_bytes(
    img_bytes: bytes,
    *,
    timeout: int = 60,
) -> ChartAnalysisResult:
    """
    Send *img_bytes* (a chart screenshot) to GPT-4o Vision, parse the
    structured response, and return a ChartAnalysisResult.
    """
    # Detect MIME type
    mime = "image/jpeg"
    if img_bytes[:4] == b"\x89PNG":
        mime = "image/png"

    b64_image = base64.b64encode(img_bytes).decode()

    logger.info("Sending chart to GPT-4o Vision…")
    client = _get_client()
    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=800,
        timeout=timeout,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64_image}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": _USER_PROMPT},
                ],
            },
        ],
    )

    raw_text = response.choices[0].message.content or ""
    logger.info(f"GPT-4o raw response: {raw_text[:300]}")

    # Parse JSON — strip any accidental markdown fences
    json_text = raw_text.strip()
    json_text = re.sub(r"^```[a-z]*\n?", "", json_text)
    json_text = re.sub(r"\n?```$", "", json_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        # Try to extract the first {...} block
        m = re.search(r"\{.*\}", json_text, re.DOTALL)
        if m:
            data = json.loads(m.group())
        else:
            raise ValueError(
                f"GPT-4o did not return valid JSON: {e}\n---\n{raw_text}"
            ) from e

    def _f(key: str) -> Optional[float]:
        v = data.get(key)
        return float(v) if v is not None else None

    def _fl(key: str) -> list[float]:
        raw = data.get(key, [])
        if not isinstance(raw, list):
            return []
        return [float(x) for x in raw if x is not None]

    return ChartAnalysisResult(
        bias=str(data.get("bias", "NEUTRAL")).upper(),
        confidence=int(data.get("confidence", 50)),
        trend=str(data.get("trend", "SIDEWAYS")).upper(),
        pattern=str(data.get("pattern", "None")),
        candlestick=str(data.get("candlestick", "None")),
        timeframe=str(data.get("timeframe", "Unknown")),
        key_support=_fl("key_support"),
        key_resistance=_fl("key_resistance"),
        entry=_f("entry"),
        stop_loss=_f("stop_loss"),
        take_profit_1=_f("take_profit_1"),
        take_profit_2=_f("take_profit_2"),
        summary=str(data.get("summary", "")),
        raw=data,
    )
