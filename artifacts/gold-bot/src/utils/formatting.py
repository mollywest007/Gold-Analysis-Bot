import html
import time
from src.analysis.engine import MarketAnalysis, Indicator
from src.market_hours import market_status


def _esc(s) -> str:
    """HTML-escape dynamic text before inserting into <pre> blocks."""
    return html.escape(str(s)) if s else ""


def fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def _mkt_line() -> str:
    ms = market_status()
    return f"LIVE  |  {ms['note']}" if ms["is_open"] else f"{ms['status_text']}  |  {ms['note']}"


def _win_bar(pct: int) -> str:
    filled = round(pct / 10)
    return "[" + "█" * filled + "░" * (10 - filled) + f"] {pct}%"


def _struct_label(s: str) -> str:
    return {"HH_HL": "HH / HL  (Bullish)", "LH_LL": "LH / LL  (Bearish)",
            "TRANSITION": "Structure Breaking", "RANGING": "Ranging"}.get(s, s)


def _choch_label(s: str) -> str:
    return {"BULLISH_CHOCH": "⚠️ Bullish (reversal up)",
            "BEARISH_CHOCH": "⚠️ Bearish (reversal down)",
            "NONE": "None"}.get(s, s)


def _trade_type_label(a: MarketAnalysis) -> str:
    return {"Scalp": "SCALP (minutes-hours)", "Intraday": "INTRADAY (same session)",
            "Swing": "SWING (1-5 days)", "Position": "POSITION (weeks)"}.get(a.trade_type, a.trade_type)


_TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

_TF_RANK = {"D1": 6, "H4": 5, "H1": 4, "M30": 3, "M15": 2, "M5": 1}


def _resolve_direction(analyses: list) -> dict:
    """
    Returns a dict with:
      master      : "BUY" | "SELL" | "WAIT"
      anchor_tf   : the timeframe that sets the direction (highest with BUY/SELL)
      conflict    : bool — True when lower TFs disagree with the anchor
      advice      : one plain-English line the user should act on
      counter_tfs : list of TF names that are counter-trend
    """
    actioned = [a for a in analyses if a.action in ("BUY", "SELL")]
    if not actioned:
        return dict(master="WAIT", anchor_tf="", conflict=False,
                    advice="No setup on any timeframe. Stand aside.", counter_tfs=[])

    # Highest-ranked timeframe with an actionable signal is the anchor
    anchor = max(actioned, key=lambda a: _TF_RANK.get(a.timeframe, 0))
    master = anchor.action
    anchor_tf = anchor.timeframe

    counter_tfs = [
        a.timeframe for a in actioned
        if a.action != master and _TF_RANK.get(a.timeframe, 0) < _TF_RANK.get(anchor_tf, 0)
    ]
    conflict = len(counter_tfs) > 0

    if not conflict:
        tfs_aligned = [a.timeframe for a in actioned if a.action == master]
        advice = f"All active timeframes agree: {master}. Trade with the trend."
    else:
        counter_str = " + ".join(counter_tfs)
        advice = (
            f"{anchor_tf} says {master} — this is your direction. "
            f"{counter_str} signal{'s are' if len(counter_tfs) > 1 else ' is'} "
            f"counter-trend. Skip {'them' if len(counter_tfs) > 1 else 'it'}."
        )

    return dict(master=master, anchor_tf=anchor_tf, conflict=conflict,
                advice=advice, counter_tfs=counter_tfs)


def _estimate_time(a: MarketAnalysis, target: float) -> str:
    atr = a.atr if a.atr and a.atr > 0 else None
    if atr is None:
        return "N/A"
    dist = abs(target - a.entry)
    if dist <= 0:
        return "N/A"
    candle_min = _TF_MINUTES.get(a.timeframe, 60)
    minutes = (dist / atr) * 1.5 * candle_min
    if minutes < 60:
        return f"~{max(1, round(minutes))}m"
    if minutes < 1440:
        hrs = minutes / 60
        return f"~{round(hrs, 1)}h"
    days = minutes / 1440
    return f"~{round(days, 1)}d"


def _indicator_rows(a: MarketAnalysis) -> str:
    rows = []
    for ind in a.indicators:
        arrow = "BUY" if ind.signal == "BUY" else ("SELL" if ind.signal == "SELL" else "----")
        if ind.name == "BB %B":
            val_str = f"{ind.value:.1f}%"
        elif ind.name == "MACD":
            val_str = f"{ind.value:+.3f}"
        else:
            val_str = f"{ind.value:.1f}"
        rows.append(f"  {ind.name:<12} {val_str:>8}   {arrow}")
    return "\n".join(rows)


# ─── SIGNAL CARD ──────────────────────────────────────────────────────────────

def _kill_zone_line(a: MarketAnalysis) -> str:
    """One-line kill zone status for cards."""
    kz = getattr(a, "kill_zone", "")
    is_kz = getattr(a, "is_kill_zone", False)
    if is_kz:
        return f"  Kill Zone : ✓ {kz}"
    return f"  Kill Zone : Off-hours (lower prob)"


def _pd_line(a: MarketAnalysis) -> str:
    pd = getattr(a, "premium_discount", "")
    if not pd:
        return ""
    icons = {"PREMIUM": "▲ PREMIUM  — sell zone", "DISCOUNT": "▼ DISCOUNT — buy zone",
             "EQUILIBRIUM": "◆ EQUILIBRIUM — chop zone"}
    return f"  Regime    : {icons.get(pd, pd)}"


