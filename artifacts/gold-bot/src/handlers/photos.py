"""
Photo message handler — receives a chart screenshot, analyses it with GPT-4o
Vision, annotates the image with a Pillow overlay, and replies with:
  1. The annotated JPEG
  2. A structured text summary

Design decisions:
- Image bytes are downloaded ONCE using python-telegram-bot's native API
  (no raw aiohttp, no external URL fetch).
- status_msg is never deleted mid-flight; it is edited to show the final
  error if anything goes wrong, so the user always gets feedback.
- All model-derived strings are HTML-escaped before insertion into cards.
"""
from __future__ import annotations

import html
import io
import logging

from telegram import Update, InputFile
from telegram.ext import ContextTypes, MessageHandler, filters, Application

from src.chart_analysis import analyse_chart_bytes, ChartAnalysisResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Text card formatter
# ─────────────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """HTML-escape a model-derived string."""
    return html.escape(str(text))


def _result_card(r: ChartAnalysisResult) -> str:
    bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪", "RANGING": "🟡"}
    trend_emoji = {"UPTREND": "📈", "DOWNTREND": "📉", "SIDEWAYS": "➡️"}

    be = bias_emoji.get(r.bias, "⚪")
    te = trend_emoji.get(r.trend, "➡️")

    lines = [
        "┌─────────────────────────────┐",
        "│  📸  CHART VISION ANALYSIS  │",
        "└─────────────────────────────┘",
        "",
        f"{be} <b>Bias:</b> {_esc(r.bias)}   {te} <b>Trend:</b> {_esc(r.trend)}",
        f"🎯 <b>Confidence:</b> {r.confidence}%   🕒 <b>TF:</b> {_esc(r.timeframe)}",
        "",
    ]

    if r.pattern and r.pattern.lower() != "none":
        lines.append(f"📐 <b>Pattern:</b> {_esc(r.pattern)}")
    if r.candlestick and r.candlestick.lower() != "none":
        lines.append(f"🕯 <b>Candle:</b> {_esc(r.candlestick)}")

    if r.key_support or r.key_resistance:
        lines.append("")
        if r.key_support:
            sup_str = "  |  ".join(f"{v:.2f}" for v in r.key_support)
            lines.append(f"🔵 <b>Support:</b>    {sup_str}")
        if r.key_resistance:
            res_str = "  |  ".join(f"{v:.2f}" for v in r.key_resistance)
            lines.append(f"🔴 <b>Resistance:</b> {res_str}")

    has_trade = any(v is not None for v in [r.entry, r.stop_loss, r.take_profit_1])
    if has_trade:
        lines.append("")
        lines.append("──────────────────────────────")
        lines.append("📊 <b>TRADE LEVELS</b>")
        if r.entry:
            lines.append(f"  Entry:  <b>{r.entry:.2f}</b>")
        if r.stop_loss:
            lines.append(f"  SL:     <b>{r.stop_loss:.2f}</b>  🛡")
        if r.take_profit_1:
            lines.append(f"  TP1:    <b>{r.take_profit_1:.2f}</b>  🎯")
        if r.take_profit_2:
            lines.append(f"  TP2:    <b>{r.take_profit_2:.2f}</b>  🎯")
        lines.append("──────────────────────────────")

    if r.summary:
        lines.append("")
        lines.append(f"📝 {_esc(r.summary)}")

    lines.append("")
    lines.append("<i>Analysis powered by GPT-4o Vision.</i>")
    lines.append("<i>Not financial advice — always use your own judgement.</i>")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Handler
# ─────────────────────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an incoming photo message containing a chart screenshot."""
    message = update.message
    if not message or not message.photo:
        return

    # Acknowledge immediately so the user knows we're working.
    # status_msg stays alive until the very end — editable for error fallback.
    status_msg = await message.reply_text(
        "📸 <b>Chart received!</b>\n\nAnalysing with AI vision… this may take 15–30 seconds.",
        parse_mode="HTML",
    )

    try:
        # ── 1. Download image bytes via PTB (one round-trip, no raw URL fetch) ──
        photo = max(message.photo, key=lambda p: p.file_size or 0)
        tg_file = await context.bot.get_file(photo.file_id)
        img_bytes = bytes(await tg_file.download_as_bytearray())
        logger.info(f"Downloaded chart photo — {len(img_bytes):,} bytes")

        # ── 2. Run GPT-4o vision analysis ──────────────────────────────────────
        result = await analyse_chart_bytes(img_bytes)

        # ── 3. Annotate the image (Pillow overlay) ─────────────────────────────
        from src.chart_annotator import annotate_chart
        annotated_bytes = annotate_chart(img_bytes, result)

        # ── 4. Send annotated image ─────────────────────────────────────────────
        await message.reply_photo(
            photo=InputFile(io.BytesIO(annotated_bytes), filename="gold_analysis.jpg"),
            caption="📊 <b>XAU/USD Chart Analysis</b>",
            parse_mode="HTML",
        )

        # ── 5. Send text summary ────────────────────────────────────────────────
        await message.reply_text(_result_card(result), parse_mode="HTML")

        # ── 6. Delete the "Analysing…" status message now that all sends worked ─
        try:
            await status_msg.delete()
        except Exception:
            pass  # Non-critical — ignore if already gone

    except Exception as e:
        logger.error(f"Photo analysis failed: {e}", exc_info=True)
        # Edit (not delete) the status message so the user still gets feedback
        try:
            await status_msg.edit_text(
                "⚠️ <b>Analysis failed.</b>\n\n"
                "Please send a clear XAU/USD chart screenshot and try again.\n"
                f"<i>Error: {html.escape(type(e).__name__)}</i>",
                parse_mode="HTML",
            )
        except Exception:
            # Last resort — reply fresh if status_msg is already gone
            await message.reply_text(
                "⚠️ Analysis failed. Please try again with a clear chart screenshot.",
            )


def register_photo_handlers(app: Application) -> None:
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
