from src.analysis.engine import MarketAnalysis, Indicator


def fmt_price(p: float) -> str:
    return f"{p:.2f}"


def _indicator_bar(indicators) -> str:
    lines = []
    for ind in indicators:
        arrow = "^" if ind.signal == "BUY" else ("v" if ind.signal == "SELL" else "-")
        if ind.name == "EMA Cross":
            val_str = f"{ind.value:+.4f}"
        elif ind.name == "MACD":
            val_str = f"{ind.value:+.3f}"
        else:
            val_str = f"{ind.value:.1f}"
        lines.append(f"{ind.name:<12} {val_str:>8}   [{arrow}] {ind.signal}")
    return "\n".join(lines)


def recommend_card(a: MarketAnalysis) -> str:
    total = a.buy_votes + a.sell_votes + a.wait_votes
    buy_pct = int((a.buy_votes / total) * 100) if total else 0
    sell_pct = int((a.sell_votes / total) * 100) if total else 0

    bar_len = 20
    buy_blocks = round((a.buy_votes / max(total, 1)) * bar_len)
    sell_blocks = round((a.sell_votes / max(total, 1)) * bar_len)
    neut_blocks = bar_len - buy_blocks - sell_blocks
    consensus_bar = ("+" * buy_blocks) + ("-" * sell_blocks) + ("." * max(neut_blocks, 0))

    if a.action in ("BUY", "SELL"):
        verdict_line = f"VERDICT:  >>>  {a.action}  <<<"
    else:
        verdict_line = "VERDICT:  >>>  WAIT  <<<"

    lines = [
        "XAU/USD  RECOMMENDATION",
        "=" * 28,
        verdict_line,
        "=" * 28,
        f"Price:    {fmt_price(a.price)}",
        f"Timeframe:{a.timeframe}",
        "─" * 28,
        "INDICATOR CONSENSUS",
        f"[{consensus_bar}]",
        f"BUY {buy_pct}%   SELL {sell_pct}%   NEUTRAL {100-buy_pct-sell_pct}%",
        "─" * 28,
        _indicator_bar(a.indicators),
        "─" * 28,
    ]

    if a.action in ("BUY", "SELL"):
        lines += [
            f"Entry:    {fmt_price(a.entry)}",
            f"SL:       {fmt_price(a.stop_loss)}",
            f"TP1:      {fmt_price(a.tp1)}",
            f"TP2:      {fmt_price(a.tp2)}",
            f"R:R       1:{a.rr_ratio}",
            "─" * 28,
            f"Confidence: {a.confidence}%",
            f"Reason: {a.verdict_reason[:48]}",
        ]
        if a.breakout:
            lines.append("Pattern:  Breakout confirmed")
        if a.reversal:
            lines.append("Pattern:  Reversal signal")
    else:
        lines += [
            f"Confidence: {a.confidence}%",
            f"Reason: {(a.wait_reason or a.verdict_reason)[:48]}",
            "─" * 28,
            "No trade. Monitor for clearer setup.",
        ]

    return "<pre>" + "\n".join(lines) + "</pre>"


def analysis_card(a: MarketAnalysis) -> str:
    lines = [
        "XAU/USD  |  " + a.timeframe,
        "─" * 24,
        f"Price:   {fmt_price(a.price)}",
        f"Bias:    {a.bias}",
        f"Trend:   {a.trend}",
        f"Strength:{a.strength}",
        "─" * 24,
        f"Entry:   {fmt_price(a.entry)}",
        f"SL:      {fmt_price(a.stop_loss)}",
        f"TP1:     {fmt_price(a.tp1)}",
        f"TP2:     {fmt_price(a.tp2)}",
        f"R:R      1:{a.rr_ratio}",
        "─" * 24,
        f"Confidence: {a.confidence}%",
        f"Action:     {a.action}",
    ]
    if a.action == "WAIT" and a.wait_reason:
        lines.append(f"Reason:  {a.wait_reason}")
    if a.breakout:
        lines.append("Pattern: Breakout detected")
    if a.reversal:
        lines.append("Pattern: Reversal signal")
    return "<pre>" + "\n".join(lines) + "</pre>"