def signal_card(a: MarketAnalysis) -> str:
    ms = market_status()

    lines = ["<pre>"]

    if a.action in ("BUY", "SELL"):
        dir_str = "BUY  LONG" if a.action == "BUY" else "SELL SHORT"
        lines += [
            "╔══════════════════════════════════╗",
            f"║  XAU/USD  [ {a.action} ]  {a.trade_type.upper():<14}║",
            "╚══════════════════════════════════╝",
            "",
            f"  Win Rate  : {_win_bar(a.win_probability)}",
            f"  Confidence: {a.confidence}%   ADX: {a.adx:.1f}",
            "",
            f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
            f"  Daily Bias: {getattr(a, 'daily_bias', '') or 'N/A'}",
            f"  HTF Align : {a.htf_bias}",
            f"  Session   : {a.session or 'N/A'}",
        ]
        lines.append(_kill_zone_line(a))
        pd_line = _pd_line(a)
        if pd_line:
            lines.append(pd_line)
        nr = getattr(a, "near_round", "")
        if nr:
            lines.append(f"  Round Lvl : {nr}")
        pdh = getattr(a, "pdh", 0.0)
        pdl = getattr(a, "pdl", 0.0)
        if pdh > 0 and pdl > 0:
            lines.append(f"  PDH / PDL : {fmt_price(pdh)} / {fmt_price(pdl)}")
        lines.append(f"  Price     : {fmt_price(a.price)}")

        htf_lower = a.htf_bias.lower()
        signal_is_buy = a.action == "BUY"
        htf_is_against = (
            (signal_is_buy  and any(w in htf_lower for w in ("bearish", "sell"))) or
            (not signal_is_buy and any(w in htf_lower for w in ("bullish", "buy")))
        )
        if htf_is_against:
            opposite = "SELL" if signal_is_buy else "BUY"
            lines += [
                "",
                "  ! COUNTER-TREND WARNING",
                "──────────────────────────────────",
                f"  HTF says {opposite}. This {a.timeframe} signal",
                f"  fights the bigger trend. Use",
                f"  smaller size or skip this trade.",
                "──────────────────────────────────",
            ]
        else:
            lines.append("")

        lines += [
            "──────────────────────────────────",
            "  TRADE PLAN",
            "──────────────────────────────────",
            f"  Entry     : {fmt_price(a.entry)}",
        ]
        if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
            lines.append(f"  Limit     : {fmt_price(a.limit_entry)}  (better fill)")
        lines += [
            f"  Stop Loss : {fmt_price(a.stop_loss)}",
            "──────────────────────────────────",
        ]
        t1 = _estimate_time(a, a.tp1)
        t2 = _estimate_time(a, a.tp2)
        t3 = _estimate_time(a, a.tp3)
        rr1 = round(abs(a.tp1 - a.entry) / abs(a.entry - a.stop_loss), 1) if abs(a.entry - a.stop_loss) > 0 else 0
        rr2 = round(abs(a.tp2 - a.entry) / abs(a.entry - a.stop_loss), 1) if abs(a.entry - a.stop_loss) > 0 else 0
        rr3 = round(abs(a.tp3 - a.entry) / abs(a.entry - a.stop_loss), 1) if abs(a.entry - a.stop_loss) > 0 else 0
        lines += [
            f"  TP1       : {fmt_price(a.tp1)}  (1:{rr1} R:R  {t1})",
            f"  TP2       : {fmt_price(a.tp2)}  (1:{rr2} R:R  {t2})",
            f"  TP3       : {fmt_price(a.tp3)}  (1:{rr3} R:R  {t3})",
            "──────────────────────────────────",
        ]
        if a.confluence_list:
            n = len(a.confluence_list)
            lines.append(f"  CONFLUENCE  ({n} factors)")
            for cf in a.confluence_list:
                lines.append(f"    + {cf}")
            lines.append("")
        if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Spinning Top"):
            lines.append(f"  Pattern   : {a.candle_pattern}")
    else:
        lines += [
            "╔══════════════════════════════════╗",
            "║  XAU/USD  [ WAIT ]               ║",
            "╚══════════════════════════════════╝",
            "",
            f"  No entry. Setup not confirmed.",
            f"  Reason: {(a.wait_reason or 'Indicators mixed')[:42]}",
            "",
            f"  Confidence: {a.confidence}%   ADX: {a.adx:.1f}",
            f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
            f"  HTF Bias  : {a.htf_bias}",
            f"  Session   : {a.session or 'N/A'}",
            "",
            "  INDICATOR SNAPSHOT",
            "──────────────────────────────────",
            f"  RSI(14)  : {a.rsi_value:.1f}",
            f"  Stoch K/D: {a.stoch_k_val:.1f} / {a.stoch_d_val:.1f}",
            f"  MACD Hist: {a.macd_hist:+.3f}",
            f"  +DI/-DI  : {a.plus_di:.1f} / {a.minus_di:.1f}",
            f"  BB%B     : {a.bb_pct:.1f}%",
            f"  Votes    : BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
        ]

    # ── Alert cooldown status ──────────────────────────────────────────────────
    try:
        from src.alerts import get_signal_lock_info
        lock_info = get_signal_lock_info(a.timeframe)
        if lock_info:
            lines += ["──────────────────────────────────", f"  {lock_info}"]
    except Exception:
        pass

    if not ms["is_open"]:
        lines += ["", f"  ! {ms['status_text']} — {ms['note']}"]

    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── ANALYSIS CARD ────────────────────────────────────────────────────────────

def analysis_card(a: MarketAnalysis) -> str:
    ms = market_status()
    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  FULL ANALYSIS         ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Price     : {fmt_price(a.price)}",
        f"  Timeframe : {a.timeframe}   {_mkt_line()}",
        f"  Session   : {a.session or 'N/A'}",
        "",
        "──────────────────────────────────",
        "  MARKET STRUCTURE",
        "──────────────────────────────────",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
        f"  Bias      : {a.bias}   ({a.strength})",
        f"  HTF Bias  : {a.htf_bias}",
        f"  Trend     : {a.trend}",
        f"  Momentum  : {a.momentum}",
        f"  ADX       : {a.adx:.1f}",
        "",
        "──────────────────────────────────",
        "  INDICATORS",
        "──────────────────────────────────",
        _indicator_rows(a),
        f"  MACD Hist : {a.macd_hist:+.3f}",
        f"  +DI / -DI : {a.plus_di:.1f} / {a.minus_di:.1f}",
        f"  Stoch K/D : {a.stoch_k_val:.1f} / {a.stoch_d_val:.1f}",
        f"  BB%B      : {a.bb_pct:.1f}%",
        f"  Votes     : BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
    ]

    if a.candle_pattern and a.candle_pattern != "None":
        lines.append(f"  Pattern   : {a.candle_pattern}")

    lines += ["",
        "──────────────────────────────────",
        "  KEY LEVELS",
        "──────────────────────────────────",
        f"  R2        : {fmt_price(a.resistance2)}",
        f"  R1        : {fmt_price(a.resistance1)}",
        f"  BB Upper  : {fmt_price(a.bb_upper)}",
        f"  -- Price  : {fmt_price(a.price)}",
        f"  BB Lower  : {fmt_price(a.bb_lower)}",
        f"  S1        : {fmt_price(a.support1)}",
        f"  S2        : {fmt_price(a.support2)}",
        f"  ATR(14)   : {fmt_price(a.atr)}",
        "",
        "──────────────────────────────────",
    ]

    if a.action in ("BUY", "SELL"):
        t1 = _estimate_time(a, a.tp1)
        t2 = _estimate_time(a, a.tp2)
        lines += [
            f"  SIGNAL    : {a.action}   {_trade_type_label(a)}",
            f"  Win Rate  : {_win_bar(a.win_probability)}",
            "",
            f"  Entry     : {fmt_price(a.entry)}",
        ]
        if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
            lines.append(f"  Limit     : {fmt_price(a.limit_entry)}")
        lines += [
            f"  Stop Loss : {fmt_price(a.stop_loss)}",
            f"  TP1       : {fmt_price(a.tp1)}  ({t1})",
            f"  TP2       : {fmt_price(a.tp2)}  ({t2})",
            f"  TP3       : {fmt_price(a.tp3)}",
            f"  R:R       : 1:{a.rr_ratio}",
        ]
        if a.confluence_list:
            lines.append("")
            lines.append(f"  Confluence ({len(a.confluence_list)} factors):")
            for cf in a.confluence_list:
                lines.append(f"    + {cf}")
    else:
        lines += [
            f"  SIGNAL    : WAIT",
            f"  Reason    : {(a.wait_reason or a.verdict_reason)[:44]}",
        ]

    if not ms["is_open"]:
        lines += ["", f"  ! {ms['status_text']} — {ms['note']}"]
    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── RECOMMEND CARD ───────────────────────────────────────────────────────────

def _quality_label(q: str) -> str:
    return {
        "A+": "A+  PREMIUM  (85%+ win rate)",
        "A":  "A   QUALITY  (80%+ win rate)",
        "B":  "B   STANDARD (70%+ win rate)",
        "C":  "C   MARGINAL (no entry)",
    }.get(q, q)


# ─── PART 1: Full professional market analysis ────────────────────────────────

