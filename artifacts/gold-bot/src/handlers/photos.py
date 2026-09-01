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


def _wrap(text: str, width: int = 34, indent: str = "    ") -> list[str]:
    """Word-wrap text to fit within width, returning indented lines."""
    words = str(text).split()
    buf, rows = [], []
    for w in words:
        if sum(len(x) + 1 for x in buf) + len(w) > width:
            rows.append(indent + _esc(" ".join(buf)))
            buf = [w]
        else:
            buf.append(w)
    if buf:
        rows.append(indent + _esc(" ".join(buf)))
    return rows


def _result_card(r: ChartAnalysisResult) -> str:
    SEP  = "──────────────────────────────────"
    WIDE = "╔══════════════════════════════════╗"

    ms_map = {
        "HH_HL":      "HH / HL — Bullish structure",
        "LH_LL":      "LH / LL — Bearish structure",
        "RANGING":    "Ranging — no clear structure",
        "TRANSITION": "Structure break in progress",
    }.get(r.market_structure, r.market_structure)

    htf_label = {"BULLISH": "Bullish ↑", "BEARISH": "Bearish ↓", "NEUTRAL": "Neutral —"}.get(r.htf_trend, r.htf_trend)
    ltf_label = {"BULLISH": "Bullish ↑", "BEARISH": "Bearish ↓", "NEUTRAL": "Neutral —"}.get(r.ltf_trend, r.ltf_trend)
    adv_label = {"BUYERS": "Buyers ↑", "SELLERS": "Sellers ↓", "NEUTRAL": "Neutral"}.get(r.pressure_advantage, r.pressure_advantage)

    # Probability bars
    bp = r.bullish_probability
    br = r.bearish_probability
    bull_bar = "█" * round(bp / 10) + "░" * (10 - round(bp / 10))
    bear_bar = "█" * round(br / 10) + "░" * (10 - round(br / 10))
    conf_bar = "█" * round(r.confidence / 10) + "░" * (10 - round(r.confidence / 10))

    lines = [
        "<pre>",
        WIDE,
        "║  XAU/USD  INSTITUTIONAL ANALYSIS ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Timeframe  : {_esc(r.timeframe)}",
        "",
        SEP,
        "  TREND",
        SEP,
        f"  HTF (H4/D1): {htf_label}",
        f"  LTF (H1-M15): {ltf_label}",
        "",
        SEP,
        "  MARKET STRUCTURE",
        SEP,
        f"  {_esc(ms_map)}",
    ]

    # Structure events
    events = []
    if r.bos_detected:
        events.append("✓ BOS detected (trend continuation)")
    if r.choch_detected:
        events.append("✓ CHoCH detected (potential reversal)")
    if r.liquidity_sweep:
        events.append(f"✓ Liq sweep: {_esc(r.liquidity_sweep[:40])}")
    for ev in events:
        lines.append(f"  {ev}")

    # Key levels
    lines += ["", SEP, "  KEY LEVELS", SEP]
    if r.key_resistance:
        lines.append("  Resistance:")
        for v in r.key_resistance:
            lines.append(f"    R: {v:,.2f}")
    if r.key_support:
        lines.append("  Support:")
        for v in r.key_support:
            lines.append(f"    S: {v:,.2f}")
    if r.order_block:
        lines.append(f"  Order Block : {r.order_block:,.2f}")
    if r.fair_value_gap:
        lines.append(f"  Fair Value Gap: {r.fair_value_gap:,.2f}")

    # Patterns
    if r.chart_patterns or (r.candlestick_pattern and r.candlestick_pattern.lower() not in ("none", "")):
        lines += ["", SEP, "  PATTERNS", SEP]
        for p in r.chart_patterns:
            lines.append(f"  • {_esc(p)}")
        if r.candlestick_pattern and r.candlestick_pattern.lower() not in ("none", ""):
            lines.append(f"  Candle: {_esc(r.candlestick_pattern)}")

    # Candlestick behaviour
    if r.candlestick_behavior:
        lines += ["", SEP, "  CANDLESTICK BEHAVIOUR", SEP]
        lines += _wrap(r.candlestick_behavior, width=34, indent="  ")

    # Pressure
    lines += ["", SEP, "  BUYING vs SELLING PRESSURE", SEP]
    if r.buying_pressure:
        lines.append("  Buying:")
        lines += _wrap(r.buying_pressure, width=32, indent="    ")
    if r.selling_pressure:
        lines.append("  Selling:")
        lines += _wrap(r.selling_pressure, width=32, indent="    ")
    lines.append(f"  Advantage  : {adv_label}")

    # Probability & confidence
    lines += [
        "",
        SEP,
        "  PROBABILITY",
        SEP,
        f"  Bullish [{bull_bar}] {bp}%",
        f"  Bearish [{bear_bar}] {br}%",
        f"  Confidence [{conf_bar}] {r.confidence}%",
    ]

    # Reasons for bias
    if r.reasons:
        lines += ["", "  Reasons:"]
        for reason in r.reasons:
            lines += _wrap(reason, width=32, indent="    • ")

    # Trade setup
    lines += ["", SEP, "  TRADE SETUP", SEP]
    has_trade = r.entry_type not in ("WAIT", "") and r.entry is not None
    if has_trade:
        rr_str = f"1:{r.rr_ratio:.1f}" if r.rr_ratio else "N/A"
        lines += [
            f"  Type     : {_esc(r.entry_type.replace('_', ' '))}",
            f"  Quality  : {_esc(r.trade_quality)}",
            f"  Risk     : {_esc(r.risk_level)}",
            f"  R:R      : {rr_str}",
            "",
            f"  Entry    : {r.entry:,.2f}",
        ]
        if r.stop_loss:
            lines.append(f"  Stop Loss: {r.stop_loss:,.2f}")
        if r.take_profit_1:
            lines.append(f"  TP1      : {r.take_profit_1:,.2f}")
        if r.take_profit_2:
            lines.append(f"  TP2      : {r.take_profit_2:,.2f}")
        if r.take_profit_3:
            lines.append(f"  TP3      : {r.take_profit_3:,.2f}")
        if r.invalidation:
            lines.append(f"  Invalidat: {r.invalidation:,.2f}")
        if r.early_entry_reason:
            lines += ["", "  Entry logic:"]
            lines += _wrap(r.early_entry_reason, width=32, indent="    ")
    else:
        lines += [
            "  WAIT — No high-probability setup",
            "  at current price action.",
        ]
        if r.early_entry_reason:
            lines += _wrap(r.early_entry_reason, width=34, indent="  ")

    # Confluence
    if r.confluence_factors:
        lines += ["", SEP, f"  CONFLUENCE  ({len(r.confluence_factors)} factors)", SEP]
        for f_ in r.confluence_factors:
            lines.append(f"  + {_esc(f_)}")

    # Open trade section
    if r.open_trade_valid is not None:
        valid_str = "✅ STILL VALID" if r.open_trade_valid else "⚠️  INVALIDATED"
        lines += ["", SEP, f"  OPEN TRADE: {valid_str}", SEP]
        if r.open_trade_notes:
            lines += _wrap(r.open_trade_notes, width=34, indent="  ")

    # Final summary
    lines += ["", SEP, "  FINAL SUMMARY", SEP]
    if r.summary:
        lines += _wrap(r.summary, width=34, indent="  ")

    # Scenarios
    lines += ["", "  Bullish scenario:"]
    lines += _wrap(r.bullish_scenario or "N/A", width=32, indent="    ")
    lines += ["", "  Bearish scenario:"]
    lines += _wrap(r.bearish_scenario or "N/A", width=32, indent="    ")

    # Risk Assessment block
    lines += [
        "",
        SEP,
        "  RISK ASSESSMENT",
        SEP,
        f"  Bias       : {_esc(r.bias.capitalize())}",
        f"  Bullish    : {bp}%   Bearish: {br}%",
    ]
    if r.key_resistance:
        lines.append(f"  Key R      : {r.key_resistance[0]:,.2f}")
    if r.key_support:
        lines.append(f"  Key S      : {r.key_support[0]:,.2f}")
    lines += [
        f"  Trade Qual : {_esc(r.trade_quality if has_trade else 'N/A')}",
        f"  Risk Level : {_esc(r.risk_level if has_trade else 'N/A')}",
        "",
        SEP,
        "  ⚠️  This analysis is based solely on",
        "  current price action and cannot",
        "  guarantee future market movement.",
        "  Always use proper risk management",
        "  and stop losses.",
        "</pre>",
    ]

    from src.utils.formatting import safe_html
    return safe_html("\n".join(lines))


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

        # Pass the most recent open trade so Gemini can assess its validity
        open_trade_ctx = None
        try:
            from src import trade_tracker
            open_trades = trade_tracker.get_active_trades(update.effective_chat.id)
            if open_trades:
                # Pick the most recent open trade
                open_trade_ctx = max(open_trades, key=lambda t: t.get("opened_at", 0))
        except Exception:
            pass

        result = await analyse_chart_bytes(img_bytes, open_trade=open_trade_ctx)

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
