from src.analysis.engine import MarketAnalysis, Indicator
from src.market_hours import market_status


def fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def _market_status_line() -> str:
    ms = market_status()
    if ms["is_open"]:
        return f"LIVE  |  {ms['note']}"
    return f"{ms['status_text']}  |  {ms['note']}"


def _closed_banner() -> str:
    ms = market_status()
    if ms["is_open"]:
        return ""
    return f"! {ms['status_text']} — {ms['note']}"


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
    if a.action == "BUY":
        trade_line = f"Type:  {a.trade_type}"
        return (
            "[ BUY  ]  ENTRY CONFIRMED\n"
            f"Confidence: {a.confidence}%   ADX: {a.adx:.0f}\n"
            f"{trade_line}"
        )
    if a.action == "SELL":
        trade_line = f"Type:  {a.trade_type}"
        return (
            "[ SELL ]  ENTRY CONFIRMED\n"
            f"Confidence: {a.confidence}%   ADX: {a.adx:.0f}\n"
            f"{trade_line}"
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


def _trade_type_label(a: MarketAnalysis) -> str:
    labels = {
        "Scalp":    "SCALP    (minutes–hours)",
        "Intraday": "INTRADAY (same session)",
        "Swing":    "SWING    (1–5 days)",
        "Position": "POSITION (weeks)",
    }
    return labels.get(a.trade_type, a.trade_type)


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
        ]
        if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
            lines.append(f"Limit:    {fmt_price(a.limit_entry)}  *better fill")
        lines += [
            f"SL:       {fmt_price(a.stop_loss)}",
            f"TP1:      {fmt_price(a.tp1)}",
            f"TP2:      {fmt_price(a.tp2)}",
            f"R:R       1:{a.rr_ratio}",
            "─" * 30,
            f"Type:     {_trade_type_label(a)}",
            f"Reason: {a.verdict_reason[:48]}",
        ]
        if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Inside Bar", "Spinning Top"):
            lines.append(f"Pattern:  {a.candle_pattern}")
        if a.breakout:
            lines.append("Signal:   Breakout confirmed")
        if a.reversal:
            lines.append("Signal:   Reversal detected")
    else:
        lines += [
            f"Reason: {(a.wait_reason or a.verdict_reason)[:48]}",
            "No trade. Monitor for clearer setup.",
        ]
        if a.candle_pattern and a.candle_pattern not in ("None",):
            lines.append(f"Pattern:  {a.candle_pattern}")

    return "<pre>" + "\n".join(lines) + "</pre>"


def alert_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"[CLOSED] {ms['note']}" if not ms["is_open"] else ms["note"]
    session_str = f"{a.session}" if a.session else ""
    htf_str     = f"HTF: {a.htf_bias}" if a.htf_bias and a.htf_bias != "Neutral" else ""
    context_line = "  |  ".join(filter(None, [session_str, htf_str])) or mkt
    lines = [
        "SIGNAL ALERT  |  XAU/USD",
        "=" * 30,
        f"[ {a.action} ]  {a.trade_type.upper()}",
        f"Confidence: {a.confidence}%   ADX: {a.adx:.0f}",
        "=" * 30,
        f"TF:         {a.timeframe}",
        f"Price:      {fmt_price(a.price)}",
        f"Session:    {context_line}",
        "─" * 30,
        f"Entry:      {fmt_price(a.entry)}",
    ]
    if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
        lines.append(f"Limit:      {fmt_price(a.limit_entry)}  *better fill")
    lines += [
        f"SL:         {fmt_price(a.stop_loss)}",
        f"TP1:        {fmt_price(a.tp1)}",
        f"TP2:        {fmt_price(a.tp2)}",
        f"R:R         1:{a.rr_ratio}",
        "─" * 30,
        f"Bias:       {a.bias}   Strength: {a.strength}",
    ]
    if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Inside Bar", "Spinning Top"):
        lines.append(f"Pattern:    {a.candle_pattern}")
    if a.verdict_reason:
        lines.append(f"Reason:     {a.verdict_reason[:46]}")
    return "<pre>" + "\n".join(lines) + "</pre>"