def pro_analysis_card(a: MarketAnalysis) -> str:
    """
    Step 1 of /recommend — the full institutional breakdown.
    Shows everything the engine computed so the user understands
    the market before seeing any entry.
    """
    ms  = market_status()
    mkt = "LIVE" if ms["is_open"] else ms["status_text"]

    # Describe what each indicator is saying in plain language
    def _ind_verdict(ind) -> str:
        if ind.signal == "BUY":
            return "Bullish"
        if ind.signal == "SELL":
            return "Bearish"
        return "Neutral"

    # ADX trend strength description
    if a.adx >= 40:
        adx_desc = "Very Strong Trend"
    elif a.adx >= 25:
        adx_desc = "Trending"
    elif a.adx >= 18:
        adx_desc = "Weak Trend"
    else:
        adx_desc = "Ranging / Choppy"

    # RSI description
    if a.rsi_value >= 70:
        rsi_desc = "Overbought"
    elif a.rsi_value <= 30:
        rsi_desc = "Oversold"
    elif a.rsi_value >= 60:
        rsi_desc = "Bullish territory"
    elif a.rsi_value <= 40:
        rsi_desc = "Bearish territory"
    else:
        rsi_desc = "Neutral zone"

    # MACD description
    macd_desc = "Bullish momentum" if a.macd_hist > 0 else "Bearish momentum"
    if abs(a.macd_hist) < 0.1:
        macd_desc = "Flat / crossing"

    # Stoch description
    if a.stoch_k_val >= 80:
        stoch_desc = "Overbought"
    elif a.stoch_k_val <= 20:
        stoch_desc = "Oversold"
    else:
        stoch_desc = "Mid-range"

    # BB description
    if a.bb_pct >= 90:
        bb_desc = "Near upper band — extended"
    elif a.bb_pct <= 10:
        bb_desc = "Near lower band — extended"
    else:
        bb_desc = f"{a.bb_pct:.0f}% of range"

    # Di line interpretation
    if a.plus_di > a.minus_di:
        di_desc = "Buyers in control"
    elif a.minus_di > a.plus_di:
        di_desc = "Sellers in control"
    else:
        di_desc = "Balanced"

    # HTF context sentence
    htf_map = {"M5": "H1", "M15": "H1", "M30": "H4",
                "H1": "H4", "H4": "D1", "D1": "D1"}
    htf_tf = htf_map.get(a.timeframe, "HTF")
    if a.htf_bias in ("Bullish", "Slightly Bullish"):
        htf_desc = f"{htf_tf} is {a.htf_bias} — macro supports longs"
    elif a.htf_bias in ("Bearish", "Slightly Bearish"):
        htf_desc = f"{htf_tf} is {a.htf_bias} — macro supports shorts"
    else:
        htf_desc = f"{htf_tf} is Neutral — no macro edge"

    # Kill zone / regime
    kz    = getattr(a, "kill_zone", "")
    is_kz = getattr(a, "is_kill_zone", False)
    kz_str = f"✓ {kz}" if is_kz else f"Off-hours"
    pd    = getattr(a, "premium_discount", "")
    pdh   = getattr(a, "pdh", 0.0)
    pdl   = getattr(a, "pdl", 0.0)
    nr    = getattr(a, "near_round", "")
    db    = getattr(a, "daily_bias", "")
    ote_h = getattr(a, "ote_high", 0.0)
    ote_l = getattr(a, "ote_low", 0.0)
    pd_icons = {"PREMIUM": "▲ PREMIUM (sell zone)", "DISCOUNT": "▼ DISCOUNT (buy zone)",
                "EQUILIBRIUM": "◆ EQUILIBRIUM (chop)"}

    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  FULL ANALYSIS  Pt.1   ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Price     : {fmt_price(a.price)}",
        f"  Timeframe : {a.timeframe}   Status: {mkt}",
        f"  Session   : {a.session or 'N/A'}",
        "",
        "══════════════════════════════════",
        "  INSTITUTIONAL CONTEXT",
        "══════════════════════════════════",
        f"  Kill Zone  : {kz_str}",
        f"  Daily Bias : {db or 'N/A'}",
        f"  HTF Bias   : {htf_desc}",
        f"  Regime     : {pd_icons.get(pd, pd or 'N/A')}",
    ]
    if pdh > 0 and pdl > 0:
        lines.append(f"  PDH / PDL  : {fmt_price(pdh)} / {fmt_price(pdl)}")
    if nr:
        lines.append(f"  Round Lvl  : {nr}")
    if ote_h > 0 and ote_l > 0:
        lines.append(f"  OTE Zone   : {fmt_price(ote_l)} – {fmt_price(ote_h)}")
        lines.append(f"              (38.2-61.8% retrace — ideal limit zone)")
    lines += [
        "",
        "══════════════════════════════════",
        "  MARKET STRUCTURE",
        "══════════════════════════════════",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
        f"  Trend     : {a.trend}   Strength: {a.strength}",
        f"  Bias      : {a.bias}   Momentum: {a.momentum}",
        "",
        "══════════════════════════════════",
        "  INDICATOR BREAKDOWN",
        "══════════════════════════════════",
        f"  ADX  {a.adx:>5.1f}   {adx_desc}",
        f"  +DI  {a.plus_di:>5.1f}   {di_desc}",
        f"  -DI  {a.minus_di:>5.1f}",
        "──────────────────────────────────",
        f"  RSI  {a.rsi_value:>5.1f}   {rsi_desc}",
        f"  Stoch {a.stoch_k_val:>4.1f}/{a.stoch_d_val:.1f}   {stoch_desc}",
        "──────────────────────────────────",
        f"  MACD Hist {a.macd_hist:>+7.3f}   {macd_desc}",
        f"  BB%B      {a.bb_pct:>6.1f}%   {bb_desc}",
        "──────────────────────────────────",
        f"  Indicator votes:",
        f"    BUY  {a.buy_votes}/5   SELL  {a.sell_votes}/5   WAIT  {a.wait_votes}/5",
    ]

    if a.candle_pattern and a.candle_pattern != "None":
        lines += [
            "──────────────────────────────────",
            f"  Candle  : {a.candle_pattern}",
        ]
    if a.breakout:
        lines.append("  Signal  : Breakout above swing high")
    if a.reversal:
        lines.append("  Signal  : Divergence reversal detected")

    lines += [
        "",
        "══════════════════════════════════",
        "  KEY LEVELS",
        "══════════════════════════════════",
        f"  R2       : {fmt_price(a.resistance2)}",
        f"  R1       : {fmt_price(a.resistance1)}",
        f"  BB Upper : {fmt_price(a.bb_upper)}",
        f"  -- Price : {fmt_price(a.price)}",
        f"  BB Lower : {fmt_price(a.bb_lower)}",
        f"  S1       : {fmt_price(a.support1)}",
        f"  S2       : {fmt_price(a.support2)}",
        f"  ATR(14)  : {fmt_price(a.atr)}  (daily range estimate)",
        "",
        "══════════════════════════════════",
        "  SETUP ASSESSMENT",
        "══════════════════════════════════",
        f"  Direction   : {a.action}",
        f"  Confidence  : {a.confidence}%",
        f"  Setup Grade : {_quality_label(a.setup_quality)}",
    ]

    if a.action in ("BUY", "SELL"):
        if a.setup_quality in ("A+", "A"):
            lines += [
                "",
                "  Grade A/A+ confirmed.",
                "  Early entry signal follows.",
            ]
        else:
            lines += [
                "",
                f"  Grade {a.setup_quality} — conditions not strong",
                "  enough for 80%+ early entry.",
                "  Reason: " + (a.wait_reason or a.verdict_reason or "Low confluence")[:36],
            ]
    else:
        lines += [
            "",
            "  No directional signal.",
            f"  Reason: {(a.wait_reason or a.verdict_reason)[:42]}",
            "  Wait for market to set up.",
        ]

    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── PART 2: Early entry signal (only for A/A+ grade) ────────────────────────

