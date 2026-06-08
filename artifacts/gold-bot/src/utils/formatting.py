from src.analysis.engine import MarketAnalysis, Indicator
from src.market_hours import market_status


def fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def _market_status_line() -> str:
    ms = market_status()
    if ms["is_open"]:
        return f"LIVE  |  {ms['note']}"
    return f"{ms['status_text']}  |  {ms['note']}"


def _indicator_bar(indicators) -> str:
    lines = []
    for ind in indicators:
        arrow = "^" if ind.signal == "BUY" else ("v" if ind.signal == "SELL" else "-")
        if ind.name == "BB %B":
            val_str = f"{ind.value:.1f}%"
        elif ind.name == "MACD":
            val_str = f"{ind.value:+.3f}"
        else:
            val_str = f"{ind.value:.1f}"
        lines.append(f"{ind.name:<12} {val_str:>8}  [{arrow}] {ind.signal}")
    return "\n".join(lines)


def _verdict_block(a: MarketAnalysis) -> str:
    """Compact BUY / SELL / WAIT verdict banner."""
    if a.action == "BUY":
        return (
            "[ BUY  ]  ENTRY CONFIRMED\n"
            f"Confidence: {a.confidence}%   ADX: {a.adx:.0f}"
        )
    if a.action == "SELL":
        return (
            "[ SELL ]  ENTRY CONFIRMED\n"
            f"Confidence: {a.confidence}%   ADX: {a.adx:.0f}"
        )
    return (
        "[  --  ]  WAIT — NO SIGNAL\n"
        f"Reason: {(a.wait_reason or a.verdict_reason)[:44]}"
    )


def _consensus_bar(a: MarketAnalysis) -> str:
    total   = max(a.buy_votes + a.sell_votes + a.wait_votes, 1)
    b_blks  = round(a.buy_votes  / total * 16)
    s_blks  = round(a.sell_votes / total * 16)
    n_blks  = 16 - b_blks - s_blks
    bar     = ("+" * b_blks) + ("-" * s_blks) + ("." * max(n_blks, 0))
    b_pct   = int(a.buy_votes  / total * 100)
    s_pct   = int(a.sell_votes / total * 100)
    n_pct   = 100 - b_pct - s_pct
    return f"[{bar}]\nBUY {b_pct}%   SELL {s_pct}%   WAIT {n_pct}%"


def recommend_card(a: MarketAnalysis) -> str:
    ms = market_status()
    if not ms["is_open"]:
        mkt_line = f"! {ms['status_text']} — {ms['note']}"
    else:
        mkt_line = f"Market: {ms['note']}"

    lines = [
        "XAU/USD  RECOMMENDATION",
        "=" * 30,
        _verdict_block(a),
        "=" * 30,
        f"Price:    {fmt_price(a.price)}",
        f"TF:       {a.timeframe}",
        f"Bias:     {a.bias}   Strength: {a.strength}",
        mkt_line,
        "─" * 30,
        "INDICATOR CONSENSUS",
        _consensus_bar(a),
        "─" * 30,
        _indicator_bar(a.indicators),
        "─" * 30,
    ]

    if a.action in ("BUY", "SELL"):
        lines += [
            f"Entry:    {fmt_price(a.entry)}",
            f"SL:       {fmt_price(a.stop_loss)}",
            f"TP1:      {fmt_price(a.tp1)}",
            f"TP2:      {fmt_price(a.tp2)}",
            f"R:R       1:{a.rr_ratio}",
            "─" * 30,
            f"Reason: {a.verdict_reason[:48]}",
        ]
        if a.breakout:
            lines.append("Pattern:  Breakout confirmed")
        if a.reversal:
            lines.append("Pattern:  Reversal signal")
    else:
        lines += [
            f"Reason: {(a.wait_reason or a.verdict_reason)[:48]}",
            "No trade. Monitor for clearer setup.",
        ]

    return "<pre>" + "\n".join(lines) + "</pre>"