def signal_card(a: MarketAnalysis) -> str:
    if a.action == "WAIT":
        lines = [
            "TRADE SIGNAL",
            "─" * 24,
            "Action:  WAIT",
            f"Reason:  {a.wait_reason or 'No clear setup'}",
            "─" * 24,
            "Conditions not met for entry.",
            "Monitor price action.",
        ]
    else:
        lines = [
            "TRADE SIGNAL",
            "─" * 24,
            f"Action:  {a.action}",
            f"Entry:   {fmt_price(a.entry)}",
            f"SL:      {fmt_price(a.stop_loss)}",
            f"TP1:     {fmt_price(a.tp1)}",
            f"TP2:     {fmt_price(a.tp2)}",
            f"R:R      1:{a.rr_ratio}",
            "─" * 24,
            f"Confidence: {a.confidence}%",
            f"Timeframe:  {a.timeframe}",
        ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def trend_card(a: MarketAnalysis) -> str:
    lines = [
        "XAU/USD TREND",
        "─" * 24,
        f"Timeframe: {a.timeframe}",
        f"Trend:     {a.trend}",
        f"Bias:      {a.bias}",
        f"Strength:  {a.strength}",
        f"Momentum:  {a.momentum}",
        "─" * 24,
        f"Price:     {fmt_price(a.price)}",
    ]
    if a.breakout:
        lines.append("Note:      Breakout in progress")
    if a.reversal:
        lines.append("Note:      Reversal signal present")
    return "<pre>" + "\n".join(lines) + "</pre>"


def levels_card(a: MarketAnalysis) -> str:
    lines = [
        "KEY LEVELS  |  " + a.timeframe,
        "─" * 24,
        f"Resistance 2: {fmt_price(a.resistance2)}",
        f"Resistance 1: {fmt_price(a.resistance1)}",
        "─" * 12,
        f"  Price: {fmt_price(a.price)}",
        "─" * 12,
        f"Support 1:    {fmt_price(a.support1)}",
        f"Support 2:    {fmt_price(a.support2)}",
        "─" * 24,
        f"Liquidity Zone: {a.liquidity_zone}",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def outlook_card(a: MarketAnalysis) -> str:
    tf_label = {
        "M5": "5-Minute", "M15": "15-Minute", "M30": "30-Minute",
        "H1": "1-Hour", "H4": "4-Hour", "D1": "Daily"
    }.get(a.timeframe, a.timeframe)

    if a.bias == "Bullish":
        outlook_text = f"Price targeting {fmt_price(a.tp1)} with extension to {fmt_price(a.tp2)}. Key support at {fmt_price(a.support1)}."
    elif a.bias == "Bearish":
        outlook_text = f"Price targeting {fmt_price(a.tp1)} with extension to {fmt_price(a.tp2)}. Key resistance at {fmt_price(a.resistance1)}."
    else:
        outlook_text = f"Market ranging between {fmt_price(a.support1)} and {fmt_price(a.resistance1)}. Wait for breakout."

    lines = [
        f"MARKET OUTLOOK | {tf_label}",
        "─" * 28,
        f"Bias:      {a.bias}",
        f"Trend:     {a.trend}  ({a.strength})",
        f"Momentum:  {a.momentum}",
        "─" * 28,
        "Outlook:",
        outlook_text,
        "─" * 28,
        f"Confidence: {a.confidence}%",
        f"Action:     {a.action}",
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def welcome_text(name: str) -> str:
    return (
        f"Welcome, {name}.\n\n"
        "<b>XAU/USD Gold Analysis Bot</b>\n\n"
        "Professional market analysis for Gold vs USD.\n"
        "Institutional-grade signals. Precision entries.\n\n"
        "Select an option from the menu below."
    )


def help_text() -> str:
    cmds = [
        ("/recommend", "BUY / SELL verdict with indicator breakdown"),
        ("/analyze",   "Full XAU/USD market analysis"),
        ("/signal",    "Trade setup if conditions are met"),
        ("/trend",     "Current trend direction"),
        ("/levels",    "Support and resistance levels"),
        ("/outlook",   "Market outlook report"),
        ("/settings",  "Bot settings"),
        ("/help",      "This message"),
    ]
    lines = ["<b>Available Commands</b>\n"]
    for cmd, desc in cmds:
        lines.append(f"{cmd}  —  {desc}")
    return "\n".join(lines)