def early_entry_card(a: MarketAnalysis) -> str:
    """Alert card — compact layout, all data preserved."""
    ms     = market_status()
    sl_dist = abs(a.entry - a.stop_loss)
    rr1 = round(abs(a.tp1 - a.entry) / sl_dist, 1) if sl_dist > 0 else 0
    rr2 = round(abs(a.tp2 - a.entry) / sl_dist, 1) if sl_dist > 0 else 0
    rr3 = round(abs(a.tp3 - a.entry) / sl_dist, 1) if sl_dist > 0 else 0
    t1  = _estimate_time(a, a.tp1)
    t2  = _estimate_time(a, a.tp2)
    sep = "──────────────────────────────"

    kz     = getattr(a, "kill_zone", "")
    is_kz  = getattr(a, "is_kill_zone", False)
    pdh    = getattr(a, "pdh", 0.0)
    pdl    = getattr(a, "pdl", 0.0)
    pd     = getattr(a, "premium_discount", "")
    db     = getattr(a, "daily_bias", "")
    ote_h  = getattr(a, "ote_high", 0.0)
    ote_l  = getattr(a, "ote_low", 0.0)
    nr     = getattr(a, "near_round", "")

    pd_arrow = {"PREMIUM": "▲ PREMIUM", "DISCOUNT": "▼ DISCOUNT", "EQUILIBRIUM": "◆ EQUIL"}.get(pd, pd)

    lines = ["<pre>",
        f"XAU/USD  {a.action}  {a.timeframe}  {a.session or 'N/A'}",
        f"Grade {a.setup_quality}  |  Strength {a.win_probability}%  |  {a.trade_type}",
        sep,
        "INSTITUTIONAL CONTEXT",
        f"  Daily : {db or 'N/A'}  |  HTF: {a.htf_bias}",
        f"  Zone  : {pd_arrow}  |  KZ: {'✓ ' + kz if is_kz else 'Off-hrs'}",
    ]
    if pdh > 0 and pdl > 0:
        lines.append(f"  PDH   : {fmt_price(pdh)}   PDL: {fmt_price(pdl)}")
    if nr:
        lines.append(f"  Round : {nr}")
    lines += [
        sep,
        "FIB RETRACEMENT",
        f"  38.2% : {fmt_price(a.fib_382)}",
        f"  50.0% : {fmt_price(a.fib_500)}",
        f"  61.8% : {fmt_price(a.fib_618)}",
    ]
    if ote_h > 0 and ote_l > 0:
        lines += [
            f"  ─── OTE : {fmt_price(ote_l)} – {fmt_price(ote_h)} ───",
            f"  (38.2-61.8% retrace — best limit zone)",
        ]
    lines += [sep, "ENTRY"]

    if a.early_entry and a.early_entry != a.entry:
        lines += [
            f"  Limit : {fmt_price(a.early_entry)}  ({a.early_entry_reason[:30]})",
            f"  Mkt   : {fmt_price(a.entry)}  (if missed)",
        ]
    else:
        lines.append(f"  Mkt   : {fmt_price(a.entry)}")

    lines += [
        f"  SL    : {fmt_price(a.stop_loss)}",
        sep,
        "TARGETS",
        f"  TP1 : {fmt_price(a.tp1)}  1:{rr1}  {t1}",
        f"  TP2 : {fmt_price(a.tp2)}  1:{rr2}  {t2}",
        f"  TP3 : {fmt_price(a.tp3)}  1:{rr3}  (full move)",
        sep,
        "CONFLUENCE",
    ]

    for i, cf in enumerate(a.confluence_list, 1):
        lines.append(f"  {i}. {cf}")

    if not a.confluence_list:
        lines.append("  (no factors)")

    if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Spinning Top"):
        lines.append(f"  + {a.candle_pattern}")

    if a.wait_reason and a.action in ("BUY", "SELL"):
        lines += [sep, "CAUTIONS"]
        for note in a.wait_reason.split(" | "):
            if note.strip():
                lines.append(f"  ! {note.strip()}")

    if not ms["is_open"]:
        lines += [sep, f"  ! {ms['status_text']}"]

    lines += [sep,
        "  50% at TP1. Move SL to entry.",
        "  Not financial advice.",
        "</pre>"]
    return "\n".join(lines)


def no_early_entry_card(a: MarketAnalysis) -> str:
    """Shown when analysis finds a direction but grade is B/C — not 80%+ confident."""
    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  NO ENTRY  Pt.2        ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Direction : {a.action}  (grade {a.setup_quality})",
        f"  Win Rate  : {_win_bar(a.win_probability) if a.win_probability else 'N/A'}",
        "",
        "  Grade below A — 80% threshold NOT met.",
        "  No early entry issued.",
        "",
        "  What needs to improve:",
        "──────────────────────────────────",
    ]

    # Tell the user what is missing
    missing = []
    if a.confidence < 80:
        missing.append(f"Confidence {a.confidence}% < 80% (need more indicators)")
    if len(a.confluence_list) < 4:
        missing.append(f"Confluence {len(a.confluence_list)}/4+ factors needed")
    if a.adx < 20:
        missing.append(f"ADX {a.adx:.1f} too low — market ranging")
    if a.htf_bias in ("Neutral",):
        missing.append("HTF bias neutral — need clear macro alignment")
    if a.session in ("Asian",):
        missing.append("Asian session — wait for London/NY for volume")
    if not missing:
        missing.append("Signal gating: R:R or ADX conditions not met")

    for m in missing:
        lines.append(f"  - {m}")

    lines += [
        "",
        "  Monitor for setup to improve.",
        "  Use /alerts to get notified",
        "  when A/A+ grade fires.",
        "",
        "  Not financial advice.", "</pre>",
    ]
    return "\n".join(lines)


def recommend_card(a: MarketAnalysis) -> str:
    """Single-TF fallback (kept for internal use)."""
    return pro_analysis_card(a)


def recommend_multi_card(analyses: list) -> str:
    """
    All-timeframe recommendation card.
    Shows a quick signal matrix for all TFs, then full trade plans for
    any TF that has an actionable BUY or SELL.
    """
    if not analyses:
        return "<pre>No analysis data available. Please try again.</pre>"

    price  = analyses[0].price if analyses else 0.0
    ms     = market_status()
    mkt    = ms["note"]

    TF_ORDER = ["M5", "M15", "M30", "H1", "H4", "D1"]
    tf_map   = {a.timeframe: a for a in analyses}

    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  ALL TIMEFRAMES        ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Price : {fmt_price(price)}    {mkt}",
        "",
        "──────────────────────────────────",
        "  TF    SIGNAL  CONF  GRADE  BIAS",
        "──────────────────────────────────",
    ]

    active_tfs = []
    for tf in TF_ORDER:
        a = tf_map.get(tf)
        if a is None:
            lines.append(f"  {tf:<4}  ------  ---   ----")
            continue
        action = a.action if a.action in ("BUY", "SELL") else "WAIT"
        grade  = a.setup_quality or "-"
        bias   = _esc(a.bias)[:7] if a.bias else "Neutral"
        marker = "  &lt;--" if action in ("BUY", "SELL") else ""
        lines.append(
            f"  {tf:<4}  {action:<6}  {a.confidence}%  {grade:<5} {bias}{marker}"
        )
        if action in ("BUY", "SELL"):
            active_tfs.append(a)

    lines.append("──────────────────────────────────")

    if not active_tfs:
        lines += [
            "",
            "  No actionable signals across",
            "  any timeframe right now.",
            "  Market is ranging — wait for",
            "  a clean directional setup.",
        ]
    else:
        lines += ["", "  ACTIVE SIGNALS", "──────────────────────────────────"]
        for a in active_tfs:
            sl_dist  = abs(a.entry - a.stop_loss)
            rr1 = round(abs(a.tp1 - a.entry) / sl_dist, 1) if sl_dist > 0 and a.tp1 else 0
            rr2 = round(abs(a.tp2 - a.entry) / sl_dist, 1) if sl_dist > 0 and a.tp2 else 0
            lines += [
                f"  {a.timeframe}  {a.action}  {a.confidence}%  Grade: {a.setup_quality or '-'}",
                f"  Entry   : {fmt_price(a.entry)}",
                f"  SL      : {fmt_price(a.stop_loss)}",
                f"  TP1     : {fmt_price(a.tp1 or 0)}  (1:{rr1})",
                f"  TP2     : {fmt_price(a.tp2 or 0)}  (1:{rr2})",
            ]
            if a.verdict_reason:
                lines.append(f"  Reason  : {_esc(a.verdict_reason[:34])}")
            lines.append("  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·")

    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── TREND CARD ───────────────────────────────────────────────────────────────

