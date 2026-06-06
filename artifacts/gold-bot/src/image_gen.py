"""
Generates WIN and LOSS result images using Pillow.
Returns a bytes buffer (PNG) ready to be sent as a Telegram photo.
"""
import io
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont

FONT_MONO_PATH      = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# Palette
BG          = "#0A0C10"
PANEL       = "#111318"
GOLD        = "#C9A84C"
WHITE       = "#E8E8E8"
MUTED       = "#6B7280"
WIN_GREEN   = "#22C55E"
LOSS_RED    = "#EF4444"
ACCENT_LINE = "#1E2228"

W, H = 900, 500


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO_BOLD_PATH if bold else FONT_MONO_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_base(draw: ImageDraw.ImageDraw) -> None:
    """Draw dark background with subtle grid."""
    draw.rectangle([(0, 0), (W, H)], fill=BG)
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill="#12141A", width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill="#12141A", width=1)


def _draw_top_bar(draw: ImageDraw.ImageDraw, color: str) -> None:
    draw.rectangle([(0, 0), (W, 5)], fill=color)


def _draw_panel(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int) -> None:
    draw.rectangle([(x1, y1), (x2, y2)], fill=PANEL, outline=ACCENT_LINE, width=1)


def _price_bar(
    draw: ImageDraw.ImageDraw,
    direction: str,
    entry: float,
    exit_price: float,
    sl: float,
    tp1: float,
    tp2: float,
    x: int, y: int, bw: int, bh: int,
    accent: str,
) -> None:
    """Draw a compact horizontal price journey bar."""
    prices = sorted([entry, exit_price, sl, tp1, tp2])
    lo, hi = prices[0], prices[-1]
    span = hi - lo or 1.0

    def px(price: float) -> int:
        return x + int((price - lo) / span * bw)

    # Base track
    draw.rectangle([(x, y + bh // 2 - 2), (x + bw, y + bh // 2 + 2)], fill="#1E2228")

    # Entry → exit fill
    ex = min(px(entry), px(exit_price))
    ey = max(px(entry), px(exit_price))
    draw.rectangle([(ex, y + bh // 2 - 4), (ey, y + bh // 2 + 4)], fill=accent)

    # Marker function
    def marker(price: float, label: str, color: str, up: bool = True) -> None:
        mx = px(price)
        draw.line([(mx, y), (mx, y + bh)], fill=color, width=2)
        lx = mx - 2
        ly = y - 14 if up else y + bh + 2
        draw.text((lx, ly), label, font=_font(10, False), fill=color, anchor="lm")

    marker(entry,      "ENTRY", WHITE,      up=True)
    marker(sl,         "SL",    LOSS_RED,   up=False)
    marker(tp1,        "TP1",   WIN_GREEN,  up=True)
    marker(tp2,        "TP2",   WIN_GREEN,  up=False)
    marker(exit_price, "EXIT",  accent,     up=True)


def generate_result_image(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    exit_price: float,
    result: str,       # "WIN_TP1" | "WIN_TP2" | "LOSS"
    confidence: int,
    timeframe: str,
    rr_ratio: float,
) -> bytes:
    """Returns PNG bytes."""
    is_win  = result.startswith("WIN")
    accent  = WIN_GREEN if is_win else LOSS_RED
    label   = "TRADE WIN" if is_win else "TRADE LOSS"
    sub     = ("TP1 HIT" if result == "WIN_TP1" else "ALL TARGETS HIT") if is_win else "STOP LOSS HIT"
    pnl     = abs(entry - exit_price)
    pnl_dir = "+" if is_win else "-"

    img  = Image.new("RGB", (W, H), color=BG)
    draw = ImageDraw.Draw(img)

    _draw_base(draw)
    _draw_top_bar(draw, accent)

    # ── Header area ──────────────────────────────────
    f_xs  = _font(11)
    f_sm  = _font(14)
    f_md  = _font(18)
    f_lg  = _font(36, bold=True)
    f_hd  = _font(24, bold=True)
    f_sub = _font(13)

    # Symbol
    draw.text((40, 20), "XAU / USD", font=f_md, fill=GOLD)
    # Timestamp
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
    draw.text((W - 40, 20), ts, font=f_xs, fill=MUTED, anchor="ra")

    # Main label
    draw.text((40, 60), label, font=f_lg, fill=accent)
    # Sub-label
    draw.text((40, 108), sub, font=f_hd, fill=WHITE)

    # Separator
    draw.line([(40, 145), (W - 40, 145)], fill=ACCENT_LINE, width=1)

    # ── Stats panel ──────────────────────────────────
    _draw_panel(draw, 40, 155, W - 40, 340)

    col1_x, col2_x = 60, 370
    col3_x, col4_x = 530, 750

    rows_left = [
        ("Direction", direction),
        ("Entry",     f"{entry:,.2f}"),
        ("TP1",       f"{tp1:,.2f}"),
        ("TP2",       f"{tp2:,.2f}"),
    ]
    rows_right = [
        ("SL",         f"{sl:,.2f}"),
        ("Exit Price", f"{exit_price:,.2f}"),
        ("Timeframe",  timeframe),
        ("Confidence", f"{confidence}%"),
    ]

    y_row = 168
    row_h = 38
    for i, (key, val) in enumerate(rows_left):
        ry = y_row + i * row_h
        draw.text((col1_x, ry), key, font=f_sub, fill=MUTED)
        color = accent if (key in ("TP1", "TP2") and is_win) or (key == "SL" and not is_win) else WHITE
        draw.text((col2_x, ry), val, font=_font(14, bold=True), fill=color, anchor="ra")

    for i, (key, val) in enumerate(rows_right):
        ry = y_row + i * row_h
        draw.text((col3_x, ry), key, font=f_sub, fill=MUTED)
        draw.text((W - 60, ry), val, font=_font(14, bold=True), fill=WHITE, anchor="ra")

    # ── Price bar ────────────────────────────────────
    draw.line([(40, 345), (W - 40, 345)], fill=ACCENT_LINE, width=1)
    _price_bar(draw, direction, entry, exit_price, sl, tp1, tp2,
               55, 360, W - 110, 40, accent)

    # ── P&L footer ───────────────────────────────────
    draw.line([(40, 415), (W - 40, 415)], fill=ACCENT_LINE, width=1)

    pnl_text = f"P&L  {pnl_dir}{pnl:,.2f} pts  |  R:R  1:{rr_ratio}"
    draw.text((40, 428), pnl_text, font=_font(16, bold=True), fill=accent)

    # Watermark
    draw.text((W - 40, 470), "XAU/USD Bot  |  For analysis only",
              font=f_xs, fill=MUTED, anchor="ra")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()