def analysis_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = [
        f"XAU/USD  |  {a.timeframe}",
        "─" * 30,
        f"Price:   {fmt_price(a.price)}",
        f"Market:  {mkt}",
        f"Session: {a.session or 'N/A'}",
        "─" * 30,
        f"Bias:    {a.bias}",
        f"HTF:     {a.htf_bias}",
        f"Trend:   {a.trend}   ({a.strength})",
        f"ADX:     {a.adx:.1f}   BB%B: {a.bb_pct:.1f}%",
        f"Votes:   BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
    ]
    if a.candle_pattern and a.candle_pattern not in ("None",):
        lines.append(f"Candle:  {a.candle_pattern}")
    lines += [
        "─" * 30,
        f"Type:    {_trade_type_label(a)}",
        "─" * 30,
        f"Entry:   {fmt_price(a.entry)}",
    ]
    if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry and a.action in ("BUY", "SELL"):
        lines.append(f"Limit:   {fmt_price(a.limit_entry)}  *better fill")
    lines += [
        f"SL:      {fmt_price(a.stop_loss)}",
        f"TP1:     {fmt_price(a.tp1)}",
        f"TP2:     {fmt_price(a.tp2)}",
        f"R:R      1:{a.rr_ratio}",
        "─" * 30,
        _verdict_block(a),
    ]
    if a.breakout:
        lines.append("Note:    Breakout detected")
    if a.reversal:
        lines.append("Note:    Reversal signal")
    if not ms["is_open"]:
        lines += ["─" * 30, f"! {ms['status_text']} — {ms['note']}"]
    return "<pre>" + "\n".join(lines) + "</pre>"


def signal_card(a: MarketAnalysis) -> str:
    ms = market_status()
    if a.action == "WAIT":
        lines = [
            "TRADE SIGNAL  |  XAU/USD",
            "─" * 30,
        ]
        if not ms["is_open"]:
            lines.append(f"! {ms['status_text']} — {ms['note']}")
        lines += [
            "[ WAIT ]  No entry",
            f"Reason:  {(a.wait_reason or 'No clear setup')[:46]}",
            "─" * 30,
            f"Confidence: {a.confidence}%   ADX: {a.adx:.1f}",
            f"Session:    {a.session or 'N/A'}",
            f"HTF Bias:   {a.htf_bias}",
            f"Votes:      BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
        ]
        if a.candle_pattern and a.candle_pattern not in ("None",):
            lines.append(f"Pattern:    {a.candle_pattern}")
    else:
        lines = [
            "TRADE SIGNAL  |  XAU/USD",
            "─" * 30,
        ]
        if not ms["is_open"]:
            lines.append(f"! {ms['status_text']} — {ms['note']}")
        lines += [
            f"[ {a.action} ]  {a.bias}  —  {a.trade_type.upper()}",
            f"Confidence: {a.confidence}%",
            "─" * 30,
            f"Entry:   {fmt_price(a.entry)}",
        ]
        if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
            lines.append(f"Limit:   {fmt_price(a.limit_entry)}  *better fill")
        lines += [
            f"SL:      {fmt_price(a.stop_loss)}",
            f"TP1:     {fmt_price(a.tp1)}",
            f"TP2:     {fmt_price(a.tp2)}",
            f"R:R      1:{a.rr_ratio}",
            "─" * 30,
            f"ADX:     {a.adx:.1f}   TF: {a.timeframe}",
            f"Session: {a.session or 'N/A'}   HTF: {a.htf_bias}",
            f"Votes:   BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
        ]
        if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Inside Bar", "Spinning Top"):
            lines.append(f"Pattern: {a.candle_pattern}")
        lines.append(f"Reason:  {a.verdict_reason[:46]}")
    return "<pre>" + "\n".join(lines) + "</pre>"


def trend_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    lines = [
        "XAU/USD  TREND",
        "─" * 28,
        f"Timeframe: {a.timeframe}",
        f"Market:    {mkt}",
        "─" * 28,
        f"Trend:     {a.trend}",
        f"Bias:      {a.bias}",
        f"HTF Bias:  {a.htf_bias}",
        f"Strength:  {a.strength}",
        f"Momentum:  {a.momentum}",
        f"ADX:       {a.adx:.1f}",
        f"Session:   {a.session or 'N/A'}",
        "─" * 28,
        f"Price:     {fmt_price(a.price)}",
        f"EMA Votes: BUY {a.buy_votes}/5  SELL {a.sell_votes}/5",
    ]
    if a.candle_pattern and a.candle_pattern not in ("None",):
        lines.append(f"Pattern:   {a.candle_pattern}")
    if a.breakout:
        lines.append("Note:      Breakout in progress")
    if a.reversal:
        lines.append("Note:      Reversal signal present")
    return "<pre>" + "\n".join(lines) + "</pre>"