def trend_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  TREND ANALYSIS        ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Timeframe : {a.timeframe}   {mkt}",
        f"  Price     : {fmt_price(a.price)}",
        "",
        "──────────────────────────────────",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
        f"  Trend     : {a.trend}   ({a.strength})",
        f"  Bias      : {a.bias}",
        f"  HTF Bias  : {a.htf_bias}",
        f"  Momentum  : {a.momentum}",
        f"  Session   : {a.session or 'N/A'}",
        "",
        "──────────────────────────────────",
        "  INDICATORS",
        "──────────────────────────────────",
        f"  ADX       : {a.adx:.1f}   +DI: {a.plus_di:.1f}  -DI: {a.minus_di:.1f}",
        f"  RSI(14)   : {a.rsi_value:.1f}",
        f"  Stoch K/D : {a.stoch_k_val:.1f} / {a.stoch_d_val:.1f}",
        f"  MACD Hist : {a.macd_hist:+.3f}",
        f"  BB%B      : {a.bb_pct:.1f}%",
        f"  Votes     : BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
    ]
    if a.candle_pattern and a.candle_pattern != "None":
        lines.append(f"  Pattern   : {a.candle_pattern}")
    if a.breakout:
        lines.append("  Note      : Breakout in progress")
    if a.reversal:
        lines.append("  Note      : Divergence reversal signal")
    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── LEVELS CARD ──────────────────────────────────────────────────────────────

def levels_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  KEY LEVELS            ║",
        "╚══════════════════════════════════╝",
        "",
        f"  TF: {a.timeframe}   {mkt}",
        "",
        "──────────────────────────────────",
        f"  RESISTANCE 2 : {fmt_price(a.resistance2)}",
        f"  RESISTANCE 1 : {fmt_price(a.resistance1)}",
        f"  BB Upper     : {fmt_price(a.bb_upper)}",
        "  ──────────────────────────────",
        f"     Price     : {fmt_price(a.price)}",
        f"     BB%B      : {a.bb_pct:.1f}%",
        "  ──────────────────────────────",
        f"  BB Lower     : {fmt_price(a.bb_lower)}",
        f"  SUPPORT 1    : {fmt_price(a.support1)}",
        f"  SUPPORT 2    : {fmt_price(a.support2)}",
        "",
        "──────────────────────────────────",
        f"  ATR(14)      : {fmt_price(a.atr)}",
        f"  Liq Zone     : {a.liquidity_zone}",
        f"  Structure    : {_struct_label(a.market_structure)}",
        f"  CHoCH        : {_choch_label(a.choch)}",
        "",
        "  Not financial advice.", "</pre>",
    ]
    return "\n".join(lines)


# ─── OUTLOOK CARD ─────────────────────────────────────────────────────────────

def outlook_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]

    if a.bias == "Bullish":
        scenario = (f"Targeting {fmt_price(a.tp1)}, extension {fmt_price(a.tp2)}."
                    f" Key support at {fmt_price(a.support1)}."
                    f" Invalidation below {fmt_price(a.stop_loss)}.")
    elif a.bias == "Bearish":
        scenario = (f"Targeting {fmt_price(a.tp1)}, extension {fmt_price(a.tp2)}."
                    f" Key resistance at {fmt_price(a.resistance1)}."
                    f" Invalidation above {fmt_price(a.stop_loss)}.")
    else:
        scenario = (f"Ranging {fmt_price(a.support1)} — {fmt_price(a.resistance1)}."
                    f" Wait for a clean directional break with volume confirmation.")

    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  MARKET OUTLOOK        ║",
        "╚══════════════════════════════════╝",
        "",
        f"  TF: {a.timeframe}   {mkt}",
        f"  Session   : {a.session or 'N/A'}",
        "",
        "──────────────────────────────────",
        f"  Bias      : {a.bias}   ({a.strength})",
        f"  HTF Bias  : {a.htf_bias}",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
        f"  Momentum  : {a.momentum}   ADX: {a.adx:.1f}",
        f"  Action    : {a.action}",
    ]
    if a.action in ("BUY", "SELL"):
        lines.append(f"  Win Rate  : {_win_bar(a.win_probability)}")
    lines += [
        "",
        "──────────────────────────────────",
        "  SCENARIO",
        "──────────────────────────────────",
    ]
    # Word-wrap scenario at ~36 chars
    words, buf, wrapped = scenario.split(), [], []
    for w in words:
        if sum(len(x) + 1 for x in buf) + len(w) > 36:
            wrapped.append("  " + " ".join(buf))
            buf = [w]
        else:
            buf.append(w)
    if buf:
        wrapped.append("  " + " ".join(buf))
    lines += wrapped

    if a.confluence_list:
        lines += ["", f"  Confluence ({len(a.confluence_list)} factors):"]
        for cf in a.confluence_list:
            lines.append(f"    + {cf}")

    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── ALERT CARD ───────────────────────────────────────────────────────────────

def alert_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"[CLOSED] {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        f"║  SIGNAL ALERT  XAU/USD  {a.action:<9}║",
        "╚══════════════════════════════════╝",
        "",
        f"  Win Rate  : {_win_bar(a.win_probability)}",
        f"  Confidence: {a.confidence}%   ADX: {a.adx:.1f}",
        f"  Type      : {_trade_type_label(a)}",
        "",
        f"  Price     : {fmt_price(a.price)}",
        f"  Session   : {a.session or 'N/A'}   {mkt}",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
        f"  HTF Align : {a.htf_bias}",
        "",
        "──────────────────────────────────",
        f"  Entry     : {fmt_price(a.entry)}",
    ]
    if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
        lines.append(f"  Limit     : {fmt_price(a.limit_entry)}  (better fill)")
    t1 = _estimate_time(a, a.tp1)
    t2 = _estimate_time(a, a.tp2)
    rr1 = round(abs(a.tp1 - a.entry) / abs(a.entry - a.stop_loss), 1) if abs(a.entry - a.stop_loss) > 0 else 0
    rr2 = round(abs(a.tp2 - a.entry) / abs(a.entry - a.stop_loss), 1) if abs(a.entry - a.stop_loss) > 0 else 0
    lines += [
        f"  Stop Loss : {fmt_price(a.stop_loss)}",
        f"  TP1       : {fmt_price(a.tp1)}  (1:{rr1}  {t1})",
        f"  TP2       : {fmt_price(a.tp2)}  (1:{rr2}  {t2})",
        f"  TP3       : {fmt_price(a.tp3)}",
        "──────────────────────────────────",
    ]
    if a.confluence_list:
        lines.append(f"  CONFLUENCE ({len(a.confluence_list)} factors)")
        for cf in a.confluence_list:
            lines.append(f"    + {cf}")
    if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Spinning Top"):
        lines.append(f"  Pattern   : {a.candle_pattern}")
    lines += ["", "  Not financial advice.", "</pre>"]
    return "\n".join(lines)


# ─── MISC CARDS (unchanged structure) ─────────────────────────────────────────

