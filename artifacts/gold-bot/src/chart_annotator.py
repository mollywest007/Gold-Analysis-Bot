"""
Pillow-based chart image annotator.

Takes the original chart image bytes and a ChartAnalysisResult and returns
a new JPEG image (bytes) with an analysis overlay drawn on top.
"""
from __future__ import annotations

import io
import textwrap
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.chart_analysis import ChartAnalysisResult

# ─────────────────────────────────────────────────────────────────────────────
# Colour palette
# ─────────────────────────────────────────────────────────────────────────────

_BIAS_COLOUR = {
    "BULLISH":  (0,   210,  90),   # green
    "BEARISH":  (220,  50,  50),   # red
    "NEUTRAL":  (180, 180, 180),   # grey
    "RANGING":  (200, 160,   0),   # amber
}
_DEFAULT_COLOUR  = (200, 200, 200)
_BG_PANEL        = (15,  15,  25, 210)   # dark, semi-transparent
_WHITE           = (255, 255, 255)
_LIGHT_GREY      = (180, 180, 180)
_GREEN           = (0,   220,  90)
_RED             = (220,  60,  60)
_YELLOW          = (255, 200,   0)
_CYAN            = (0,   200, 220)
_SUPPORT_COL     = (60,  180, 255, 180)   # blue-ish, semi-transparent
_RESIST_COL      = (255,  80,  80, 180)   # red-ish, semi-transparent


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a built-in font at the requested size (falls back to default)."""
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except (OSError, IOError):
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", size)
        except (OSError, IOError):
            return ImageFont.load_default()


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x: int, y: int,
    text: str,
    fg: tuple,
    bg: tuple,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    padding: int = 10,
) -> int:
    """Draw a rounded-rectangle pill label; return its right edge x."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    rx0, ry0 = x, y
    rx1, ry1 = x + tw + padding * 2, y + th + padding
    draw.rounded_rectangle([rx0, ry0, rx1, ry1], radius=6, fill=bg)
    draw.text((rx0 + padding, ry0 + padding // 2), text, font=font, fill=fg)
    return rx1


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def annotate_chart(
    img_bytes: bytes,
    result: ChartAnalysisResult,
) -> bytes:
    """
    Draw an analysis overlay onto *img_bytes* and return the annotated JPEG.

    Overlay contents:
    - Top bar: bias pill + confidence + timeframe + trend
    - Left panel: pattern / candlestick / key levels
    - Bottom panel: Entry / SL / TP1 / TP2 + summary text
    - Horizontal lines for support/resistance (if price axis is visible)
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    W, H = img.size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bias_col  = _BIAS_COLOUR.get(result.bias, _DEFAULT_COLOUR)
    font_lg   = _load_font(max(16, W // 50))
    font_md   = _load_font(max(13, W // 65))
    font_sm   = _load_font(max(11, W // 80))

    margin = max(10, W // 60)

    # ── 1. Top bar ────────────────────────────────────────────────────────────
    bar_h = max(38, H // 18)
    draw.rectangle([0, 0, W, bar_h], fill=(10, 10, 20, 200))

    x = margin
    # Bias pill
    x = _draw_pill(draw, x, margin // 2, f"  {result.bias}  ", _WHITE, bias_col, font_lg, padding=8)
    x += margin

    # Confidence
    conf_text = f"Confidence: {result.confidence}%"
    draw.text((x, bar_h // 4), conf_text, font=font_md, fill=_LIGHT_GREY)
    bbox = draw.textbbox((0, 0), conf_text, font=font_md)
    x += bbox[2] - bbox[0] + margin * 2

    # Timeframe
    tf_text = f"TF: {result.timeframe}"
    draw.text((x, bar_h // 4), tf_text, font=font_md, fill=_CYAN)
    bbox = draw.textbbox((0, 0), tf_text, font=font_md)
    x += bbox[2] - bbox[0] + margin * 2

    # Trend
    trend_col = _GREEN if result.trend == "UPTREND" else (_RED if result.trend == "DOWNTREND" else _YELLOW)
    trend_arrow = "▲" if result.trend == "UPTREND" else ("▼" if result.trend == "DOWNTREND" else "◆")
    draw.text((x, bar_h // 4), f"{trend_arrow} {result.trend}", font=font_md, fill=trend_col)

    # ── 2. Left info panel ────────────────────────────────────────────────────
    panel_w  = max(160, W // 5)
    panel_top = bar_h + margin
    lines: list[tuple[str, tuple]] = []

    if result.pattern and result.pattern.lower() != "none":
        lines.append(("PATTERN", _YELLOW))
        lines.append((f"  {result.pattern}", _WHITE))
    if result.candlestick and result.candlestick.lower() != "none":
        lines.append(("CANDLE", _YELLOW))
        lines.append((f"  {result.candlestick}", _WHITE))
    if result.key_support:
        lines.append(("SUPPORT", _SUPPORT_COL[:3]))
        for lvl in result.key_support:
            lines.append((f"  {lvl:.2f}", _LIGHT_GREY))
    if result.key_resistance:
        lines.append(("RESIST", _RESIST_COL[:3]))
        for lvl in result.key_resistance:
            lines.append((f"  {lvl:.2f}", _LIGHT_GREY))

    if lines:
        line_h = max(18, H // 35)
        panel_h = len(lines) * line_h + margin * 2
        draw.rounded_rectangle(
            [margin, panel_top, margin + panel_w, panel_top + panel_h],
            radius=8, fill=(10, 10, 20, 190),
        )
        ty = panel_top + margin
        for text_str, col in lines:
            draw.text((margin * 2, ty), text_str, font=font_sm, fill=col)
            ty += line_h

    # ── 3. Bottom panel: Entry / SL / TP + summary ───────────────────────────
    bottom_lines: list[tuple[str, tuple]] = []
    if result.entry:
        bottom_lines.append((f"Entry:  {result.entry:.2f}", _WHITE))
    if result.stop_loss:
        bottom_lines.append((f"SL:     {result.stop_loss:.2f}", _RED))
    if result.take_profit_1:
        bottom_lines.append((f"TP1:    {result.take_profit_1:.2f}", _GREEN))
    if result.take_profit_2:
        bottom_lines.append((f"TP2:    {result.take_profit_2:.2f}", _GREEN))

    # Word-wrap summary
    wrapped_summary: list[str] = []
    if result.summary:
        chars_per_line = max(40, W // (max(7, W // 100)))
        wrapped_summary = textwrap.wrap(result.summary, width=chars_per_line)

    all_bottom = bottom_lines + [("", _LIGHT_GREY)] + [(l, _LIGHT_GREY) for l in wrapped_summary]
    if all_bottom:
        line_h = max(18, H // 35)
        panel_h = len(all_bottom) * line_h + margin * 2
        by0 = H - panel_h - margin
        draw.rounded_rectangle(
            [margin, by0, W - margin, H - margin],
            radius=8, fill=(10, 10, 20, 200),
        )
        ty = by0 + margin
        for text_str, col in all_bottom:
            if text_str:
                draw.text((margin * 2, ty), text_str, font=font_sm, fill=col)
            ty += line_h

    # ── 4. Merge overlay onto image ───────────────────────────────────────────
    img = Image.alpha_composite(img, overlay)
    img_rgb = img.convert("RGB")

    out = io.BytesIO()
    img_rgb.save(out, format="JPEG", quality=92)
    return out.getvalue()