def alert_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"[CLOSED] {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = [
        "SIGNAL ALERT  |  XAU/USD",
        "=" * 30,
        f"[ {a.action} ]  Confidence: {a.confidence}%",
        "=" * 30,
        f"TF:         {a.timeframe}   ADX: {a.adx:.0f}",
        f"Price:      {fmt_price(a.price)}",
        f"Market:     {mkt}",
        "─" * 30,
        f"Entry:      {fmt_price(a.entry)}",
        f"SL:         {fmt_price(a.stop_loss)}",
        f"TP1:        {fmt_price(a.tp1)}",
        f"TP2:        {fmt_price(a.tp2)}",
        f"R:R         1:{a.rr_ratio}",
        "─" * 30,
        f"Bias:       {a.bias}   Strength: {a.strength}",
    ]
    if a.verdict_reason:
        lines.append(f"Reason: {a.verdict_reason[:48]}")
    lines += [
        "─" * 30,
        "Trade tracking is active. Win/loss",
        "result image sent on close.",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def analysis_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = [
        f"XAU/USD  |  {a.timeframe}",
        "─" * 26,
        f"Price:   {fmt_price(a.price)}",
        f"Market:  {mkt}",
        "─" * 26,
        f"Bias:    {a.bias}",
        f"Trend:   {a.trend}   ({a.strength})",
        f"ADX:     {a.adx:.1f}   BB%B: {a.bb_pct:.1f}",
        "─" * 26,
        f"Entry:   {fmt_price(a.entry)}",
        f"SL:      {fmt_price(a.stop_loss)}",
        f"TP1:     {fmt_price(a.tp1)}",
        f"TP2:     {fmt_price(a.tp2)}",
        f"R:R      1:{a.rr_ratio}",
        "─" * 26,
        _verdict_block(a),
    ]
    if a.action == "WAIT" and a.wait_reason:
        pass  # already in verdict block
    if a.breakout:
        lines.append("Pattern: Breakout detected")
    if a.reversal:
        lines.append("Pattern: Reversal signal")
    return "<pre>" + "\n".join(lines) + "</pre>"


def signal_card(a: MarketAnalysis) -> str:
    ms = market_status()
    if a.action == "WAIT":
        lines = [
            "TRADE SIGNAL  |  XAU/USD",
            "─" * 26,
            "[ WAIT ]  No entry",
            f"Reason:  {a.wait_reason or 'No clear setup'}",
            "─" * 26,
            f"Confidence: {a.confidence}%",
            f"ADX:        {a.adx:.1f}",
        ]
        if not ms["is_open"]:
            lines.insert(2, f"! {ms['status_text']} — {ms['note']}")
    else:
        lines = [
            "TRADE SIGNAL  |  XAU/USD",
            "─" * 26,
            f"[ {a.action} ]  {a.bias}  Confidence: {a.confidence}%",
            "─" * 26,
            f"Entry:   {fmt_price(a.entry)}",
            f"SL:      {fmt_price(a.stop_loss)}",
            f"TP1:     {fmt_price(a.tp1)}",
            f"TP2:     {fmt_price(a.tp2)}",
            f"R:R      1:{a.rr_ratio}",
            "─" * 26,
            f"ADX:     {a.adx:.1f}   TF: {a.timeframe}",
            f"Reason:  {a.verdict_reason[:44]}",
        ]
        if not ms["is_open"]:
            lines.append(f"! {ms['status_text']} — {ms['note']}")
    return "<pre>" + "\n".join(lines) + "</pre>"


def trend_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = [
        "XAU/USD  TREND",
        "─" * 26,
        f"Timeframe: {a.timeframe}",
        f"Market:    {mkt}",
        "─" * 26,
        f"Trend:     {a.trend}",
        f"Bias:      {a.bias}",
        f"Strength:  {a.strength}",
        f"Momentum:  {a.momentum}",
        f"ADX:       {a.adx:.1f}",
        "─" * 26,
        f"Price:     {fmt_price(a.price)}",
    ]
    if a.breakout:
        lines.append("Note:      Breakout in progress")
    if a.reversal:
        lines.append("Note:      Reversal signal present")
    return "<pre>" + "\n".join(lines) + "</pre>"


def levels_card(a: MarketAnalysis) -> str:
    lines = [
        f"KEY LEVELS  |  {a.timeframe}",
        "─" * 26,
        f"Resistance 2: {fmt_price(a.resistance2)}",
        f"Resistance 1: {fmt_price(a.resistance1)}",
        "─" * 12,
        f"  Price:     {fmt_price(a.price)}",
        f"  BB Upper:  {fmt_price(a.bb_pct)}%",
        "─" * 12,
        f"Support 1:    {fmt_price(a.support1)}",
        f"Support 2:    {fmt_price(a.support2)}",
        "─" * 26,
        f"Liquidity Zone: {a.liquidity_zone}",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def outlook_card(a: MarketAnalysis) -> str:
    tf_label = {
        "M5": "5-Min", "M15": "15-Min", "M30": "30-Min",
        "H1": "1-Hour", "H4": "4-Hour", "D1": "Daily"
    }.get(a.timeframe, a.timeframe)

    if a.bias == "Bullish":
        outlook_text = (
            f"Price targeting {fmt_price(a.tp1)} extension {fmt_price(a.tp2)}. "
            f"Key support at {fmt_price(a.support1)}."
        )
    elif a.bias == "Bearish":
        outlook_text = (
            f"Price targeting {fmt_price(a.tp1)} extension {fmt_price(a.tp2)}. "
            f"Key resistance at {fmt_price(a.resistance1)}."
        )
    else:
        outlook_text = (
            f"Ranging between {fmt_price(a.support1)} and {fmt_price(a.resistance1)}. "
            "Wait for directional break."
        )

    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]

    lines = [
        f"MARKET OUTLOOK  |  {tf_label}",
        "─" * 30,
        f"Bias:       {a.bias}",
        f"Trend:      {a.trend}  ({a.strength})",
        f"Momentum:   {a.momentum}   ADX: {a.adx:.1f}",
        f"Market:     {mkt}",
        "─" * 30,
        "Outlook:",
        outlook_text,
        "─" * 30,
        f"Confidence: {a.confidence}%",
        f"Action:     {a.action}",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def market_open_card(a: MarketAnalysis) -> str:
    """Weekly market-open notification card sent every Sunday when COMEX reopens."""
    lines = [
        "MARKET NOW OPEN  |  XAU/USD",
        "=" * 30,
        "New week. Fresh analysis.",
        "=" * 30,
        f"Price:      {fmt_price(a.price)}",
        f"Bias:       {a.bias}",
        f"Trend:      {a.trend}  ({a.strength})",
        f"ADX:        {a.adx:.1f}   BB%B: {a.bb_pct:.1f}",
        "─" * 30,
        _verdict_block(a),
        "─" * 30,
    ]
    if a.action in ("BUY", "SELL"):
        lines += [
            f"Entry:      {fmt_price(a.entry)}",
            f"SL:         {fmt_price(a.stop_loss)}",
            f"TP1:        {fmt_price(a.tp1)}",
            f"TP2:        {fmt_price(a.tp2)}",
            f"R:R         1:{a.rr_ratio}",
            "─" * 30,
            f"Reason: {a.verdict_reason[:48]}",
        ]
    else:
        lines += [
            f"Reason: {(a.wait_reason or a.verdict_reason)[:48]}",
            "No immediate setup. Monitor price action.",
        ]
    lines += [
        "─" * 30,
        f"R1: {fmt_price(a.resistance1)}   R2: {fmt_price(a.resistance2)}",
        f"S1: {fmt_price(a.support1)}   S2: {fmt_price(a.support2)}",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def weekly_closed_recap_text() -> str:
    """Brief message sent when market closes Friday to recap the week."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%A %d %b %Y  %H:%M UTC")
    lines = [
        "MARKET CLOSED  |  XAU/USD",
        "─" * 30,
        f"Time:   {now}",
        "─" * 30,
        "Gold futures closed for the weekend.",
        "Analysis resumes Sunday 6:00 PM ET.",
        "─" * 30,
        "Active trades remain tracked.",
        "Win/loss images fire when market",
        "reopens and price hits a level.",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def welcome_text(name: str) -> str:
    ms  = market_status()
    mkt = ("Market is OPEN — live analysis available."
           if ms["is_open"]
           else f"Market is CLOSED — {ms['note']}.")
    return (
        f"Welcome, {name}.\n\n"
        "<b>XAU/USD Gold Analysis Bot</b>\n\n"
        "Institutional-grade signals. Precision entries.\n"
        f"{mkt}\n\n"
        "Select an option from the menu below."
    )


def help_text() -> str:
    cmds = [
        ("/recommend", "BUY / SELL verdict with indicator breakdown"),
        ("/analyze",   "Full XAU/USD market analysis + ADX + BB"),
        ("/signal",    "Trade setup if conditions are met"),
        ("/trend",     "Current trend direction and strength"),
        ("/levels",    "Support, resistance, and BB levels"),
        ("/outlook",   "Market outlook report"),
        ("/alerts",    "Toggle automatic entry notifications"),
        ("/settings",  "Change timeframe"),
        ("/help",      "This message"),
    ]
    lines = ["<b>Available Commands</b>\n"]
    for cmd, desc in cmds:
        lines.append(f"{cmd}  —  {desc}")
    ms = market_status()
    lines += [
        "",
        f"<b>Market:</b> {ms['status_text']} — {ms['note']}",
    ]
    return "\n".join(lines)