def market_open_card(a: MarketAnalysis) -> str:
    lines = ["<pre>",
        "MARKET NOW OPEN  |  XAU/USD",
        "=" * 32,
        f"Price     : {fmt_price(a.price)}",
        f"Structure : {_struct_label(a.market_structure)}",
        f"CHoCH     : {_choch_label(a.choch)}",
        f"Bias      : {a.bias}   ({a.strength})",
        f"HTF Bias  : {a.htf_bias}",
        f"ADX       : {a.adx:.1f}",
        "─" * 32,
    ]
    if a.action in ("BUY", "SELL"):
        lines += [
            f"SIGNAL    : {a.action}   {_trade_type_label(a)}",
            f"Win Rate  : {_win_bar(a.win_probability)}",
            f"Entry     : {fmt_price(a.entry)}",
            f"Stop Loss : {fmt_price(a.stop_loss)}",
            f"TP1       : {fmt_price(a.tp1)}",
            f"TP2       : {fmt_price(a.tp2)}",
            f"R:R       : 1:{a.rr_ratio}",
        ]
        if a.confluence_list:
            for cf in a.confluence_list:
                lines.append(f"  + {cf}")
    else:
        lines += [
            "SIGNAL    : WAIT",
            f"Reason    : {(a.wait_reason or a.verdict_reason)[:44]}",
            "Monitor for a clean directional setup.",
        ]
    lines += [
        "─" * 32,
        f"R1: {fmt_price(a.resistance1)}   R2: {fmt_price(a.resistance2)}",
        f"S1: {fmt_price(a.support1)}   S2: {fmt_price(a.support2)}",
        "</pre>",
    ]
    return "\n".join(lines)


def weekly_closed_recap_text() -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%A %d %b %Y  %H:%M UTC")
    lines = ["<pre>",
        "MARKET CLOSED  |  XAU/USD",
        "─" * 30,
        f"Time:   {now}",
        "─" * 30,
        "Gold futures closed for the weekend.",
        "Analysis resumes Sunday 6:00 PM ET.",
        "─" * 30,
        "Active trades remain tracked.",
        "</pre>",
    ]
    return "\n".join(lines)


def news_card(items: list) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")
    lines = ["<pre>",
        "GOLD NEWS  |  XAU/USD",
        "=" * 32,
        f"Updated: {now}",
        "=" * 32,
    ]
    if not items:
        lines += ["No headlines available right now.", "Try again in a few minutes."]
    else:
        for i, item in enumerate(items, 1):
            date_part = f"  [{item['date']}]" if item.get("date") else ""
            title = item.get("title", "")
            words = title.split()
            wrapped, line = [], ""
            for w in words:
                if len(line) + len(w) + 1 > 34:
                    if line: wrapped.append(line)
                    line = w
                else:
                    line = f"{line} {w}".strip()
            if line: wrapped.append(line)
            first_line = f"{i}. {wrapped[0]}" if wrapped else f"{i}. {title}"
            lines.append(first_line)
            for extra in wrapped[1:]:
                lines.append(f"   {extra}")
            src = item.get("source", "")
            lines.append(f"   {src}{date_part}")
            if i < len(items):
                lines.append("─" * 32)
    lines += ["=" * 32, "Source: Yahoo Finance / RSS", "Refreshes every 30 minutes.", "</pre>"]
    return "\n".join(lines)


def multi_timeframe_card(analyses: list) -> str:
    """Compact all-timeframe analysis card — one block per TF."""
    SEP  = "─" * 34
    WIDE = "═" * 34
    if not analyses:
        return "<pre>No analysis available.</pre>"

    first = analyses[0]
    ms = market_status()
    session = first.session or "N/A"
    mkt_line = "LIVE" if ms["is_open"] else ms["status_text"]

    resolved = _resolve_direction(analyses)
    master    = resolved["master"]
    anchor_tf = resolved["anchor_tf"]
    conflict  = resolved["conflict"]
    advice    = resolved["advice"]
    counter   = resolved["counter_tfs"]

    lines = [
        "<pre>",
        "XAU/USD  MULTI-TIMEFRAME ANALYSIS",
        WIDE,
        f"Price   : {fmt_price(first.price)}  |  {mkt_line}",
        f"Session : {session}",
        WIDE,
        "WHAT TO DO",
        SEP,
    ]

    if master in ("BUY", "SELL"):
        lines += [
            f"Direction : {master}  (set by {anchor_tf})",
            f"Conflict  : {'YES — see below' if conflict else 'None — all clear'}",
            "",
        ]
        for line in _wrap(advice, 34):
            lines.append(line)
    else:
        lines.append("Direction : WAIT — no clear setup")
        lines.append("")
        for line in _wrap(advice, 34):
            lines.append(line)

    lines.append(WIDE)

    for a in analyses:
        action = a.action
        grade  = a.setup_quality if action in ("BUY", "SELL") else ""
        conf   = f"{a.confidence}%"
        is_counter = a.timeframe in counter
        flag   = "  [COUNTER-TREND]" if is_counter else ""
        label  = f"{a.timeframe}  {action}" + (f"  ({grade})" if grade else "") + f"  |  {conf}" + flag
        lines += [
            label,
            SEP,
            f"Bias    : {a.bias}  ({a.strength})",
            f"Trend   : {a.trend}  |  ADX {a.adx:.0f}",
            f"RSI     : {a.rsi_value:.0f}  |  Stoch {a.stoch_k_val:.0f}",
        ]
        if action in ("BUY", "SELL"):
            lines += [
                f"Entry   : {fmt_price(a.entry)}",
                f"SL      : {fmt_price(a.stop_loss)}",
                f"TP1     : {fmt_price(a.tp1)}",
                f"TP2     : {fmt_price(a.tp2)}",
            ]
        if a.action in ("BUY", "SELL") and a.win_probability:
            win_pct = a.win_probability
            gate_ok = win_pct >= 68 and a.setup_quality in ("A+", "A") and a.adx >= 25
            gate_icon = "✅" if gate_ok else "❌"
            if not gate_ok:
                reasons = []
                if win_pct < 68:      reasons.append(f"win {win_pct}%<68%")
                if a.adx < 25:        reasons.append(f"ADX {a.adx:.0f}<25")
                if a.setup_quality not in ("A+", "A"): reasons.append(f"grade {a.setup_quality}")
                gate_note = f" ({', '.join(reasons)})"
            else:
                gate_note = " — alert will fire"
            lines.append(f"Win %   : {_win_bar(win_pct)}")
            lines.append(f"Alert   : {gate_icon}{gate_note}")
        else:
            lines.append(f"Win %   : —")
        lines.append(WIDE)

    # ── Alert cooldown summary ─────────────────────────────────────────────────
    try:
        from src.alerts import get_signal_lock_info
        alert_rows = []
        for a in analyses:
            info = get_signal_lock_info(a.timeframe)
            if info:
                alert_rows.append(f"  {a.timeframe:<4} {info}")
        if alert_rows:
            lines += ["ALERT STATUS", SEP] + alert_rows + [WIDE]
    except Exception:
        pass

    lines.append("</pre>")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list:
    """Word-wrap a string to fit within width characters."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    return lines


def market_conditions_card(a: MarketAnalysis) -> str:
    """Auto-broadcast every 4 hours explaining current market state."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%d %b  %H:%M UTC")
    ms  = market_status()

    if a.adx >= 40:
        adx_desc = "Strong trend"
    elif a.adx >= 25:
        adx_desc = "Trending"
    elif a.adx >= 18:
        adx_desc = "Weak trend"
    else:
        adx_desc = "Ranging / Choppy"

    if a.rsi_value >= 70:
        rsi_zone = "Overbought"
    elif a.rsi_value <= 30:
        rsi_zone = "Oversold"
    elif a.rsi_value >= 55:
        rsi_zone = "Bullish zone"
    elif a.rsi_value <= 45:
        rsi_zone = "Bearish zone"
    else:
        rsi_zone = "Neutral"

    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  MARKET UPDATE         ║",
        "╚══════════════════════════════════╝",
        "",
        f"  {now}",
        f"  Price     : {fmt_price(a.price)}",
        f"  Session   : {a.session or 'N/A'}",
        "",
        "──────────────────────────────────",
        "  CURRENT CONDITIONS",
        "──────────────────────────────────",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
        f"  Bias      : {a.bias}   ({a.strength})",
        f"  HTF Bias  : {a.htf_bias}",
        f"  Momentum  : {a.momentum}",
        "",
        f"  ADX  {a.adx:>5.1f}   {adx_desc}",
        f"  RSI  {a.rsi_value:>5.1f}   {rsi_zone}",
        f"  Stoch     : {a.stoch_k_val:.1f} / {a.stoch_d_val:.1f}",
        f"  +DI / -DI : {a.plus_di:.1f} / {a.minus_di:.1f}",
        f"  Votes     : BUY {a.buy_votes}/5   SELL {a.sell_votes}/5",
        "",
        "──────────────────────────────────",
        "  SIGNAL STATUS",
        "──────────────────────────────────",
    ]

    if a.action in ("BUY", "SELL"):
        lines += [
            f"  Direction : {a.action}",
            f"  Confidence: {a.confidence}%",
            f"  Grade     : {a.setup_quality}",
            f"  Entry     : {fmt_price(a.entry)}",
            f"  Stop Loss : {fmt_price(a.stop_loss)}",
            f"  TP1 / TP2 : {fmt_price(a.tp1)} / {fmt_price(a.tp2)}",
        ]
    else:
        reason = (a.wait_reason or a.verdict_reason or "Indicators mixed")[:42]
        lines += [
            "  Status    : WAIT — no entry yet",
            f"  Reason    : {reason}",
            "",
            "  Watching for:",
        ]
        if a.buy_votes >= a.sell_votes:
            lines.append("  - BUY setup: bullish breakout + ADX > 25")
        else:
            lines.append("  - SELL setup: bearish break + ADX > 25")
        lines.append("  - Confidence >= 75% + R:R >= 1:2")

    lines += [
        "",
        f"  Key levels:",
        f"  R1: {fmt_price(a.resistance1)}   R2: {fmt_price(a.resistance2)}",
        f"  S1: {fmt_price(a.support1)}   S2: {fmt_price(a.support2)}",
        "",
        "  Next update in ~4 hours.",
        "  Use /signal for on-demand scan.",
        "</pre>",
    ]
    return "\n".join(lines)


