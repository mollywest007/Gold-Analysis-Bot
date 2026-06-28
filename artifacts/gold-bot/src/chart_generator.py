"""
Professional XAU/USD chart generator.

Layout (top to bottom):
  - Main panel  : Candlesticks + EMA9/21/50/200 + Bollinger Bands + swing H/L markers
                  + optional trade levels (Entry, SL, TP1, TP2, TP3)
  - Volume panel: Colour-coded volume bars
  - RSI panel   : RSI(14) with 70/50/30 reference lines

Returns raw JPEG bytes ready to pass to Gemini Vision or send via Telegram.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import mplfinance as mpf

from src.analysis.market_data import fetch_ohlcv

logger = logging.getLogger(__name__)

# ── Colour palette (TradingView Dark clone) ───────────────────────────────────
BG          = "#131722"
GRID        = "#1e222d"
TEXT        = "#b2b5be"
UP          = "#26a69a"
DOWN        = "#ef5350"
EMA9_C      = "#f5a623"
EMA21_C     = "#2196f3"
EMA50_C     = "#9c27b0"
EMA200_C    = "#ff5722"
BB_C        = "#546e7a"
VOL_C       = "#37474f"
RSI_C       = "#e91e63"
RSI_OB      = "#ef5350"
RSI_OS      = "#26a69a"
SWH_C       = "#ef5350"
SWL_C       = "#26a69a"

_CANDLE_COUNT = {
    "M5":  80, "M15": 80, "M30": 80,
    "H1":  80, "H4":  60, "D1":  80,
}


def _tf_to_freq(tf: str) -> str:
    return {"M5": "5min", "M15": "15min", "M30": "30min",
            "H1": "1h",   "H4":  "4h",   "D1":  "1D"}.get(tf, "1h")


# ── Indicators ────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _bollinger(series: pd.Series, window: int = 20, k: float = 2.0):
    mid = series.rolling(window).mean()
    std = series.rolling(window).std()
    return mid - k * std, mid, mid + k * std


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta  = series.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _swing_points(df: pd.DataFrame, lookback: int = 5):
    """Return indices of swing highs and swing lows."""
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(lookback, len(df) - lookback):
        if h[i] == max(h[i - lookback: i + lookback + 1]):
            highs.append(i)
        if l[i] == min(l[i - lookback: i + lookback + 1]):
            lows.append(i)
    return highs, lows


# ── Trade-level overlay colours ───────────────────────────────────────────────
ENTRY_C = "#ffffff"   # white
SL_C    = "#ef5350"   # red
TP1_C   = "#26a69a"   # teal
TP2_C   = "#00e676"   # bright green
TP3_C   = "#69f0ae"   # mint


def _draw_trade_levels(
    ax,
    x_end: int,
    entry:     Optional[float] = None,
    sl:        Optional[float] = None,
    tp1:       Optional[float] = None,
    tp2:       Optional[float] = None,
    tp3:       Optional[float] = None,
    direction: Optional[str]   = None,
) -> None:
    """Overlay horizontal trade-level lines with price labels on the right edge."""

    def _hline(price: float, color: str, label: str, lw: float = 1.2, ls: str = "--") -> None:
        ax.axhline(price, color=color, linewidth=lw, linestyle=ls, alpha=0.9, zorder=6)
        ax.annotate(
            f" {label}  {price:,.2f}",
            xy=(x_end, price),
            xycoords=("data", "data"),
            fontsize=7.5,
            color=color,
            va="center",
            ha="left",
            fontfamily="monospace",
            fontweight="bold",
            zorder=7,
        )

    # Shaded risk/reward zones
    if entry is not None and sl is not None:
        ax.axhspan(min(entry, sl), max(entry, sl),
                   alpha=0.06, color=SL_C, zorder=1)
    if entry is not None and tp1 is not None:
        ax.axhspan(min(entry, tp1), max(entry, tp1),
                   alpha=0.06, color=TP1_C, zorder=1)

    # Lines — draw from furthest target inward so labels don't collide
    if tp3 is not None:   _hline(tp3,   TP3_C,  "TP3", lw=1.0, ls=":")
    if tp2 is not None:   _hline(tp2,   TP2_C,  "TP2", lw=1.1, ls="-.")
    if tp1 is not None:   _hline(tp1,   TP1_C,  "TP1", lw=1.3, ls="--")
    if sl  is not None:   _hline(sl,    SL_C,   "SL",  lw=1.3, ls="--")
    if entry is not None: _hline(entry, ENTRY_C, "ENTRY", lw=1.5, ls="-")


# ── Main chart builder ────────────────────────────────────────────────────────

async def generate_chart_image(
    timeframe:  str            = "H1",
    entry:      Optional[float] = None,
    sl:         Optional[float] = None,
    tp1:        Optional[float] = None,
    tp2:        Optional[float] = None,
    tp3:        Optional[float] = None,
    direction:  Optional[str]   = None,
) -> Optional[bytes]:
    data = await fetch_ohlcv(timeframe)
    if data is None or len(data) < 15:
        logger.error(f"No OHLCV data for {timeframe}")
        return None

    n = _CANDLE_COUNT.get(timeframe, 80)

    # Slice to the most recent N candles
    opens  = np.array(data.opens[-n:],   dtype=float)
    highs  = np.array(data.highs[-n:],   dtype=float)
    lows   = np.array(data.lows[-n:],    dtype=float)
    closes = np.array(data.closes[-n:],  dtype=float)
    vols   = np.array(data.volumes[-n:], dtype=float) if data.volumes else np.zeros(len(closes))

    idx = pd.date_range(end="now", periods=len(closes), freq=_tf_to_freq(timeframe))
    df  = pd.DataFrame({"Open": opens, "High": highs, "Low": lows,
                         "Close": closes, "Volume": vols}, index=idx)

    # ── Compute indicators ────────────────────────────────────────────────────
    e9    = _ema(df["Close"], 9)
    e21   = _ema(df["Close"], 21)
    e50   = _ema(df["Close"], 50)
    e200  = _ema(df["Close"], 200)
    bb_lo, bb_mid, bb_hi = _bollinger(df["Close"])
    rsi   = _rsi(df["Close"])
    swing_hi_idx, swing_lo_idx = _swing_points(df, lookback=4)

    # ── Figure layout ─────────────────────────────────────────────────────────
    # right=0.78 leaves room for the trade-level price labels on the right edge
    has_levels = any(v is not None for v in (entry, sl, tp1, tp2, tp3))
    right_margin = 0.78 if has_levels else 0.93
    fig = plt.figure(figsize=(16, 10), facecolor=BG)
    gs  = GridSpec(3, 1, figure=fig, height_ratios=[5, 1.2, 1.5],
                   hspace=0.04, left=0.04, right=right_margin, top=0.94, bottom=0.05)

    ax_main = fig.add_subplot(gs[0])
    ax_vol  = fig.add_subplot(gs[1], sharex=ax_main)
    ax_rsi  = fig.add_subplot(gs[2], sharex=ax_main)

    x = np.arange(len(df))

    # ── Main panel ────────────────────────────────────────────────────────────
    for ax in (ax_main, ax_vol, ax_rsi):
        ax.set_facecolor(BG)
        ax.tick_params(colors=TEXT, labelsize=7)
        ax.yaxis.set_label_position("right")
        ax.yaxis.tick_right()
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
        ax.grid(color=GRID, linestyle="--", linewidth=0.4, alpha=0.8)

    # Candlesticks
    for i in x:
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        col = UP if c >= o else DOWN
        ax_main.plot([i, i], [l, h], color=col, linewidth=0.8, zorder=2)
        ax_main.add_patch(mpatches.FancyBboxPatch(
            (i - 0.35, min(o, c)), 0.7, abs(c - o) or 0.01 * c,
            boxstyle="square,pad=0", linewidth=0,
            facecolor=col, zorder=3,
        ))

    # EMAs
    ax_main.plot(x, e9,   color=EMA9_C,   linewidth=0.9, label="EMA 9",   zorder=4)
    ax_main.plot(x, e21,  color=EMA21_C,  linewidth=0.9, label="EMA 21",  zorder=4)
    ax_main.plot(x, e50,  color=EMA50_C,  linewidth=1.1, label="EMA 50",  zorder=4)
    if not e200.isna().all():
        ax_main.plot(x, e200, color=EMA200_C, linewidth=1.3, label="EMA 200", zorder=4, linestyle="--")

    # Bollinger Bands
    ax_main.fill_between(x, bb_lo, bb_hi, alpha=0.06, color=BB_C, zorder=1)
    ax_main.plot(x, bb_lo, color=BB_C, linewidth=0.6, linestyle=":", alpha=0.7)
    ax_main.plot(x, bb_hi, color=BB_C, linewidth=0.6, linestyle=":", alpha=0.7)
    ax_main.plot(x, bb_mid, color=BB_C, linewidth=0.5, linestyle="-", alpha=0.5)

    # Swing High / Low markers
    for si in swing_hi_idx:
        ax_main.annotate("H", xy=(si, highs[si]),
                          xytext=(si, highs[si] + (highs.max() - lows.min()) * 0.008),
                          fontsize=6, color=SWH_C, ha="center", va="bottom",
                          fontweight="bold")
    for si in swing_lo_idx:
        ax_main.annotate("L", xy=(si, lows[si]),
                          xytext=(si, lows[si] - (highs.max() - lows.min()) * 0.008),
                          fontsize=6, color=SWL_C, ha="center", va="top",
                          fontweight="bold")

    # Trade levels (Entry / SL / TP1 / TP2 / TP3)
    if has_levels:
        _draw_trade_levels(
            ax_main,
            x_end=len(x) - 0.5,
            entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
            direction=direction,
        )

    # Legend
    ax_main.legend(loc="upper left", fontsize=6.5, framealpha=0.25,
                   facecolor=BG, labelcolor=TEXT, ncol=4)

    # Title with live price
    live_price = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else closes[-1]
    chg        = live_price - prev_close
    chg_pct    = (chg / prev_close) * 100 if prev_close else 0
    chg_col    = UP if chg >= 0 else DOWN
    sign       = "+" if chg >= 0 else ""

    ax_main.set_title(
        f"XAU/USD  {timeframe}     {live_price:.2f}   {sign}{chg:.2f} ({sign}{chg_pct:.2f}%)",
        color=TEXT, fontsize=11, loc="left", pad=6, fontfamily="monospace",
    )
    ax_main.title.set_color(chg_col if chg != 0 else TEXT)

    # Y-axis price labels
    ax_main.set_xlim(-0.5, len(x) - 0.5)
    ax_main.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.2f}"))

    # ── Volume panel ──────────────────────────────────────────────────────────
    vol_colors = [UP if closes[i] >= opens[i] else DOWN for i in x]
    ax_vol.bar(x, vols, color=vol_colors, width=0.7, alpha=0.7)
    ax_vol.set_ylabel("Vol", color=TEXT, fontsize=7, rotation=0, labelpad=24)
    ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, _: f"{v/1e3:.0f}K" if v >= 1000 else f"{v:.0f}"
    ))
    plt.setp(ax_vol.get_xticklabels(), visible=False)

    # ── RSI panel ─────────────────────────────────────────────────────────────
    ax_rsi.plot(x, rsi, color=RSI_C, linewidth=1.0)
    ax_rsi.axhline(70, color=RSI_OB, linewidth=0.6, linestyle="--", alpha=0.7)
    ax_rsi.axhline(50, color=TEXT,   linewidth=0.4, linestyle="--", alpha=0.4)
    ax_rsi.axhline(30, color=RSI_OS, linewidth=0.6, linestyle="--", alpha=0.7)
    ax_rsi.fill_between(x, rsi, 70, where=(rsi >= 70), alpha=0.15, color=RSI_OB)
    ax_rsi.fill_between(x, rsi, 30, where=(rsi <= 30), alpha=0.15, color=RSI_OS)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_yticks([30, 50, 70])
    ax_rsi.set_ylabel("RSI", color=TEXT, fontsize=7, rotation=0, labelpad=24)

    # Current RSI label
    cur_rsi = rsi.iloc[-1]
    ax_rsi.annotate(f"RSI {cur_rsi:.1f}", xy=(x[-1], cur_rsi),
                     xytext=(x[-1] - 3, cur_rsi + 6 if cur_rsi < 60 else cur_rsi - 12),
                     fontsize=7, color=RSI_C)

    # X-axis: show a few date labels
    step    = max(1, len(x) // 8)
    x_ticks = x[::step]
    x_labels = [df.index[i].strftime("%m/%d %H:%M") for i in x_ticks]
    ax_rsi.set_xticks(x_ticks)
    ax_rsi.set_xticklabels(x_labels, rotation=30, ha="right", fontsize=6.5, color=TEXT)

    # ── Save ─────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="jpg", dpi=130, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    image_bytes = buf.read()
    logger.info(f"Pro chart generated — {timeframe} | {len(df)} candles | {len(image_bytes):,} bytes")
    return image_bytes
