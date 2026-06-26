"""
Generate a candlestick chart image from live OHLCV data using mplfinance.
Returns raw JPEG bytes ready to pass to Gemini Vision or send via Telegram.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import pandas as pd
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must be set before pyplot import

from src.analysis.market_data import fetch_ohlcv, TF_PARAMS

logger = logging.getLogger(__name__)

# How many candles to show on the chart (keeps it readable)
_CANDLE_COUNT = {
    "M5":  60,
    "M15": 60,
    "M30": 60,
    "H1":  60,
    "H4":  50,
    "D1":  60,
}

_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="#26a69a",
        down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume={"up": "#26a69a", "down": "#ef5350"},
    ),
    facecolor="#131722",
    figcolor="#131722",
    gridcolor="#1e222d",
    gridstyle="--",
    y_on_right=True,
)


async def generate_chart_image(timeframe: str = "H1") -> Optional[bytes]:
    """
    Fetch live OHLCV data and render a candlestick chart.
    Returns JPEG bytes, or None if data is unavailable.
    """
    data = await fetch_ohlcv(timeframe)
    if data is None or len(data) < 10:
        logger.error(f"No OHLCV data available for {timeframe}")
        return None

    n = _CANDLE_COUNT.get(timeframe, 60)
    opens  = data.opens[-n:]
    highs  = data.highs[-n:]
    lows   = data.lows[-n:]
    closes = data.closes[-n:]

    # Build a DatetimeIndex — mplfinance requires it
    idx = pd.date_range(end="now", periods=len(closes), freq=_tf_to_freq(timeframe))

    df = pd.DataFrame({
        "Open":  opens,
        "High":  highs,
        "Low":   lows,
        "Close": closes,
        "Volume": [0] * len(closes),   # volume omitted — keeps chart clean
    }, index=idx)

    # Add 20 and 50 EMA overlays
    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()

    addplots = [
        mpf.make_addplot(ema20, color="#f5a623", width=1.0, label="EMA20"),
        mpf.make_addplot(ema50, color="#9b59b6", width=1.0, label="EMA50"),
    ]

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style=_STYLE,
        addplot=addplots,
        volume=False,
        title=f"  XAU/USD  {timeframe}",
        figsize=(12, 6),
        savefig=dict(fname=buf, format="jpg", dpi=120, bbox_inches="tight"),
        tight_layout=True,
        warn_too_much_data=9999,
    )
    buf.seek(0)
    image_bytes = buf.read()
    logger.info(f"Chart generated — {timeframe} | {len(df)} candles | {len(image_bytes):,} bytes")
    return image_bytes


def _tf_to_freq(timeframe: str) -> str:
    mapping = {
        "M5":  "5min",
        "M15": "15min",
        "M30": "30min",
        "H1":  "1h",
        "H4":  "4h",
        "D1":  "1D",
    }
    return mapping.get(timeframe, "1h")