def history_card(trades: list, stats: dict) -> str:
    """Signal history panel — last 20 trades with outcomes and summary stats."""
    from datetime import datetime, timezone

    def _status_label(t: dict) -> str:
        s = t.get("status", "")
        if s == "open":         return "OPEN     "
        if s == "tp2_hit":      return "WIN   TP2"
        if s == "tp1_hit":      return "WIN   TP1"
        if s == "tp1_sl_hit":   return "TP1 / SL "
        if s == "sl_hit":       return "LOSS  SL "
        if s == "expired":      return "EXPIRED  "
        return s.upper()[:9]

    def _fmt_date(ts) -> str:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M")
        except Exception:
            return "------"

    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  SIGNAL HISTORY        ║",
        "╚══════════════════════════════════╝",
        "",
        "  SUMMARY",
        "──────────────────────────────────",
        f"  Total signals : {stats['total']}",
        f"  Wins          : {stats['wins']}",
        f"  Losses        : {stats['losses']}",
        f"  Open          : {stats['open']}",
        f"  Win rate      : {stats['win_rate']}%"
        + ("" if stats['total_closed'] == 0 else f"  ({stats['total_closed']} closed)"),
        "──────────────────────────────────",
    ]

    if not trades:
        lines += [
            "",
            "  No signals recorded yet.",
            "  Alerts fire automatically when",
            "  a BUY or SELL is detected.",
            "</pre>",
        ]
        return "\n".join(lines)

    lines += ["", "  RECENT SIGNALS  (newest first)", "──────────────────────────────────"]

    shown = trades[:20]
    for t in shown:
        date     = _fmt_date(t.get("opened_at", 0))
        dir_     = t.get("direction", "???")
        tf       = t.get("timeframe", "??")
        entry    = t.get("entry", 0)
        conf     = t.get("confidence", 0)
        outcome  = _status_label(t)
        lines += [
            f"  {date}  {dir_:<4} {tf:<3}",
            f"  Entry: {entry:,.2f}   Conf: {conf}%",
            f"  Result: {outcome}",
            "  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·",
        ]

    if len(trades) > 20:
        lines.append(f"  ... and {len(trades) - 20} older signals")

    lines += ["", "  Use /alerts to enable auto-signals.", "</pre>"]
    return "\n".join(lines)


