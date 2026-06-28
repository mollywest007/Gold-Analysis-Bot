"""
Photo message handler — receives a chart screenshot, analyses it with Gemini Vision,
and replies with a professional institutional-grade analysis card.
"""
from __future__ import annotations

import html
import io
import logging

from telegram import Update, InputFile
from telegram.ext import ContextTypes, MessageHandler, filters, Application

from src.chart_analysis import analyse_chart_bytes, ChartAnalysisResult

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return html.escape(str(text))


def _result_card(r: ChartAnalysisResult) -> str:
    lines = []

    # ── Header ───────────────────────────────────────────────────────────────
    lines += [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  CHART VISION ANALYSIS ║",
        "╚══════════════════════════════════╝",
        "",
    ]

    # ── Structure & Bias ─────────────────────────────────────────────────────
    bias_label  = {"BULLISH": "BULLISH", "BEARISH": "BEARISH", "NEUTRAL": "NEUTRAL", "RANGING": "RANGING"}.get(r.bias, r.bias)
    trend_label = {"UPTREND": "UPTREND", "DOWNTREND": "DOWNTREND", "SIDEWAYS": "SIDEWAYS"}.get(r.trend, r.trend)
    ms_map      = {"HH_HL": "HH / HL  (Bullish)", "LH_LL": "LH / LL  (Bearish)", "RANGING": "Ranging", "TRANSITION": "Structure Break"}.get(r.market_structure, r.market_structure)
    mom_map     = {"STRONG": "Strong", "MODERATE": "Moderate", "WEAK": "Weak", "DIVERGING": "Diverging"}.get(r.momentum, r.momentum)

    lines += [
        f"  TF       : {_esc(r.timeframe)}",
        f"  Bias     : {_esc(bias_label)}",
        f"  Trend    : {_esc(trend_label)}",
        f"  Structure: {_esc(ms_map)}",
        f"  Momentum : {_esc(mom_map)}",
        "",
    ]

    # ── Win Probability Bar ───────────────────────────────────────────────────
    filled = round(r.win_probability / 10)
    bar    = "█" * filled + "░" * (10 - filled)
    lines += [
        "──────────────────────────────────",
        f"  Win Rate : [{bar}] {r.win_probability}%",
        f"  Confidence: {r.confidence}%",
        "──────────────────────────────────",
        "",
    ]

    # ── Patterns ─────────────────────────────────────────────────────────────
    if r.chart_patterns:
        lines.append("  PATTERNS DETECTED:")
        for p in r.chart_patterns:
            lines.append(f"    - {_esc(p)}")
    if r.candlestick_pattern and r.candlestick_pattern.lower() not in ("none", ""):
        lines.append(f"  Candle   : {_esc(r.candlestick_pattern)}")
    lines.append("")

    # ── Key Levels ───────────────────────────────────────────────────────────
    lines.append("  KEY LEVELS:")
    if r.key_resistance:
        res_str = "  |  ".join(f"{v:.2f}" for v in r.key_resistance)
        lines.append(f"    Resistance: {res_str}")
    if r.key_support:
        sup_str = "  |  ".join(f"{v:.2f}" for v in r.key_support)
        lines.append(f"    Support   : {sup_str}")
    if r.order_block:
        lines.append(f"    Order Blk : {r.order_block:.2f}")
    if r.fair_value_gap:
        lines.append(f"    FVG       : {r.fair_value_gap:.2f}")
    lines.append("")

    # ── Trade Setup ──────────────────────────────────────────────────────────
    has_trade = r.entry_type not in ("WAIT", "") and r.entry is not None
    lines.append("──────────────────────────────────")
    if has_trade:
        rr_str = f"{r.rr_ratio:.1f}:1" if r.rr_ratio else "N/A"
        lines += [
            f"  SETUP    : {_esc(r.entry_type.replace('_', ' '))}",
            f"  R:R      : {rr_str}",
            "",
            f"  Entry    : {r.entry:.2f}",
        ]
        if r.stop_loss:
            lines.append(f"  Stop Loss: {r.stop_loss:.2f}")
        if r.take_profit_1:
            lines.append(f"  TP 1     : {r.take_profit_1:.2f}")
        if r.take_profit_2:
            lines.append(f"  TP 2     : {r.take_profit_2:.2f}")
        if r.take_profit_3:
            lines.append(f"  TP 3     : {r.take_profit_3:.2f}")
        if r.invalidation:
            lines.append(f"  Invalidat: {r.invalidation:.2f}")
    else:
        lines.append("  SETUP    : WAIT — No high-prob setup")
    lines.append("──────────────────────────────────")
    lines.append("")

    # ── Confluence Factors ───────────────────────────────────────────────────
    if r.confluence_factors:
        lines.append("  CONFLUENCE:")
        for f_ in r.confluence_factors:
            lines.append(f"    + {_esc(f_)}")
        lines.append("")

    # ── Early Entry Reason ───────────────────────────────────────────────────
    if r.early_entry_reason:
        lines.append("  ENTRY LOGIC:")
        # Word-wrap at ~36 chars
        words = r.early_entry_reason.split()
        line_buf, row_lines = [], []
        for w in words:
            if sum(len(x) + 1 for x in line_buf) + len(w) > 34:
                row_lines.append("    " + _esc(" ".join(line_buf)))
                line_buf = [w]
            else:
                line_buf.append(w)
        if line_buf:
            row_lines.append("    " + _esc(" ".join(line_buf)))
        lines += row_lines
        lines.append("")

    # ── Summary ──────────────────────────────────────────────────────────────
    if r.summary:
        lines.append("  ASSESSMENT:")
        words = r.summary.split()
        line_buf, row_lines = [], []
        for w in words:
            if sum(len(x) + 1 for x in line_buf) + len(w) > 34:
                row_lines.append("    " + _esc(" ".join(line_buf)))
                line_buf = [w]
            else:
                line_buf.append(w)
        if line_buf:
            row_lines.append("    " + _esc(" ".join(line_buf)))
        lines += row_lines
        lines.append("")

    lines += [
        "  Not financial advice.",
        "</pre>",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Handler
# ─────────────────────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an incoming photo message containing a chart screenshot."""
    message = update.message
    if not message or not message.photo:
        return

    status_msg = await message.reply_text(
        "Chart received. Running professional analysis... this takes 20-40 seconds.",
    )

    try:
        photo    = max(message.photo, key=lambda p: p.file_size or 0)
        tg_file  = await context.bot.get_file(photo.file_id)
        img_bytes = bytes(await tg_file.download_as_bytearray())
        logger.info(f"Downloaded chart photo — {len(img_bytes):,} bytes")

        result = await analyse_chart_bytes(img_bytes)

        await message.reply_text(_result_card(result), parse_mode="HTML")

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Photo analysis failed: {e}", exc_info=True)
        try:
            await status_msg.edit_text(
                f"Analysis failed. Please try again with a clear chart screenshot.\n"
                f"Error: {html.escape(type(e).__name__)}",
            )
        except Exception:
            await message.reply_text("Analysis failed. Please try again.")


def register_photo_handlers(app: Application) -> None:
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
