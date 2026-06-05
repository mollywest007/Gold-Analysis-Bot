from src.analysis.engine import MarketAnalysis


def fmt_price(p: float) -> str:
    return f"{p:.2f}"


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
        ("/analyze", "Full XAU/USD market analysis"),
        ("/signal", "Trade setup if conditions are met"),
        ("/trend", "Current trend direction"),
        ("/levels", "Support and resistance levels"),
        ("/outlook", "Market outlook report"),
        ("/settings", "Bot settings"),
        ("/help", "This message"),
    ]
    lines = ["<b>Available Commands</b>\n"]
    for cmd, desc in cmds:
        lines.append(f"{cmd}  —  {desc}")
    return "\n".join(lines)