def restart_summary_card(open_trades: list, recent_trades: list, stats: dict) -> str:
    """Sent to all subscribers when the bot restarts — shows open positions + last 5 signals."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%d %b  %H:%M UTC")

    def _status_label(s: str) -> str:
        return {
            "open":        "OPEN",
            "tp2_hit":     "WIN  TP2",
            "tp1_hit":     "WIN  TP1",
            "tp1_sl_hit":  "TP1 / SL",
            "sl_hit":      "LOSS SL",
            "expired":     "EXPIRED",
        }.get(s, s.upper())

    def _fmt_date(ts) -> str:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M")
        except Exception:
            return "------"

    ms  = market_status()
    mkt = "OPEN" if ms["is_open"] else "CLOSED"

    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD BOT  |  BACK ONLINE   ║",
        "╚══════════════════════════════════╝",
        "",
        f"  {now}",
        f"  Market : {mkt}  —  {ms['note']}",
        "──────────────────────────────────",
    ]

    if open_trades:
        lines += ["  OPEN POSITIONS", "──────────────────────────────────"]
        for t in open_trades:
            d      = t.get("direction", "?")
            tf     = t.get("timeframe", "?")
            entry  = t.get("entry", 0)
            sl     = t.get("sl", 0)
            tp1    = t.get("tp1", 0)
            tp2    = t.get("tp2", 0)
            conf   = t.get("confidence", 0)
            opened = _fmt_date(t.get("opened_at", 0))
            lines += [
                f"  {d}  {tf}   opened {opened}",
                f"  Entry : {entry:,.2f}   Conf: {conf}%",
                f"  SL    : {sl:,.2f}",
                f"  TP1   : {tp1:,.2f}   TP2: {tp2:,.2f}",
                "  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·",
            ]
    else:
        lines += ["  No open positions.", "──────────────────────────────────"]

    if recent_trades:
        lines += ["", "  LAST 5 SIGNALS", "──────────────────────────────────"]
        for t in recent_trades[:5]:
            d      = t.get("direction", "?")
            tf     = t.get("timeframe", "?")
            entry  = t.get("entry", 0)
            conf   = t.get("confidence", 0)
            opened = _fmt_date(t.get("opened_at", 0))
            result = _status_label(t.get("status", ""))
            lines += [
                f"  {opened}  {d:<4} {tf:<3}  {result}",
                f"  Entry: {entry:,.2f}   Conf: {conf}%",
                "  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·",
            ]

    lines += [
        "",
        f"  Signals  W:{stats['wins']}  L:{stats['losses']}  "
        f"Rate:{stats['win_rate']}%",
        "──────────────────────────────────",
        "  Use /signal for live scan.",
        "  Use /history for full log.",
        "</pre>",
    ]
    return "\n".join(lines)


def welcome_text(name: str) -> str:
    ms = market_status()
    if ms["is_open"]:
        mkt = f"Market is OPEN — {ms['note']}. Live signals available."
    else:
        mkt = f"Market is CLOSED — {ms['note']}."
    return (
        f"Welcome, {name}.\n\n"
        "<b>XAU/USD Gold Analysis Bot</b>\n\n"
        "Institutional-grade signals. Precision entries.\n"
        f"{mkt}\n\n"
        "Select an option from the menu below."
    )


def help_text() -> str:
    cmds = [
        ("/signal",    "Trade signal — BUY/SELL with full trade plan"),
        ("/analyze",   "Full analysis — structure, indicators, levels, entry"),
        ("/recommend", "Verdict with indicator breakdown"),
        ("/chart",     "Live chart + AI vision analysis"),
        ("/trend",     "Trend direction, structure, momentum"),
        ("/levels",    "Support, resistance, BB, ATR levels"),
        ("/outlook",   "Market outlook and scenario"),
        ("/news",      "Latest gold market headlines"),
        ("/alerts",    "Toggle automatic signal notifications"),
        ("/settings",  "Change timeframe (M5 to D1)"),
        ("/help",      "This message"),
    ]
    lines = ["<b>Available Commands</b>\n"]
    for cmd, desc in cmds:
        lines.append(f"{cmd}  —  {desc}")
    ms = market_status()
    mkt_status = "OPEN" if ms["is_open"] else "CLOSED"
    lines += [
        "",
        f"<b>Market:</b> {mkt_status} — {ms['note']}",
        "",
        "<b>Trade Types:</b>",
        "Scalp    — M5/M15 (minutes-hours)",
        "Intraday — M30/H1 (same session)",
        "Swing    — H1/H4  (1-5 days)",
        "Position — D1     (weeks)",
    ]
    return "\n".join(lines)


def active_trades_card(open_trades: list, current_price: float) -> str:
    SEP  = "─" * 30
    WIDE = "═" * 30
    lines = [
        "<pre>",
        "ACTIVE TRADES  |  XAU/USD",
        WIDE,
        f"Live Price : {current_price:,.2f}",
        SEP,
    ]

    if not open_trades:
        lines += ["No open trades.", WIDE, "</pre>"]
        return "\n".join(lines)

    for i, t in enumerate(open_trades):
        direction = t["direction"]
        entry     = t["entry"]
        sl        = t["sl"]
        tp1       = t["tp1"]
        tp2       = t.get("tp2")
        tp3       = t.get("tp3")
        tf        = t.get("timeframe", "?")
        conf      = t.get("confidence", 0)
        opened_at = t.get("opened_at", 0)

        # P&L in points
        if direction == "BUY":
            pnl = current_price - entry
        else:
            pnl = entry - current_price
        pnl_sign  = "+" if pnl >= 0 else ""
        pnl_label = "IN PROFIT" if pnl >= 0 else "IN LOSS"

        # Distances
        sl_dist  = abs(current_price - sl)
        tp1_dist = abs(current_price - tp1) if tp1 else None

        # Age
        age_secs = time.time() - opened_at if opened_at else 0
        if age_secs < 3600:
            age_str = f"{int(age_secs // 60)}m ago"
        else:
            age_str = f"{int(age_secs // 3600)}h {int((age_secs % 3600) // 60)}m ago"

        _st = t.get("status")
        if _st == "tp1_hit":
            status_note = "  ✅ TP1 HIT — watching for TP2"
        elif _st == "tp2_hit" and t.get("tp3"):
            status_note = "  ✅✅ TP1+TP2 HIT — watching for TP3"
        else:
            status_note = ""
        lines += [
            f"{tf}  {direction}  |  Conf: {conf}%{status_note}",
            f"Opened     : {age_str}",
            f"Entry      : {entry:,.2f}",
            f"Now        : {current_price:,.2f}",
            f"P&L        : {pnl_sign}{pnl:,.1f} pts  ({pnl_label})",
            SEP,
            f"SL         : {sl:,.2f}  ({sl_dist:,.1f} pts away)",
        ]
        if tp1:
            lines.append(f"TP1        : {tp1:,.2f}  ({tp1_dist:,.1f} pts away)")
        if tp2:
            tp2_dist = abs(current_price - tp2)
            lines.append(f"TP2        : {tp2:,.2f}  ({tp2_dist:,.1f} pts away)")
        if tp3:
            tp3_dist = abs(current_price - tp3)
            lines.append(f"TP3        : {tp3:,.2f}  ({tp3_dist:,.1f} pts away)")

        if i < len(open_trades) - 1:
            lines.append(WIDE)

    lines += [WIDE, "</pre>"]
    return "\n".join(lines)


def confluence_alert_card(signal_list: list, direction: str, ref_tf: str) -> str:
    """
    Single grouped alert card for when 3+ timeframes agree on a direction.
    signal_list : list of (tf, MarketAnalysis) tuples — all same direction.
    ref_tf      : the timeframe used for the trade plan section.
    """
    SEP  = "─" * 34
    WIDE = "═" * 34

    n     = len(signal_list)
    ref_a = next(a for tf, a in signal_list if tf == ref_tf)

    sl_dist = abs(ref_a.entry - ref_a.stop_loss)
    rr1 = round(abs(ref_a.tp1 - ref_a.entry) / sl_dist, 1) if sl_dist and ref_a.tp1 else 0
    rr2 = round(abs(ref_a.tp2 - ref_a.entry) / sl_dist, 1) if sl_dist and ref_a.tp2 else 0
    rr3 = round(abs(ref_a.tp3 - ref_a.entry) / sl_dist, 1) if sl_dist and getattr(ref_a, "tp3", None) else 0

    avg_conf = round(sum(a.confidence for _, a in signal_list) / n)
    mkt_line = _mkt_line()

    lines = [
        "<pre>",
        WIDE,
        f"  XAU/USD  CONFLUENCE  {direction}",
        f"  {n} TIMEFRAMES ALIGNED",
        WIDE,
        f"  {mkt_line}",
        "",
        SEP,
        f"  {'TF':<5}  {'GRADE':<6}  {'CONF':<5}  BIAS",
        SEP,
    ]

    for tf, a in signal_list:
        grade = a.setup_quality or "-"
        bias  = (a.bias or "Neutral")[:10]
        lines.append(f"  {tf:<5}  {grade:<6}  {a.confidence}%    {bias}")

    lines += [
        SEP,
        "",
        f"  TRADE PLAN  ({ref_tf} Reference)",
        SEP,
        f"  Entry  : {fmt_price(ref_a.entry)}",
        f"  SL     : {fmt_price(ref_a.stop_loss)}",
    ]

    if ref_a.tp1:
        lines.append(f"  TP1    : {fmt_price(ref_a.tp1)}  (1:{rr1})")
    if ref_a.tp2:
        lines.append(f"  TP2    : {fmt_price(ref_a.tp2)}  (1:{rr2})")
    if getattr(ref_a, "tp3", None):
        lines.append(f"  TP3    : {fmt_price(ref_a.tp3)}  (1:{rr3})")

    lines += [
        SEP,
        f"  Avg Confidence : {avg_conf}%",
        f"  Setup Grade    : {ref_a.setup_quality}",
    ]

    if ref_a.verdict_reason:
        lines.append(f"  Reason         : {ref_a.verdict_reason[:32]}")

    lines += [
        "",
        "  Not financial advice.",
        WIDE,
        "</pre>",
    ]
    return "\n".join(lines)