def levels_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    bb_upper = a.bb_upper if a.bb_upper else 0.0
    bb_lower = a.bb_lower if a.bb_lower else 0.0
    atr_val = a.atr if a.atr else 0.0
    lines = [
        f"KEY LEVELS  |  {a.timeframe}",
        "─" * 28,
        f"Market:       {mkt}",
        "─" * 28,
        f"Resistance 2: {fmt_price(a.resistance2)}",
        f"Resistance 1: {fmt_price(a.resistance1)}",
        f"BB Upper:     {fmt_price(bb_upper)}",
        "─" * 12,
        f"  Price:      {fmt_price(a.price)}",
        f"  BB%B:       {a.bb_pct:.1f}%",
        "─" * 12,
        f"BB Lower:     {fmt_price(bb_lower)}",
        f"Support 1:    {fmt_price(a.support1)}",
        f"Support 2:    {fmt_price(a.support2)}",
        "─" * 28,
        f"Liquidity Zone: {a.liquidity_zone}",
        f"ATR:          {fmt_price(atr_val)}",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def outlook_card(a: MarketAnalysis) -> str:
    tf_label = {
        "M5": "5-Min", "M15": "15-Min", "M30": "30-Min",
        "H1": "1-Hour", "H4": "4-Hour", "D1": "Daily"
    }.get(a.timeframe, a.timeframe)

    if a.bias == "Bullish":
        outlook_text = (
            f"Price targeting {fmt_price(a.tp1)}, extension {fmt_price(a.tp2)}. "
            f"Key support at {fmt_price(a.support1)}."
        )
    elif a.bias == "Bearish":
        outlook_text = (
            f"Price targeting {fmt_price(a.tp1)}, extension {fmt_price(a.tp2)}. "
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
        "─" * 32,
        f"Market:     {mkt}",
        "─" * 32,
        f"Bias:       {a.bias}",
        f"HTF Bias:   {a.htf_bias}",
        f"Trend:      {a.trend}  ({a.strength})",
        f"Momentum:   {a.momentum}   ADX: {a.adx:.1f}",
        f"Session:    {a.session or 'N/A'}",
        "─" * 32,
        "Outlook:",
        outlook_text,
        "─" * 32,
        f"Confidence: {a.confidence}%",
        f"Action:     {a.action}",
        f"Type:       {_trade_type_label(a)}",
    ]
    if a.candle_pattern and a.candle_pattern not in ("None",):
        lines.append(f"Pattern:    {a.candle_pattern}")
    return "<pre>" + "\n".join(lines) + "</pre>"


def market_open_card(a: MarketAnalysis) -> str:
    lines = [
        "MARKET NOW OPEN  |  XAU/USD",
        "=" * 30,
        "New week. Fresh analysis.",
        "=" * 30,
        f"Price:      {fmt_price(a.price)}",
        f"Bias:       {a.bias}",
        f"HTF Bias:   {a.htf_bias}",
        f"Trend:      {a.trend}  ({a.strength})",
        f"ADX:        {a.adx:.1f}   BB%B: {a.bb_pct:.1f}%",
        "─" * 30,
        _verdict_block(a),
        "─" * 30,
    ]
    if a.action in ("BUY", "SELL"):
        lines += [
            f"Entry:      {fmt_price(a.entry)}",
        ]
        if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
            lines.append(f"Limit:      {fmt_price(a.limit_entry)}  *better fill")
        lines += [
            f"SL:         {fmt_price(a.stop_loss)}",
            f"TP1:        {fmt_price(a.tp1)}",
            f"TP2:        {fmt_price(a.tp2)}",
            f"R:R         1:{a.rr_ratio}",
            "─" * 30,
            f"Type:       {_trade_type_label(a)}",
            f"Reason: {a.verdict_reason[:48]}",
        ]
        if a.candle_pattern and a.candle_pattern not in ("None", "Doji", "Inside Bar", "Spinning Top"):
            lines.append(f"Pattern:    {a.candle_pattern}")
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
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def welcome_text(name: str) -> str:
    ms  = market_status()
    if ms["is_open"]:
        mkt = f"Market is OPEN — {ms['note']}. Live analysis available."
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
        ("/recommend", "BUY / SELL verdict with indicator breakdown"),
        ("/analyze",   "Full market analysis — bias, trend, entry, SL, TP"),
        ("/signal",    "Trade setup (Scalp / Intraday / Swing / Position)"),
        ("/trend",     "Current trend direction and strength"),
        ("/levels",    "Support, resistance, BB, and liquidity zones"),
        ("/outlook",   "Market outlook report"),
        ("/alerts",    "Toggle automatic entry notifications"),
        ("/settings",  "Change timeframe"),
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
        "Scalp    — M5/M15 (minutes–hours)",
        "Intraday — M30/H1 (same session)",
        "Swing    — H1/H4  (1–5 days)",
        "Position — D1     (weeks)",
    ]
    return "\n".join(lines)
