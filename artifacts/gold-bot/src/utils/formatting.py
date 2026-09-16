import html
import math
import time
import textwrap
from src.analysis.engine import MarketAnalysis, Indicator
from src.analysis.modes import MODES
from src.market_hours import market_status
from src.mode_manager import get_mode_config

TG_MSG_LIMIT = 4080   # Telegram hard limit is 4096; leave 16 chars headroom


def _esc(s) -> str:
    """HTML-escape dynamic text before inserting into <pre> blocks."""
    return html.escape(str(s)) if s else ""


def safe_html(text: str, limit: int = TG_MSG_LIMIT) -> str:
    """
    Ensure a Telegram HTML message never exceeds the 4096-char limit.
    If it would, truncate inside the last <pre> block and close it cleanly.
    """
    if len(text) <= limit:
        return text
    # Truncate and close any open <pre> tag
    truncated = text[:limit - 60]
    # Find the last safe newline to avoid cutting mid-line
    cut = truncated.rfind("\n")
    if cut > 0:
        truncated = truncated[:cut]
    return truncated + "\n… (truncated — use individual TF commands for full detail)\n</pre>"


def _wrap_text(text: str, width: int = 34) -> list[str]:
    """Wrap narrative lines so Telegram's monospace report stays readable."""
    return textwrap.wrap(
        str(text),
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


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


def _mtf_chain_text(report: dict) -> str:
    """Render the strict Daily → H4 → H1 → M15 status in one short line."""
    directions = report.get("directions", {}) if report else {}
    scores = report.get("scores", {}) if report else {}
    if not directions:
        return "Not available"
    return " | ".join(
        f"{tf} {directions.get(tf, 'NO DATA')}/{scores.get(tf, 0)}"
        for tf in ("D1", "H4", "H1", "M15")
    )


def _trade_type_label(a: MarketAnalysis) -> str:
    return {"Scalp": "SCALP (minutes-hours)", "Intraday": "INTRADAY (same session)",
            "Swing": "SWING (1-5 days)", "Position": "POSITION (weeks)"}.get(a.trade_type, a.trade_type)


def _detail_value(value, fallback: str = "Not used") -> str:
    """Keep transparency panels honest when an engine field is unavailable."""
    if value is None or value == "" or value == "NONE":
        return fallback
    return str(value)


def _detail_price(value: float) -> str:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0
    return fmt_price(value) if value > 0 else "Not formed"


def _path_market_data_lines(
    a: MarketAnalysis,
    report: dict,
    direction: str,
    *,
    section: str,
) -> list[str]:
    """Render the raw computed inputs used by either entry path.

    This deliberately reads the serialised institutional report instead of
    restating a narrative conclusion.  The same evidence is shown under both
    paths so the user can compare the gates without guessing which data was
    reused.
    """
    structure = report.get("market_structure", {}) or {}
    levels = report.get("support_resistance", {}) or {}
    smc = report.get("smc", {}) or {}
    volume = report.get("volume", {}) or {}
    momentum = report.get("momentum", {}) or {}
    session = report.get("session", {}) or {}
    moving = report.get("moving_averages", {}) or {}
    pivot = levels.get("pivot_points", {}) or {}
    range_boundaries = levels.get("range_boundaries", {}) or {}
    ob = smc.get("order_block", {}) or {}
    fvg = smc.get("fvg", {}) or {}
    if not isinstance(ob, dict):
        ob = {}
    if not isinstance(fvg, dict):
        fvg = {}

    return [
        "",
        f"  {section} — ACTUAL MARKET DATA",
        f"  Price / TF          : {_detail_price(a.price)} / {a.timeframe}",
        f"  Market structure    : {_detail_value(structure.get('trend'), a.market_structure)} "
        f"/ {a.trend} | BOS {_detail_value(structure.get('bos'))} "
        f"CHoCH {_detail_value(structure.get('choch'))} MSS {_detail_value(structure.get('mss'))}",
        f"  Structure flags     : HH {structure.get('higher_high', False)} "
        f"HL {structure.get('higher_low', False)} | LH {structure.get('lower_high', False)} "
        f"LL {structure.get('lower_low', False)}",
        f"  Support / resistance: S2 {_detail_price(a.support2)} S1 {_detail_price(a.support1)} "
        f"R1 {_detail_price(a.resistance1)} R2 {_detail_price(a.resistance2)} "
        f"| range {range_boundaries.get('low', 'Not used')}–{range_boundaries.get('high', 'Not used')} "
        f"| P {_detail_value(pivot.get('p'))}",
        f"  Momentum / indicators: {a.momentum} | RSI {a.rsi_value:.2f} "
        f"MACD {a.macd_hist:+.4f} Stoch {a.stoch_k_val:.2f} "
        f"+DI/-DI {a.plus_di:.1f}/{a.minus_di:.1f} ADX {a.adx:.1f}",
        f"  Trend indicators    : EMA20 {moving.get('ema20', 'Not used')} "
        f"EMA50 {moving.get('ema50', 'Not used')} VWAP {_detail_price(a.vwap)} "
        f"Supertrend {_detail_value(a.supertrend_direction)}",
        f"  Volume / profile    : current {_detail_value(volume.get('current'))} "
        f"avg20 {_detail_value(volume.get('average_prior_20'))} "
        f"ratio {_detail_value(volume.get('current_vs_average'))}x "
        f"delta {_detail_value(volume.get('delta'))}",
        f"  Volume conditions   : spike {volume.get('spike', 'Not used')} "
        f"breakout {volume.get('high_volume_breakout', 'Not used')} "
        f"divergence {_detail_value(volume.get('volume_divergence'))} "
        f"POC {_detail_value((volume.get('profile', {}) or {}).get('poc'))}",
        f"  Liquidity / SMC     : sweep {_detail_value(smc.get('liquidity_sweep'))} "
        f"buy {_detail_value(smc.get('buy_side_liquidity'))} sell {_detail_value(smc.get('sell_side_liquidity'))} "
        f"| OB {_detail_value(ob.get('direction'))}/{_detail_value(ob.get('freshness'))} "
        f"| FVG {_detail_value(fvg.get('direction', smc.get('fair_value_gap')))}",
        f"  Conditions / data   : regime {getattr(a, 'market_regime', 'Not used')} "
        f"ATR {_detail_price(a.atr)} BB {getattr(a, 'bb_bandwidth', 0.0):.3f}% "
        f"session {session.get('name', a.session or 'Not used')} KZ {session.get('kill_zone', 'Not used')} "
        f"| macro {_detail_value(report.get('macro', {}).get('status', a.macro_status))} "
        f"intermarket {_detail_value(report.get('intermarket', {}).get('status', a.intermarket_status))} "
        f"| {report.get('data_quality', 'UNKNOWN')}",
    ]


def _indicator_transparency_lines(a: MarketAnalysis, direction: str) -> list[str]:
    """Show the indicator values and whether each one actually voted.

    A neutral indicator is still displayed because it is evidence that did not
    contribute to the directional decision, not a missing value.
    """
    rows = [
        "",
        "  INDICATORS — VALUE / SIGNAL / WEIGHT",
    ]
    indicators = list(getattr(a, "indicators", []) or [])
    if not indicators:
        return rows + ["  Not used            : no indicator snapshot returned"]
    for indicator in indicators:
        signal = str(getattr(indicator, "signal", "") or "Not used")
        value = getattr(indicator, "value", "Not used")
        if isinstance(value, (int, float)):
            value_text = f"{value:+.4f}" if "MACD" in indicator.name else f"{value:.4f}"
        else:
            value_text = _detail_value(value)
        weight = getattr(indicator, "weight", "Not used")
        contribution = (
            "USED"
            if direction in ("BUY", "SELL") and signal == direction
            else "Not used"
        )
        rows.append(
            f"  {indicator.name:<18}: {value_text} / {signal} / "
            f"wt {_detail_value(weight)} / {contribution}"
        )
    return rows


def _path_plan_lines(
    a: MarketAnalysis,
    *,
    entry: float,
    label: str,
) -> list[str]:
    """Render a plan without implying that a provisional plan is active."""
    stop = float(getattr(a, "stop_loss", 0.0) or 0.0)
    risk = abs(entry - stop) if entry and stop else 0.0
    def _rr(target: float) -> str:
        return f"1:{abs(target - entry) / risk:.1f}" if risk and target > 0 else "Not formed"

    rr1 = _rr(float(getattr(a, "tp1", 0.0) or 0.0))
    rr2 = _rr(float(getattr(a, "tp2", 0.0) or 0.0))
    rr3 = _rr(float(getattr(a, "tp3", 0.0) or 0.0))
    rr_text = (
        f"TP1 {rr1} | TP2 {rr2} | TP3 {rr3}"
        if risk
        else "Not formed"
    )
    return [
        "",
        f"  {label} — PRICE PLAN",
        f"  Entry              : {_detail_price(entry)}",
        f"  Stop Loss          : {_detail_price(stop)}",
        f"  Take Profit        : TP1 {_detail_price(getattr(a, 'tp1', 0.0))} | "
        f"TP2 {_detail_price(getattr(a, 'tp2', 0.0))} | TP3 {_detail_price(getattr(a, 'tp3', 0.0))}",
        f"  Risk / reward      : {rr_text}",
        f"  Confidence score   : {int(getattr(a, 'confidence_score', 0) or 0)}/100 "
        f"| legacy {int(getattr(a, 'confidence', 0) or 0)}% "
        f"| risk {_detail_value(getattr(a, 'risk_level', ''))}",
    ]


def _early_trigger_lines(
    a: MarketAnalysis,
    report: dict,
    direction: str,
    *,
    data_is_real: bool,
    watch_ready: bool,
) -> list[str]:
    """Show every Early Watch boolean and its live value."""
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    score = int(getattr(a, "confidence_score", 0) or 0)
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    evidence = _early_evidence(a, direction)
    structure = report.get("market_structure", {}) or {}
    smc = report.get("smc", {}) or {}
    return [
        "",
        "  EARLY WATCH — EXACT TRIGGER EVALUATION",
        f"  Result             : {'READY' if watch_ready else 'FORMING / NOT READY'}",
        f"  Real OHLCV         : {'PASS' if data_is_real else 'MISSING'} "
        f"({report.get('data_quality', 'UNKNOWN')})",
        f"  Direction          : {'PASS' if direction in ('BUY', 'SELL') else 'MISSING'} "
        f"({direction})",
        f"  Score >= 55        : {'PASS' if score >= 55 else 'MISSING'} ({score}/100)",
        f"  Directional votes  : {'PASS' if matching_votes >= 2 else 'MISSING'} "
        f"({direction} {matching_votes} | BUY {buy_votes} / SELL {sell_votes})",
        f"  ADX >= 18          : {'PASS' if adx >= 18 else 'MISSING'} ({adx:.1f})",
        f"  Structure evidence : {direction} BOS "
        f"{'PASS' if _aligned_bos(a, direction) else 'MISSING'} "
        f"({_detail_value(structure.get('bos', getattr(a, 'bos', 'NONE')))})",
        f"  Reversal evidence  : {direction} CHoCH "
        f"{'PASS' if _aligned_choch(a, direction) else 'MISSING'} "
        f"({_detail_value(structure.get('choch', getattr(a, 'choch', 'NONE')))})",
        f"  Liquidity evidence : {_detail_value(smc.get('liquidity_sweep'))}",
        f"  Triggered factors  : {', '.join(evidence) if evidence else 'None'}",
        "  Rule               : real data + BUY/SELL + score >=55 + at least one "
        "directional evidence factor (votes, ADX, BOS, or CHoCH)",
    ]


def _aligned_bos(a: MarketAnalysis, direction: str) -> bool:
    return (
        direction == "BUY" and getattr(a, "bos", "NONE") == "BULLISH_BOS"
    ) or (
        direction == "SELL" and getattr(a, "bos", "NONE") == "BEARISH_BOS"
    )


def _aligned_choch(a: MarketAnalysis, direction: str) -> bool:
    return (
        direction == "BUY" and getattr(a, "choch", "NONE") == "BULLISH_CHOCH"
    ) or (
        direction == "SELL" and getattr(a, "choch", "NONE") == "BEARISH_CHOCH"
    )


def _early_evidence(a: MarketAnalysis, direction: str) -> list[str]:
    """Return only direction-aligned factors used by the early-watch gate."""
    if direction not in ("BUY", "SELL"):
        return []
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    if adx >= 18:
        evidence.append(f"ADX {adx:.1f}")
    if _aligned_choch(a, direction):
        evidence.append(f"{direction} CHoCH")
    if _aligned_bos(a, direction):
        evidence.append(f"{direction} BOS")
    return evidence


def _strict_confirmation_lines(
    a: MarketAnalysis,
    report: dict,
    direction: str,
    *,
    data_is_real: bool,
    strict_ready: bool,
) -> list[str]:
    """Show strict confirmation as pass/missing checks with actual values."""
    # Ignore diagnostic multi-timeframe payloads here.  The user-facing
    # confirmation card must always describe only the selected timeframe.
    directions = {}
    scores = {}
    legacy = report.get("legacy", {}) or {}
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    direction_text = direction if direction in ("BUY", "SELL") else "WAIT"
    scope_check = f"selected timeframe data real: {'PASS' if data_is_real else 'MISSING'}"
    checks = [
        f"real OHLCV: {'PASS' if data_is_real else 'MISSING'}",
        scope_check,
        f"direction: {'PASS' if direction in ('BUY', 'SELL') else 'MISSING'} ({direction_text})",
        f"active score >=60: {'PASS' if int(getattr(a, 'confidence_score', 0) or 0) >= 60 else 'MISSING'} "
        f"({int(getattr(a, 'confidence_score', 0) or 0)}/100)",
        "HTF bias: NOT USED (selected timeframe only)",
        f"legacy conflict cleared: {'PASS' if legacy.get('confirmation', getattr(a, 'legacy_confirmation', 'NEUTRAL')) != 'CONFLICT' else 'MISSING'} "
        f"({legacy.get('confirmation', getattr(a, 'legacy_confirmation', 'NEUTRAL'))})",
        f"final action: {'PASS' if strict_ready else 'MISSING'} "
        f"({getattr(a, 'action', 'WAIT')} | mode-local)",
    ]
    rows = [
        "",
        "  STRICT CONFIRMED — EXACT CONFIRMATION",
        f"  Result             : {'CONFIRMED ' + getattr(a, 'action', direction) if strict_ready else 'REJECTED / WAITING'}",
        "  Additional gate    : selected mode timeframe only; "
        "local evidence and legacy conflict must pass.",
        *[f"  Check              : {check}" for check in checks],
    ]
    confirmed = [
        check
        for check in checks
        if ": PASS" in check or "PASS" in check
    ] + [
        f"{tf}={directions.get(tf, 'NO DATA')}/{int(scores.get(tf, 0) or 0)}"
        for tf in ("D1", "H4", "H1", "M15")
        if direction in ("BUY", "SELL")
        and directions.get(tf) == direction
        and int(scores.get(tf, 0) or 0) >= 60
    ]
    rows += [
        f"  MTF aligned flag    : {multi.get('aligned', 'Not used')}",
        f"  Confirmed conditions: {', '.join(confirmed) if confirmed else 'None'}",
        "  Still missing       : " + (
            "; ".join(
                [check for check in checks if "MISSING" in check]
                + [
                    f"{tf}={directions.get(tf, 'NO DATA')}/{int(scores.get(tf, 0) or 0)}"
                    for tf in ("D1", "H4", "H1", "M15")
                    if not (
                        direction in ("BUY", "SELL")
                        and directions.get(tf) == direction
                        and int(scores.get(tf, 0) or 0) >= 60
                    )
                ]
            )
            or "None"
        ),
    ]
    return rows


def _path_invalidation_lines(
    a: MarketAnalysis,
    *,
    strict_ready: bool,
    watch_ready: bool,
) -> list[str]:
    invalidating = [str(item) for item in (getattr(a, "invalidating_conditions", []) or [])]
    if not invalidating:
        invalidating = [
            "Real OHLCV becomes unavailable",
            "Directional indication changes or score falls below the applicable gate",
            "Higher-timeframe structure no longer supports the direction",
        ]
    if not strict_ready:
        invalidating.append("Strict gate remains incomplete; no active trade may be opened")
    return [
        "",
        "  INVALIDATION / DECISION",
        f"  Early watch state  : {'READY' if watch_ready else 'FORMING'}",
        f"  Strict state        : {'CONFIRMED' if strict_ready else 'WAITING'}",
        "  Conditions          : " + "; ".join(invalidating[:6]),
        f"  Engine reason       : {(getattr(a, 'wait_reason', '') or getattr(a, 'verdict_reason', '') or 'No additional reason returned')[:180]}",
    ]


_TF_MINUTES = {
    "M1": 1, "M3": 3, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}

_TF_RANK = {
    "M1": 1, "M3": 2, "M5": 3, "M15": 4, "M30": 5,
    "H1": 6, "H4": 7, "D1": 8, "W1": 9, "MN1": 10,
}


def timeframe_rank(timeframe: str) -> int:
    """Return the common structural rank used by cards and alert references."""
    return _TF_RANK.get(timeframe, 0)


def _mode_config_for_analysis(a: MarketAnalysis = None):
    mode_name = getattr(a, "analysis_mode", None) if a is not None else None
    return MODES.get(mode_name, get_mode_config())


def _mode_line(a: MarketAnalysis = None) -> str:
    cfg = _mode_config_for_analysis(a)
    return f"  Mode      : {cfg.emoji} {cfg.label}"


def _mode_risk_line(a: MarketAnalysis = None) -> str:
    """Expose the active profile's risk/holding guidance on trade plans."""
    note = _mode_config_for_analysis(a).risk_note
    return f"  Risk Plan : {note}" if note else ""


def _entry_confirmation_lines(
    a: MarketAnalysis,
    *,
    compact: bool = False,
) -> list[str]:
    """Explain the live conditions still blocking an entry alert.

    This is intentionally derived from the same fields used by the combined
    decision and alert-quality gates.  It must not imply that a score alone
    creates an entry.
    """
    report = getattr(a, "institutional_report", {}) or {}
    institutional_direction = str(
        report.get(
            "direction",
            getattr(a, "institutional_direction", "WAIT"),
        )
        or "WAIT"
    ).upper()
    action = str(getattr(a, "action", "WAIT") or "WAIT").upper()
    score = int(getattr(a, "confidence_score", 0) or 0)
    legacy = report.get("legacy", {}) or {}
    legacy_confirmation = str(
        legacy.get(
            "confirmation",
            getattr(a, "legacy_confirmation", "NEUTRAL"),
        )
        or "NEUTRAL"
    ).upper()
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and report.get("data_quality", "REAL_OHLCV") == "REAL_OHLCV"
    )

    reasons = [
        str(reason).strip()
        for reason in (
            getattr(a, "reasons_against", None)
            or report.get("reasons_against", [])
            or []
        )
        if str(reason).strip()
    ]
    waiting_for: list[str] = []

    if not data_is_real:
        waiting_for.append("real market data")
    if action not in ("BUY", "SELL"):
        if institutional_direction not in ("BUY", "SELL"):
            waiting_for.extend(reasons[:2])
            if score < 60:
                waiting_for.append(f"score to reach 60/100 (currently {score}/100)")
            if not reasons:
                waiting_for.append("a clear BUY or SELL direction")
        elif score < 60:
            waiting_for.append(f"score to reach 60/100 (currently {score}/100)")
        if legacy_confirmation == "CONFLICT":
            waiting_for.append("legacy indicators to stop conflicting")
    else:
        cfg = _mode_config_for_analysis(a)
        grade = str(
            getattr(a, "setup_quality", "")
            or getattr(a, "setup_grade", "WAIT")
            or "WAIT"
        )
        win_probability = int(getattr(a, "win_probability", 0) or 0)
        choch = str(getattr(a, "choch", "") or "")
        direction = action
        choch_aligned = (
            (direction == "BUY" and choch == "BULLISH_CHOCH")
            or (direction == "SELL" and choch == "BEARISH_CHOCH")
        )
        standard_quality = (
            win_probability >= cfg.alert_min_win_probability
            and grade in cfg.alert_min_grades
        )
        institutional_lead_quality = (
            legacy_confirmation == "NEUTRAL"
            and score >= 60
            and grade in cfg.alert_min_grades
        )
        choch_quality = (
            choch_aligned
            and win_probability >= max(50, cfg.alert_min_win_probability - 4)
            and grade in (*cfg.alert_min_grades, "B")
        )
        if not (standard_quality or institutional_lead_quality or choch_quality):
            waiting_for.append(
                f"alert quality (grade {grade}, win {win_probability}%/"
                f"{cfg.alert_min_win_probability}% minimum)"
            )

    # Keep the section readable and avoid repeating the same blocker.
    deduped: list[str] = []
    for item in waiting_for:
        if item not in deduped:
            deduped.append(item)
    waiting_for = deduped[:3]

    if action in ("BUY", "SELL") and not waiting_for:
        status = f"✅ CONFIRMED {action} — alert checks passed"
        waiting_text = "Nothing — entry confirmation is complete"
    elif action in ("BUY", "SELL"):
        status = f"⚠️ {action} found — alert gate not ready"
        waiting_text = "; ".join(waiting_for) or "final alert checks"
    else:
        status = "⏳ WAITING"
        waiting_text = "; ".join(waiting_for) or "entry confirmation"

    if compact:
        return [
            f"Entry wait : {waiting_text}",
            f"Score gate : {score}/100  (need 60+)",
            f"Entry state: {status}",
        ]

    sep = "─" * 34
    return [
        "",
        "  ENTRY CONFIRMATION",
        sep,
        f"  Status    : {status}",
        f"  Waiting for: {waiting_text}",
        f"  Score gate: {score}/100  (need 60+)",
        f"  Legacy gate: "
        f"{'PASS — no conflict' if legacy_confirmation != 'CONFLICT' else 'WAIT — conflict detected'}",
    ]


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
    willr_caution  = getattr(a, "willr_caution", "")
    st_direction   = getattr(a, "supertrend_direction", "NEUTRAL")
    for ind in a.indicators:
        arrow = "BUY" if ind.signal == "BUY" else ("SELL" if ind.signal == "SELL" else "----")
        if ind.name == "BB %B":
            val_str = f"{ind.value:.1f}%"
        elif ind.name == "MACD":
            val_str = f"{ind.value:+.3f}"
        elif ind.name == "Williams%R":
            val_str = f"{ind.value:.1f}"
            suffix  = f"  ← {willr_caution}" if willr_caution else ""
            rows.append(f"  {ind.name:<12} {val_str:>8}   {arrow}{suffix}")
            continue
        elif ind.name == "Supertrend":
            st_arrow = "▲ bullish" if st_direction == "BUY" else ("▼ bearish" if st_direction == "SELL" else "neutral")
            rows.append(f"  {'Supertrend':<12} {'':>8}   {arrow}  ({st_arrow})")
            continue
        elif ind.name == "CCI(20)":
            val_str = f"{ind.value:.0f}"
        elif ind.name in ("Chart Pat", "Hidden Div", "RSI Div", "Candle"):
            val_str = ""
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
             "EQUILIBRIUM": "◆ EQUILIBRIUM — consolidation zone"}
    return f"  Regime    : {icons.get(pd, pd)}"


def _wait_status(a: MarketAnalysis) -> tuple[str, str]:
    """Explain why the complete setup is not yet valid for an entry."""
    direction = getattr(a, "directional_indication", "NEUTRAL")
    score = getattr(a, "confidence_score", 0)
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    legacy_confirmation = getattr(a, "legacy_confirmation", "NEUTRAL")

    if direction not in ("BUY", "SELL"):
        return "NO DIRECTION", getattr(
            a, "wait_reason", ""
        ) or getattr(a, "verdict_reason", "") or "Evidence is mixed."

    if score < 60:
        return (
            f"{direction} FORMING",
            f"Monitoring: institutional evidence is {score}/100; 60/100 is required.",
        )
    if legacy_confirmation == "CONFLICT":
        return (
            f"{direction} FORMING",
            f"Monitoring: institutional {direction} conflicts with legacy indicators.",
        )
    return (
        f"{direction} FORMING",
        "Monitoring: the selected mode's local evidence is not complete.",
    )


def _entry_criteria_reason(a: MarketAnalysis, data_is_real: bool) -> str:
    """Return the active criteria that still prevent a valid entry."""
    report = getattr(a, "institutional_report", {}) or {}
    multi = report.get("multi_timeframe", {}) or {}
    directions = multi.get("directions", {}) or {}
    scores = multi.get("scores", {}) or {}
    direction = getattr(a, "directional_indication", "NEUTRAL")
    legacy = report.get("legacy", {}) or {}
    legacy_confirmation = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    blockers = []

    if not data_is_real:
        blockers.append("real OHLCV data unavailable")
    if direction not in ("BUY", "SELL"):
        blockers.append("no valid BUY/SELL direction")
    if int(getattr(a, "confidence_score", 0) or 0) < 60:
        blockers.append(
            f"active timeframe score {int(getattr(a, 'confidence_score', 0) or 0)}/60"
        )
    if legacy_confirmation == "CONFLICT":
        blockers.append("legacy conflict")

    return "; ".join(blockers[:6]) if blockers else "all entry criteria passed"


def _decision_summary_lines(a: MarketAnalysis) -> list[str]:
    """Keep the most important decision state near the top of the full card."""
    report = getattr(a, "institutional_report", {}) or {}
    direction = getattr(a, "directional_indication", "NEUTRAL")
    action = getattr(a, "action", "WAIT")
    score = int(getattr(a, "confidence_score", 0) or 0)
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    choch = getattr(a, "choch", "NONE")
    bos = getattr(a, "bos", "NONE")
    evidence = []
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    if adx >= 18:
        evidence.append(f"ADX {adx:.1f}")
    if choch != "NONE":
        evidence.append(choch.replace("_", " "))
    if bos in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(bos.replace("_", " "))
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and report.get("data_quality", "REAL_OHLCV") == "REAL_OHLCV"
    )
    setup_ready = (
        data_is_real
        and direction in ("BUY", "SELL")
        and score >= 55
        and bool(evidence)
    )
    legacy = report.get("legacy", {}) or {}
    legacy_confirmation = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    return [
        "",
        "──────────────────────────────────",
        "  DECISION SUMMARY",
        "──────────────────────────────────",
        f"  Entry decision  : "
        f"{action if action in ('BUY', 'SELL') else 'WAITING'}",
        f"  Setup state     : "
        f"{'READY ' + direction if setup_ready else 'FORMING'}",
        f"  Score           : {score}/100  (minimum 60 for entry)",
        f"  Mode scope      : {a.timeframe} only ({direction}/{score})",
        "  Other timeframes: Not used for this entry decision",
        f"  Legacy layer    : {legacy_confirmation}  "
        f"(BUY {buy_votes} / SELL {sell_votes})",
        f"  Data            : {report.get('data_quality', 'REAL_OHLCV')}",
        f"  Evidence        : {', '.join(evidence) if evidence else 'None yet'}",
    ]


def _entry_decision_lines(a: MarketAnalysis) -> list[str]:
    """Show one entry decision and the complete criteria behind it."""
    report = getattr(a, "institutional_report", {}) or {}
    direction = getattr(a, "directional_indication", "NEUTRAL")
    action = getattr(a, "action", "WAIT")
    score = int(getattr(a, "confidence_score", 0) or 0)
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    if adx >= 18:
        evidence.append(f"ADX {adx:.1f}")
    if getattr(a, "choch", "NONE") != "NONE":
        evidence.append(str(a.choch).replace("_", " "))
    if getattr(a, "bos", "NONE") in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(str(a.bos).replace("_", " "))

    data_quality = report.get("data_quality", "REAL_OHLCV")
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and data_quality == "REAL_OHLCV"
    )
    entry_ready = (
        action in ("BUY", "SELL")
        and data_is_real
    )
    legacy = report.get("legacy", {}) or {}
    legacy_confirmation = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    zone = getattr(a, "best_entry_zone", {}) or report.get("best_entry_zone", {}) or {}

    missing = []
    if score < 60:
        missing.append(f"score 60 (now {score})")
    if legacy_confirmation == "CONFLICT":
        missing.append("legacy conflict clearance")

    return [
        "",
        "──────────────────────────────────",
        "  ENTRY DECISION",
        "──────────────────────────────────",
        f"  Status                : "
        f"{action if entry_ready else 'WAITING — MONITORING'}",
        (
            "  Criteria              : score 60+ | selected mode timeframe"
        ),
        "                          local evidence | no conflicting evidence",
        *(
            [
                f"  Entry                 : {fmt_price(getattr(a, 'entry', 0.0))}",
                f"  Stop Loss             : {fmt_price(getattr(a, 'stop_loss', 0.0))}",
                f"  TP1 / TP2 / TP3       : {fmt_price(getattr(a, 'tp1', 0.0))} / "
                f"{fmt_price(getattr(a, 'tp2', 0.0))} / "
                f"{fmt_price(getattr(a, 'tp3', 0.0))}",
                f"  R:R                   : 1:{getattr(a, 'rr_ratio', 0)}",
                "  Trade state           : ACTIVE PLAN",
            ]
            if entry_ready
            else [
                "  Trade plan            : NOT VALID YET",
                f"  Still missing         : "
                f"{'; '.join(missing[:6]) if missing else 'entry criteria'}",
            ]
        ),
        "",
        "  ANALYSIS CURRENTLY MONITORING",
        f"  Direction             : {direction if direction in ('BUY', 'SELL') else 'WAIT'}",
        f"  Entry zone            : "
        f"{fmt_price(zone.get('low', 0))} – {fmt_price(zone.get('high', 0))}"
        if zone.get("low") and zone.get("high")
        else "  Entry zone            : Not formed",
        f"  Evidence              : {', '.join(evidence) if evidence else 'None yet'}",
        f"  Other timeframes     : Not used | Legacy: {legacy_confirmation}",
        f"  Conditions            : {', '.join(missing[:5]) if missing else 'All required conditions met'}",
    ]


def _analysis_detail_lines(a: MarketAnalysis) -> list[str]:
    """Render the complete evidence and entry criteria in reading order."""
    report = getattr(a, "institutional_report", {}) or {}
    direction = getattr(a, "directional_indication", "NEUTRAL")
    action = getattr(a, "action", "WAIT")
    score = int(getattr(a, "confidence_score", 0) or 0)
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    choch = getattr(a, "choch", "NONE")
    bos = getattr(a, "bos", "NONE")
    framework = report.get("score_breakdown", {}) or {}
    structure = report.get("market_structure", {}) or {}
    smc = report.get("smc", {}) or {}
    ob = smc.get("order_block", {}) or {}
    fvg = smc.get("fvg", {}) or {}
    if not isinstance(ob, dict):
        ob = {}
    if not isinstance(fvg, dict):
        fvg = {}
    multi = report.get("multi_timeframe", {}) or {}
    mtf_directions = multi.get("directions", {}) or {}
    mtf_scores = multi.get("scores", {}) or {}
    legacy = report.get("legacy", {}) or {}
    legacy_confirmation = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    htf_matches = (
        direction == "BUY" and htf_bias in ("Bullish", "Slightly Bullish")
    ) or (
        direction == "SELL" and htf_bias in ("Bearish", "Slightly Bearish")
    )
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    if adx >= 18:
        evidence.append(f"ADX {adx:.1f}")
    if choch != "NONE":
        evidence.append(choch.replace("_", " "))
    if bos in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(bos.replace("_", " "))

    setup_ready = (
        data_is_real
        and direction in ("BUY", "SELL")
        and score >= 55
        and bool(evidence)
    )
    entry_ready = action in ("BUY", "SELL")
    lines = [
        "",
        "──────────────────────────────────",
        "  1) DECISION SUMMARY",
        "──────────────────────────────────",
        f"  ENTRY DECISION        : "
        f"{action if entry_ready else 'WAITING'}",
        f"  SETUP STATE           : "
        f"{'READY ' + direction if setup_ready else 'FORMING'}",
        f"  Score                 : {score}/100 (minimum 60 for entry)",
    ]

    strict_mtf_rows = []
    if mtf_directions:
        for tf in ("D1", "H4", "H1", "M15"):
            tf_direction = mtf_directions.get(tf, "NO DATA")
            tf_score = int(mtf_scores.get(tf, 0) or 0)
            passed = (
                direction in ("BUY", "SELL")
                and tf_direction == direction
                and tf_score >= 60
            )
            strict_mtf_rows.append(
                f"{tf} {tf_direction}/{tf_score}{'✓' if passed else '…'}"
            )
    lines += [
        (
            f"  MTF chain             : {' | '.join(strict_mtf_rows)}"
            if strict_mtf_rows
            else f"  Mode scope            : {a.timeframe} only"
        ),
        f"  HTF context           : {htf_bias} "
        f"{'✓ MATCH' if htf_matches else '… NOT USED' if htf_bias == 'Not used' else '… WAITING'}",
        f"  Legacy layer          : {legacy_confirmation} "
        f"(BUY {buy_votes} / SELL {sell_votes})",
        f"  Data quality          : {report.get('data_quality', 'REAL_OHLCV')}",
        "",
        "──────────────────────────────────",
        "  2) ENTRY CRITERIA",
        "──────────────────────────────────",
        f"  Status                : "
        f"{action if entry_ready else 'WAITING — MONITORING'}",
        (
            "  Criteria              : score 60+ | selected mode timeframe"
            if not mtf_directions
            else "  Criteria              : score 60+ | all four timeframes 60+"
        ),
        (
            "                          local evidence | no conflicting evidence"
            if not mtf_directions
            else "                          HTF aligned | no conflicting evidence"
        ),
    ]
    if entry_ready:
        lines += [
            f"  Entry                 : {fmt_price(getattr(a, 'entry', 0.0))}",
            f"  Stop Loss             : {fmt_price(getattr(a, 'stop_loss', 0.0))}",
            f"  TP1 / TP2 / TP3       : {fmt_price(getattr(a, 'tp1', 0.0))} / "
            f"{fmt_price(getattr(a, 'tp2', 0.0))} / {fmt_price(getattr(a, 'tp3', 0.0))}",
            f"  R:R                   : 1:{getattr(a, 'rr_ratio', 0)}",
            "  Trade state           : ACTIVE PLAN — all criteria passed",
        ]
    else:
        lines += [
            "  Trade plan            : NOT VALID YET",
            "  Meaning               : required market conditions are still being monitored",
        ]

    lines += [
        "",
        "──────────────────────────────────",
        "  3) CURRENT SETUP",
        "──────────────────────────────────",
        f"  Status                : {'READY' if setup_ready else 'FORMING'}",
        f"  Direction             : {direction if direction in ('BUY', 'SELL') else 'WAIT'}",
        f"  Evidence              : {', '.join(evidence) if evidence else 'None yet'}",
        "  Meaning               : the same criteria drive the entry decision",
    ]

    layer_rows = [
        ("Trend alignment", "trend_alignment", "/25"),
        ("Market structure", "market_structure", "/25"),
        ("Liquidity sweep", "liquidity_confirmation", "/20"),
        ("Order block", "order_block_reaction", "/15"),
        ("Fair value gap", "fair_value_gap_confirmation", "/10"),
        ("Candle confirmation", "candlestick_confirmation", "/5"),
    ]
    layer_details = {
        "Trend alignment": structure.get("trend", "NONE"),
        "Market structure": (
            f"BOS {structure.get('bos', 'NONE')} / CHoCH {structure.get('choch', 'NONE')}"
        ),
        "Liquidity sweep": smc.get("liquidity_sweep", "NONE"),
        "Order block": (
            f"{ob.get('direction', 'NONE')} {ob.get('freshness', 'NONE')} "
            f"{ob.get('reaction', 'NONE')}"
        ),
        "Fair value gap": (
            f"{fvg.get('direction', smc.get('fair_value_gap', 'NONE'))} "
            f"{fvg.get('reaction', 'NONE')}"
        ),
        "Candle confirmation": ", ".join(report.get("candlesticks", []) or []) or "None",
    }
    entry_zone_price = (
        float(getattr(a, "entry_zone_price", 0.0) or 0.0)
        or float(getattr(a, "limit_entry", 0.0) or 0.0)
    )
    entry_reason = getattr(a, "entry_reason", "") or ""
    zone = getattr(a, "best_entry_zone", {}) or report.get("best_entry_zone", {}) or {}
    lines += [
        "",
        "  ENTRY LOCATION",
        f"  Reference price       : "
        f"{fmt_price(entry_zone_price) if entry_zone_price > 0 else 'Not formed'}",
        f"  Entry zone            : "
        f"{fmt_price(zone.get('low', 0))} – {fmt_price(zone.get('high', 0))}"
        if zone.get("low") and zone.get("high")
        else "  Entry zone            : Not formed",
    ]
    if entry_reason:
        lines.append(f"  Entry method          : {entry_reason[:100]}")

    lines += [
        "",
        "──────────────────────────────────",
        "  4) INSTITUTIONAL EVIDENCE",
        "──────────────────────────────────",
        "  Entry score criterion: 60/100",
    ]
    for label, key, maximum in layer_rows:
        points = int(framework.get(key, 0) or 0)
        marker = "✓" if points > 0 else "…"
        lines.append(
            f"  {marker} {label:<18} {points:>2}{maximum} "
            f"{layer_details[label]}"
        )

    lines += [
        "",
        "──────────────────────────────────",
        "  5) MARKET CONTEXT & SAFETY",
        "──────────────────────────────────",
        f"  Structure             : {structure.get('trend', 'NONE')} | "
        f"BOS {structure.get('bos', 'NONE')} | CHoCH {structure.get('choch', 'NONE')}",
        f"  ADX / strength       : {adx:.1f} / {getattr(a, 'strength', 'N/A')}",
        f"  Macro calendar        : {getattr(a, 'macro_status', 'UNAVAILABLE')}",
        f"  Intermarket           : {getattr(a, 'intermarket_status', 'UNAVAILABLE')}",
        f"  Risk level            : {getattr(a, 'risk_level', 'HIGH')}",
    ]

    missing = []
    if direction not in ("BUY", "SELL"):
        missing.append("clear BUY/SELL direction")
    if score < 60:
        missing.append(f"score 60 (now {score})")
    if not evidence:
        missing.append("directional evidence")
    if score < 60:
        missing.append("score 60")
    for tf in ("D1", "H4", "H1", "M15"):
        tf_direction = mtf_directions.get(tf, "NO DATA")
        tf_score = int(mtf_scores.get(tf, 0) or 0)
        if direction not in ("BUY", "SELL") or tf_direction != direction or tf_score < 60:
            missing.append(f"{tf} alignment")
    if not htf_matches:
        missing.append(f"HTF bias {direction}")
    if legacy_confirmation == "CONFLICT":
        missing.append("legacy conflict clearance")
    if getattr(a, "macro_status", "") == "HIGH_IMPACT_IMMINENT":
        missing.append("high-impact event window")

    lines += [
        "",
        "──────────────────────────────────",
        "  6) BLOCKERS & INVALIDATION",
        "──────────────────────────────────",
        "  Still missing          : " + ("; ".join(missing[:8]) if missing else "None"),
        f"  Entry result           : {action if entry_ready else 'WAITING'}",
        f"  Reason                : "
        f"{(getattr(a, 'wait_reason', '') or getattr(a, 'verdict_reason', '') or 'Required conditions are still being monitored')[:120]}",
    ]
    invalidating = getattr(a, "invalidating_conditions", []) or []
    if invalidating:
        lines.append("  Invalidation           : " + "; ".join(str(item) for item in invalidating[:3]))
    return lines


def entry_analysis_card(a: MarketAnalysis) -> str:
    """Standalone board for the complete single-entry analysis state."""
    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║ SINGLE ENTRY ANALYSIS BOARD      ║",
        "╚══════════════════════════════════╝",
        f"  XAU/USD  {a.timeframe}  |  Price {fmt_price(a.price)}",
        f"  Scan mode: {_mode_config_for_analysis(a).label}",
        *_analysis_detail_lines(a),
        "",
        "  Not financial advice.",
        "</pre>",
    ]
    escaped_lines = [
        lines[0],
        *(html.escape(str(line), quote=False) for line in lines[1:-1]),
        lines[-1],
    ]
    return safe_html("\n".join(escaped_lines))


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
            _mode_line(a),
            "",
            f"  Structure : {_struct_label(a.market_structure)}",
        f"  CHoCH     : {_choch_label(a.choch)}",
            f"  Daily Bias: {getattr(a, 'daily_bias', '') or 'N/A'}",
            f"  HTF Align : {a.htf_bias}",
            f"  Session   : {a.session or 'N/A'}",
        ]
        risk_line = _mode_risk_line(a)
        if risk_line:
            lines.append(risk_line)
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
            lines.append(f"  Limit     : {fmt_price(a.limit_entry)}  (preferred execution)")
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
        wait_status, wait_detail = _wait_status(a)
        lines += [
            "╔══════════════════════════════════╗",
            "║  XAU/USD  [ WAIT ]               ║",
            "╚══════════════════════════════════╝",
            "",
            f"  Status    : {wait_status}",
            f"  Blocker   : {wait_detail}",
            f"  Institutional: {getattr(a, 'confidence_score', 0)}/100",
            f"  Legacy conf.: {a.confidence}% (supporting only)",
            "",
            "  No valid entry yet.",
            "  A moving chart is not enough by itself.",
            "",
            f"  ADX       : {a.adx:.1f}",
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
            f"  Williams%R: {getattr(a,'willr_value',-50):.1f}" + (f"  ← {a.willr_caution}" if getattr(a,'willr_caution','') else ""),
            f"  Supertrend: {'▲ bullish' if getattr(a,'supertrend_direction','')=='BUY' else ('▼ bearish' if getattr(a,'supertrend_direction','')=='SELL' else 'neutral')}",
            f"  CCI(20)   : {getattr(a,'cci_value',0.0):.0f}",
            f"  VWAP      : {getattr(a,'vwap',0.0):,.2f}   Price {'>' if a.price > getattr(a,'vwap',a.price) else '<'} VWAP",
            f"  Regime    : {getattr(a,'market_regime','NORMAL')}",
            f"  Votes     : BUY {a.buy_votes}/8  SELL {a.sell_votes}/8",
        ]
        _cp = getattr(a, "chart_pattern", "None")
        _cp_sig = getattr(a, "chart_pattern_signal", "NEUTRAL")
        if _cp and _cp != "None":
            lines[-1:] += [f"  Chrt Pat  : {_cp} → {_cp_sig}"]
        _hd = getattr(a, "hidden_divergence", "NONE")
        if _hd != "NONE":
            lines += [f"  Hidden Div: {_hd.replace('_', ' ').title()}"]

    # ── Alert cooldown status ──────────────────────────────────────────────────
    try:
        from src.alerts import get_signal_lock_info, _load_account_state
        state = _load_account_state(account_id) if account_id is not None else None
        lock_info = get_signal_lock_info(a.timeframe, state=state)
        if lock_info:
            lines += ["──────────────────────────────────", f"  {lock_info}"]
    except Exception:
        pass

    if not ms["is_open"]:
        lines += ["", f"  ! {ms['status_text']} — {ms['note']}"]

    lines += ["", "  Not financial advice.", "</pre>"]
    return safe_html("\n".join(lines))


# ─── ANALYSIS CARD ────────────────────────────────────────────────────────────

def _normal_entry_conditions(a: MarketAnalysis) -> tuple[str, str, str]:
    """Summarize the one entry decision without introducing entry stages."""
    report = getattr(a, "institutional_report", {}) or {}
    data_quality = report.get("data_quality", "UNKNOWN")
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and data_quality == "REAL_OHLCV"
    )
    direction = getattr(a, "directional_indication", "NEUTRAL") or "NEUTRAL"
    score = int(getattr(a, "confidence_score", 0) or 0)
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    satisfied = []
    waiting = []
    if data_is_real:
        satisfied.append("real OHLCV data")
    else:
        waiting.append("real OHLCV data")
    if direction in ("BUY", "SELL"):
        satisfied.append(f"{direction} directional bias")
    else:
        waiting.append("clear BUY/SELL direction")
    if score >= 60:
        satisfied.append(f"institutional score {score}/100")
    else:
        waiting.append(f"institutional score 60+ (now {score})")
    if matching_votes >= 2:
        satisfied.append(f"{direction} votes {matching_votes}")
    else:
        waiting.append("direction-aligned indicator votes")
    if getattr(a, "adx", 0.0) >= 18:
        satisfied.append(f"ADX {getattr(a, 'adx', 0.0):.1f}")
    else:
        waiting.append("directional momentum")
    satisfied.append(f"{getattr(a, 'timeframe', 'selected')} mode timeframe only")
    legacy = report.get("legacy", {}) or {}
    legacy_state = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    if legacy_state == "CONFLICT":
        waiting.append("legacy indicators stop conflicting")
    else:
        satisfied.append(f"legacy evidence {legacy_state.lower()}")
    entry_state = (
        getattr(a, "action", "WAIT")
        if getattr(a, "action", "WAIT") in ("BUY", "SELL")
        else "WAITING"
    )
    reason = (
        getattr(a, "wait_reason", "")
        or getattr(a, "verdict_reason", "")
        or "All required conditions are satisfied"
    )
    return (
        entry_state,
        ", ".join(satisfied) if satisfied else "None yet",
        "; ".join(waiting) if waiting else "None — valid setup can issue a direct entry",
    )


def _simple_analysis_card(a: MarketAnalysis, alert_label: str = "") -> str:
    """Render a compact, single-scope Telegram analysis card."""
    action = str(getattr(a, "action", "WAIT") or "WAIT").upper()
    early_direction = str(getattr(a, "early_direction", "") or "").upper()
    indication = str(getattr(a, "directional_indication", "") or "").upper()
    signal_direction = (
        action if action in ("BUY", "SELL")
        else early_direction if early_direction in ("BUY", "SELL")
        else indication if indication in ("BUY", "SELL")
        else "WAIT"
    )
    condition = str(getattr(a, "market_condition", "") or "RANGING").upper()
    trend_bias = (
        "BUY" if condition == "BULLISH"
        else "SELL" if condition == "BEARISH"
        else "RANGE"
    )
    direction = signal_direction if signal_direction != "WAIT" else trend_bias
    status = str(getattr(a, "signal_status", "") or "").upper()
    if not status or (status == "NO TRADE" and action in ("BUY", "SELL")):
        status = "CONFIRMED ENTRY" if action in ("BUY", "SELL") else "NO TRADE"

    timeframe = str(getattr(a, "timeframe", "N/A") or "N/A")
    mode = str(getattr(a, "analysis_mode", "") or "").lower()
    mode_label = {
        "scalp": "SCALP",
        "intraday": "INTRADAY",
        "swing": "SWING",
        "position": "POSITION",
    }.get(mode, str(getattr(a, "trade_type", "") or "").upper())
    scope = f"{mode_label} | {timeframe}" if mode_label else timeframe
    data_quality = (
        "SIMULATED"
        if getattr(a, "is_simulated", False)
        else (getattr(a, "institutional_report", {}) or {}).get(
            "data_quality", "REAL_OHLCV"
        )
    )
    price = float(getattr(a, "price", 0.0) or 0.0)
    ema20 = float(getattr(a, "ema20", 0.0) or 0.0)
    ema50 = float(getattr(a, "ema50", 0.0) or 0.0)
    rsi = float(getattr(a, "rsi_value", 0.0) or 0.0)
    atr = float(getattr(a, "atr", 0.0) or 0.0)
    entry = float(getattr(a, "entry", 0.0) or 0.0)
    early_entry = float(getattr(a, "early_entry", 0.0) or 0.0)
    stop = float(getattr(a, "stop_loss", 0.0) or 0.0)
    target = float(getattr(a, "tp1", 0.0) or 0.0)
    invalidation = float(getattr(a, "invalidation", 0.0) or 0.0)
    rr = float(getattr(a, "rr_ratio", 0.0) or 0.0)
    zone_low = float(getattr(a, "entry_zone_low", 0.0) or 0.0)
    zone_high = float(getattr(a, "entry_zone_high", 0.0) or 0.0)
    zone = (
        f"{fmt_price(zone_low)} – {fmt_price(zone_high)}"
        if zone_low and zone_high else "—"
    )
    rsi_support = (
        "supports " + trend_bias
        if (trend_bias == "BUY" and rsi >= 50)
        or (trend_bias == "SELL" and rsi <= 50)
        else "does not support bias"
    )
    setup = str(getattr(a, "price_action_setup", "") or "None detected")
    wait_reason = str(
        getattr(a, "wait_reason", "")
        or getattr(a, "verdict_reason", "")
        or "No additional conditions are currently blocking the plan."
    )
    stream_heading = {
        "SCALP": "⚡ SCALP ENTRY",
        "INTRA-HOUR": "📊 INTRA-HOUR ENTRY",
    }.get(alert_label, "")
    lines = [
        "<pre>",
        *([stream_heading] if stream_heading else []),
        f"XAU/USD  |  {scope}",
        f"{fmt_price(price)}  |  {data_quality}",
        "──────────────────────────────────",
        "WHAT TO DO",
        f"Direction : {direction}  |  Action: {action}",
        "Conflict  : None — all clear",
        f"Bias      : {getattr(a, 'bias', 'Ranging')}  |  Condition: {condition}",
        f"Status    : {status}",
        "──────────────────────────────────",
        "HOW IT IS ANALYZING",
        f"Trend     : EMA20 {fmt_price(ema20)} / EMA50 {fmt_price(ema50)} → {trend_bias}",
        f"Momentum  : RSI14 {rsi:.1f} → {rsi_support}",
        f"Opportunity: {setup}",
        f"Risk      : ATR14 {fmt_price(atr)} → stop/targets from volatility",
        "──────────────────────────────────",
        "TRADE PLAN",
        f"Entry Zone: {zone}",
        f"Early Entry: {fmt_price(early_entry) if early_entry else '—'}",
        f"Entry     : {fmt_price(entry) if entry else '—'}",
        f"SL        : {fmt_price(stop) if stop else '—'}",
        f"TP1       : {fmt_price(target) if target else '—'}",
        f"R:R       : {f'1:{rr:g}' if rr else '—'}",
        f"Invalid   : {fmt_price(invalidation) if invalidation else '—'}",
        "──────────────────────────────────",
        "DECISION",
        f"Alert     : {'✅ alert will fire' if action in ('BUY', 'SELL') else '⏳ no active alert'}",
        f"Waiting   : {wait_reason[:180]}",
        (
            "Note      : provisional review only — not guaranteed."
            if status == "EARLY ENTRY"
            else "Note      : no trade until a valid opportunity develops."
            if status == "NO TRADE"
            else "Note      : active entry plan."
        ),
        "──────────────────────────────────",
        "Not financial advice.",
        "</pre>",
    ]
    return safe_html("\n".join(lines))


def _reference_style_analysis_card(
    a: MarketAnalysis, account_id: int | None = None
) -> str:
    return _simple_analysis_card(a)

    # Kept below as a reference for older layout experiments.
    """Render the compact, reference-style Telegram analysis card.

    The card is deliberately outcome-first: it shows the decision, plan,
    indicators, and market context, but not the engine's internal workflow,
    mode scope, or confirmation process.
    """
    del account_id
    report = getattr(a, "institutional_report", {}) or {}
    legacy = report.get("legacy", {}) or {}
    legacy_state = str(
        legacy.get("confirmation", getattr(a, "legacy_confirmation", "NEUTRAL"))
        or "NEUTRAL"
    ).upper()
    conflict = (
        f"{legacy.get('direction', 'Indicators')} — conflict detected"
        if legacy_state == "CONFLICT"
        else "None — all clear"
    )

    action = str(getattr(a, "action", "WAIT") or "WAIT").upper()
    indication = str(
        getattr(a, "directional_indication", "NEUTRAL") or "NEUTRAL"
    ).upper()
    direction = action if action in ("BUY", "SELL") else (
        indication if indication in ("BUY", "SELL") else "WAIT"
    )
    what_to_do = action if action in ("BUY", "SELL") else "WAIT"
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and report.get("data_quality", "REAL_OHLCV") == "REAL_OHLCV"
    )
    alert = (
        "✅ Signal active"
        if action in ("BUY", "SELL") and data_is_real
        else "⚠ Live data unavailable"
        if not data_is_real
        else "⏳ No active signal"
    )
    score = int(getattr(a, "confidence_score", 0) or 0)
    win_probability = int(getattr(a, "win_probability", 0) or 0)
    if action not in ("BUY", "SELL"):
        win_probability = 0
    rr_ratio = float(getattr(a, "rr_ratio", 0.0) or 0.0)

    def _value(value, fallback="—"):
        return fallback if value is None or value == "" else str(value)

    def _price(value):
        value = float(value or 0.0)
        return fmt_price(value) if value > 0 else "—"

    structure = _struct_label(getattr(a, "market_structure", "") or "—")
    trend = _value(getattr(a, "trend", "—"))
    bias = _value(getattr(a, "bias", "—"))
    strength = _value(getattr(a, "strength", "—"))
    regime = _value(getattr(a, "market_regime", "—"))
    session = _value(getattr(a, "session", "—"))
    candle = _value(getattr(a, "candle_pattern", "None"))
    chart_pattern = _value(getattr(a, "chart_pattern", "None"))
    hidden_divergence = _value(getattr(a, "hidden_divergence", "NONE"))

    grade = _value(
        getattr(a, "setup_quality", "") or getattr(a, "setup_grade", "—")
    )
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    rsi = float(getattr(a, "rsi_value", 0.0) or 0.0)
    stoch_k = float(getattr(a, "stoch_k_val", 0.0) or 0.0)
    stoch_d = float(getattr(a, "stoch_d_val", 0.0) or 0.0)
    macd_hist = float(getattr(a, "macd_hist", 0.0) or 0.0)
    cci = float(getattr(a, "cci_value", 0.0) or 0.0)
    bb_pct = float(getattr(a, "bb_pct", 0.0) or 0.0)
    bb_bandwidth = float(getattr(a, "bb_bandwidth", 0.0) or 0.0)
    plus_di = float(getattr(a, "plus_di", 0.0) or 0.0)
    minus_di = float(getattr(a, "minus_di", 0.0) or 0.0)
    willr = float(getattr(a, "willr_value", -50.0) or -50.0)
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    supertrend = _value(
        getattr(a, "supertrend_direction", "NEUTRAL")
    ).replace("_", " ")

    sep = "─" * 34
    wide = "═" * 34
    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║       XAU/USD  ANALYSIS CARD     ║",
        "╚══════════════════════════════════╝",
        f"  Price     : {_price(getattr(a, 'price', 0))}",
        f"  Market    : {_mkt_line()}",
        "",
        sep,
        "  WHAT TO DO",
        sep,
        f"  Direction : {direction}",
        f"  Action    : {what_to_do}",
        f"  Conflict  : {conflict}",
        f"  Bias      : {bias} ({strength})",
        f"  Trend     : {trend}  |  ADX {adx:.0f}",
        f"  Confidence: {int(getattr(a, 'confidence', 0) or 0)}%",
        *_entry_confirmation_lines(a),
        "",
        sep,
        "  TRADE PLAN",
        sep,
        f"  Entry : {_price(getattr(a, 'entry', 0))}",
        f"  SL    : {_price(getattr(a, 'stop_loss', 0))}",
        f"  TP1   : {_price(getattr(a, 'tp1', 0))}",
        f"  TP2   : {_price(getattr(a, 'tp2', 0))}",
        f"  TP3   : {_price(getattr(a, 'tp3', 0))}",
        f"  R:R   : {f'1:{rr_ratio:g}' if rr_ratio > 0 else '—'}",
        f"  Win % : {_win_bar(win_probability) if win_probability else '—'}",
        f"  Grade : {grade}",
        f"  Alert : {alert}",
        "",
        sep,
        "  INDICATORS",
        sep,
        f"  RSI       : {rsi:.0f}",
        f"  Stoch     : {stoch_k:.0f} | {stoch_d:.0f}",
        f"  MACD Hist : {macd_hist:+.2f}",
        f"  CCI       : {cci:.0f}",
        f"  VWAP      : {_price(getattr(a, 'vwap', 0))}",
        f"  BB %B     : {bb_pct:.0f}% | BW {bb_bandwidth:.2f}%",
        f"  +DI/-DI   : {plus_di:.0f} / {minus_di:.0f}",
        f"  WilliamsR : {willr:.0f}",
        f"  Supertrend: {supertrend}",
        f"  Structure : {structure}",
        f"  Votes     : BUY {buy_votes} | SELL {sell_votes}",
        "",
        sep,
        "  MARKET CONTEXT",
        sep,
        f"  Session   : {session}",
        f"  Regime    : {regime}",
        f"  Momentum  : {_value(getattr(a, 'momentum', '—'))}",
        f"  Kill zone : {_value(getattr(a, 'kill_zone', '—'))}",
        f"  Price zone: {_value(getattr(a, 'premium_discount', '—'))}",
        f"  ATR       : {_price(getattr(a, 'atr', 0))}",
        f"  Score     : {score}/100",
        f"  Liquidity : {_value(getattr(a, 'liquidity_zone', '—'))}",
        "",
        sep,
        "  KEY LEVELS",
        sep,
        f"  BB Upper  : {_price(getattr(a, 'bb_upper', 0))}",
        f"  BB Lower  : {_price(getattr(a, 'bb_lower', 0))}",
        f"  R1 / R2   : {_price(getattr(a, 'resistance1', 0))}"
        f" / {_price(getattr(a, 'resistance2', 0))}",
        f"  S1 / S2   : {_price(getattr(a, 'support1', 0))}"
        f" / {_price(getattr(a, 'support2', 0))}",
    ]
    limit_entry = float(getattr(a, "limit_entry", 0.0) or 0.0)
    if limit_entry > 0 and abs(limit_entry - float(getattr(a, "entry", 0.0) or 0.0)) > 0.01:
        lines.insert(
            lines.index(f"  R:R   : {f'1:{rr_ratio:g}' if rr_ratio > 0 else '—'}"),
            f"  Limit : {_price(limit_entry)}",
        )
    if getattr(a, "pdh", 0.0) and getattr(a, "pdl", 0.0):
        lines.append(
            f"  PDH / PDL : {_price(getattr(a, 'pdh', 0))}"
            f" / {_price(getattr(a, 'pdl', 0))}"
        )
    if candle not in ("None", "—"):
        lines.append(f"  Candle    : {candle}")
    if chart_pattern not in ("None", "—"):
        lines.append(
            f"  Pattern   : {chart_pattern} → "
            f"{_value(getattr(a, 'chart_pattern_signal', 'NEUTRAL'))}"
        )
    if hidden_divergence not in ("NONE", "—"):
        lines.append(f"  Divergence: {hidden_divergence.replace('_', ' ').title()}")

    ms = market_status()
    if not ms["is_open"]:
        lines += ["", f"  ! {ms['status_text']} — {ms['note']}"]
    lines += ["", wide, "  Not financial advice.", "</pre>"]
    return safe_html(
        "\n".join(
            [
                lines[0],
                *(html.escape(str(line), quote=False) for line in lines[1:-1]),
                lines[-1],
            ]
        )
    )


def _legacy_analysis_card(a: MarketAnalysis, account_id: int | None = None) -> str:
    return _reference_style_analysis_card(a, account_id)

    # Kept below as a reference for older layout experiments.
    ms = market_status()
    institutional_report = getattr(a, "institutional_report", {}) or {}
    framework_scores = institutional_report.get("score_breakdown", {}) or {}
    direction = getattr(a, "directional_indication", "NEUTRAL") or "NEUTRAL"
    score = int(getattr(a, "confidence_score", 0) or 0)
    matching_votes = (
        getattr(a, "buy_votes", 0)
        if direction == "BUY"
        else getattr(a, "sell_votes", 0)
    )
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    if float(getattr(a, "adx", 0.0) or 0.0) >= 18:
        evidence.append(f"ADX {float(a.adx):.1f}")
    if getattr(a, "choch", "NONE") != "NONE":
        evidence.append(str(a.choch).replace("_", " "))
    if getattr(a, "bos", "NONE") in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(str(a.bos).replace("_", " "))
    entry_issued = a.action in ("BUY", "SELL")
    setup_grade = (
        getattr(a, "setup_quality", "")
        or getattr(a, "setup_grade", "")
        or "WAIT"
    )
    entry_state, conditions_met, conditions_waiting = _normal_entry_conditions(a)
    if direction in ("BUY", "SELL"):
        setup_status = "ENTRY ISSUED" if entry_issued else "MONITORING"
        setup_analysis = ", ".join(evidence) if evidence else "Directional evidence remains inconclusive."
        setup_decision = (
            "Complete criteria satisfied; direct entry issued."
            if entry_issued
            else "Continue monitoring the missing analysis conditions before issuing an entry."
        )
    else:
        setup_status = "NO DIRECTION"
        setup_analysis = "Liquidity and directional evidence are not aligned."
        setup_decision = "No entry; continue monitoring the analysis inputs."
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
        "  HTF Bias  : Not used (selected timeframe only)",
        f"  Trend     : {a.trend}",
        f"  Momentum  : {a.momentum}",
        f"  ADX       : {a.adx:.1f}",
        "",
        "──────────────────────────────────",
        "  INSTITUTIONAL SCORE",
        "──────────────────────────────────",
        f"  Institutional: {getattr(a, 'confidence_score', 0)}/100",
        f"  Maximum   : 100/100",
        f"  Bias      : {institutional_report.get('direction', 'WAIT')}",
        f"  Legacy    : {institutional_report.get('legacy', {}).get('direction', 'WAIT')} "
        f"(evidence {institutional_report.get('legacy', {}).get('confirmation', 'NEUTRAL')})",
        f"  Legacy Conf.: {institutional_report.get('legacy', {}).get('confidence', getattr(a, 'confidence', 0))}%",
        f"  Combined  : {institutional_report.get('combined', {}).get('direction', 'WAIT')}",
        f"  Mode scope: {a.timeframe} only",
         f"  Data      : {institutional_report.get('data_quality', 'UNKNOWN')}",
        f"  Layers    : T {framework_scores.get('trend_alignment', 0)}/25 | "
        f"S {framework_scores.get('market_structure', 0)}/25 | "
        f"L {framework_scores.get('liquidity_confirmation', 0)}/20",
        f"              OB {framework_scores.get('order_block_reaction', 0)}/15 | "
        f"FVG {framework_scores.get('fair_value_gap_confirmation', 0)}/10 | "
        f"C {framework_scores.get('candlestick_confirmation', 0)}/5",
        "",
        "──────────────────────────────────",
        "  SETUP ANALYSIS",
        "──────────────────────────────────",
        "  One entry system uses the complete analysis below.",
        f"  Status    : {setup_status}",
        f"  Direction : {direction if direction in ('BUY', 'SELL') else 'WAIT'}",
        f"  Analysis  : {setup_analysis}",
        f"  Decision  : {setup_decision}",
        f"  Conditions met: {conditions_met}",
        f"  Monitoring: {conditions_waiting}",
        f"  Entry reason: {(getattr(a, 'wait_reason', '') or getattr(a, 'verdict_reason', '') or 'Complete analysis supports the setup')[:120]}",
         f"  Invalidation: {'; '.join(str(item) for item in (getattr(a, 'invalidating_conditions', []) or [])[:3]) or 'Direction, structure, liquidity, or data quality changes'}",
        *(
            [
                f"  Setup     : {direction}",
                f"  Setup Grade: {setup_grade}",
                f"  Confidence: {getattr(a, 'confidence', 0)}%",
            ]
            if direction in ("BUY", "SELL")
            else []
        ),
        "",
        "──────────────────────────────────",
        "  INDICATORS",
        "──────────────────────────────────",
        _indicator_rows(a),
        f"  MACD Hist : {a.macd_hist:+.3f}",
        f"  +DI / -DI : {a.plus_di:.1f} / {a.minus_di:.1f}",
        f"  Stoch K/D : {a.stoch_k_val:.1f} / {a.stoch_d_val:.1f}",
        f"  BB%B      : {a.bb_pct:.1f}%",
        f"  Williams%R: {getattr(a,'willr_value',-50):.1f}" + (f"  ← {a.willr_caution}" if getattr(a,'willr_caution','') else ""),
        f"  Supertrend: {'▲ bullish' if getattr(a,'supertrend_direction','')=='BUY' else ('▼ bearish' if getattr(a,'supertrend_direction','')=='SELL' else 'neutral')}",
        f"  CCI(20)   : {getattr(a,'cci_value',0.0):.0f}",
        f"  VWAP      : {getattr(a,'vwap',0.0):,.2f}   Price {'>' if a.price > getattr(a,'vwap',a.price) else '<'} VWAP",
        f"  BB BW     : {getattr(a,'bb_bandwidth',0.0):.2f}%   Regime: {getattr(a,'market_regime','NORMAL')}",
        f"  Votes     : BUY {a.buy_votes}/8  SELL {a.sell_votes}/8",
    ]

    if a.candle_pattern and a.candle_pattern != "None":
        lines.append(f"  Candle    : {a.candle_pattern}")
    _cp = getattr(a, "chart_pattern", "None")
    _cp_sig = getattr(a, "chart_pattern_signal", "NEUTRAL")
    if _cp and _cp != "None":
        lines.append(f"  Chrt Pat  : {_cp} → {_cp_sig}")
    _hd = getattr(a, "hidden_divergence", "NONE")
    if _hd != "NONE":
        lines.append(f"  Hidden Div: {_hd.replace('_', ' ').title()}")

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
        "  ENTRY DECISION",
        "──────────────────────────────────",
        "  Entry system: ONE DIRECT ENTRY from the complete analysis",
        f"  Current state: {entry_state}",
    ]

    if a.action in ("BUY", "SELL"):
        t1 = _estimate_time(a, a.tp1)
        t2 = _estimate_time(a, a.tp2)
        setup_grade = getattr(a, "setup_grade", "") or "WAIT"
        lines += [
            f"  SIGNAL    : {a.action}   {_trade_type_label(a)}",
            f"  Setup Grade: {setup_grade}",
            f"  Win Rate  : {_win_bar(a.win_probability)}",
            f"  Confidence: {a.confidence}%",
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
        indication = getattr(a, "directional_indication", "NEUTRAL")
        lines += [
            f"  STATUS    : WAITING — entry criteria not met",
            f"  INDICATION: {indication if indication in ('BUY', 'SELL') else 'WAIT'} (not confirmed)",
        ]
        wait_status, wait_detail = _wait_status(a)
        setup_grade = getattr(a, "setup_grade", "") or "WAIT"
        if indication in ("BUY", "SELL"):
            lines += [
                f"  Direction : {indication}",
                f"  Monitoring: {wait_status}",
                "  Status    : Awaiting confirmation",
                f"  Setup Grade: {setup_grade}",
                f"  Confidence: {a.confidence}%",
                f"  Needs     : {conditions_waiting}",
                f"  Engine note: {(a.wait_reason or a.verdict_reason or wait_detail)[:72]}",
            ]
        else:
            lines.append(f"  Setup Grade: {setup_grade}")
        lines += [
            f"  Reason    : {wait_detail[:72]}",
        ]

    if not ms["is_open"]:
        lines += ["", f"  ! {ms['status_text']} — {ms['note']}"]
    lines += ["", "  Not financial advice.", "</pre>"]
    # Every line between the wrapper tags is analysis-derived text. Escape it
    # as a single boundary so unexpected values from market/news feeds cannot
    # become Telegram HTML tags and make the whole card fail to send.
    escaped_lines = [
        lines[0],
        *(html.escape(str(line), quote=False) for line in lines[1:-1]),
        lines[-1],
    ]
    return safe_html("\n".join(escaped_lines))


def _legacy_compact_analysis_board(
    a: MarketAnalysis, account_id: int | None = None
) -> str:
    """Previous compact board kept as a reference while the layout evolves."""
    ms = market_status()
    report = getattr(a, "institutional_report", {}) or {}
    framework = report.get("score_breakdown", {}) or {}
    multi = report.get("multi_timeframe", {}) or {}
    directions = multi.get("directions", {}) or {}
    scores = multi.get("scores", {}) or {}
    direction = getattr(a, "directional_indication", "NEUTRAL")
    action = getattr(a, "action", "WAIT")
    score = int(getattr(a, "confidence_score", 0) or 0)
    entry_issued = action in ("BUY", "SELL")
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    legacy = report.get("legacy", {}) or {}
    legacy_status = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"
    mtf_text = " | ".join(
        f"{tf}:{directions.get(tf, 'NO DATA')}/{int(scores.get(tf, 0) or 0)}"
        for tf in ("D1", "H4", "H1", "M15")
    )
    mtf_parts = mtf_text.split(" | ")

    matching_votes = buy_votes if direction == "BUY" else sell_votes
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    if float(getattr(a, "adx", 0.0) or 0.0) >= 18:
        evidence.append(f"ADX {float(a.adx):.1f}")
    if getattr(a, "choch", "NONE") != "NONE":
        evidence.append(str(a.choch).replace("_", " "))
    if getattr(a, "bos", "NONE") in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(str(a.bos).replace("_", " "))
    blockers = []
    if score < 60:
        blockers.append(f"score {score}/60")
    for tf in ("D1", "H4", "H1", "M15"):
        if (
            direction not in ("BUY", "SELL")
            or directions.get(tf, "NO DATA") != direction
            or int(scores.get(tf, 0) or 0) < 60
        ):
            blockers.append(f"{tf} alignment")
    if legacy_status == "CONFLICT":
        blockers.append("legacy conflict")
    blocker_text = ", ".join(blockers[:5]) if blockers else "None"

    structure = report.get("market_structure", {}) or {}
    smc = report.get("smc", {}) or {}
    layers_1 = (
        f"T {framework.get('trend_alignment', 0)}/25  "
        f"S {framework.get('market_structure', 0)}/25  "
        f"L {framework.get('liquidity_confirmation', 0)}/20"
    )
    layers_2 = (
        f"OB {framework.get('order_block_reaction', 0)}/15  "
        f"FVG {framework.get('fair_value_gap_confirmation', 0)}/10  "
        f"C {framework.get('candlestick_confirmation', 0)}/5"
    )
    layers = f"{layers_1} | {layers_2}"
    supertrend = getattr(a, "supertrend_direction", "NEUTRAL")
    supertrend_text = (
        "Bullish" if supertrend == "BUY"
        else "Bearish" if supertrend == "SELL"
        else "Neutral"
    )
    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║ XAU/USD  ANALYSIS BOARD          ║",
        "╚══════════════════════════════════╝",
        f"  Price {fmt_price(a.price)} | {a.timeframe} | {a.session or 'N/A'}",
        f"  Market {_mkt_line()}",
        "",
        "── DECISION ───────────────────────",
         f"  Entry  : {action if entry_issued else 'WAITING'}",
         f"  Score  : {score}/100",
        f"  MTF    : {mtf_text}",
        f"  HTF    : {htf_bias} | Legacy {legacy_status}",
        f"  Data   : {report.get('data_quality', 'REAL_OHLCV')}",
        "",
        "── ENTRY INFORMATION ──────────────",
         "  Entry criteria: complete analysis, aligned evidence, usable data, no conflict",
    ]
    if entry_issued:
        lines += [
            f"  Entry       : {action} @ {fmt_price(getattr(a, 'entry', 0.0))}",
            f"  SL / TP1    : {fmt_price(getattr(a, 'stop_loss', 0.0))} / "
            f"{fmt_price(getattr(a, 'tp1', 0.0))}",
            f"  TP2 / TP3   : {fmt_price(getattr(a, 'tp2', 0.0))} / "
            f"{fmt_price(getattr(a, 'tp3', 0.0))} | R:R 1:{getattr(a, 'rr_ratio', 0)}",
        ]
    else:
        lines.append("  Entry       : None — required conditions are still being monitored")
    lines += [
         f"  Conditions met: {', '.join(evidence) if evidence else 'None yet'}",
         f"  Waiting for   : {blocker_text}",
        "",
        "── WHY ────────────────────────────",
        f"  Evidence : {', '.join(evidence) if evidence else 'None yet'}",
        f"  Structure: {structure.get('trend', getattr(a, 'trend', 'N/A'))} | "
        f"BOS {structure.get('bos', getattr(a, 'bos', 'NONE'))} | "
        f"CHoCH {structure.get('choch', getattr(a, 'choch', 'NONE'))}",
        f"  Layers   : {layers}",
        f"  ADX      : {float(getattr(a, 'adx', 0.0) or 0.0):.1f} | "
        f"Votes BUY {buy_votes}/8 SELL {sell_votes}/8",
        f"  Risk     : {getattr(a, 'risk_level', 'HIGH')} | "
        f"Macro {getattr(a, 'macro_status', 'UNAVAILABLE')}",
        f"  Intermkt : {getattr(a, 'intermarket_status', 'UNAVAILABLE')}",
        "",
        "── MARKET SNAPSHOT ────────────────",
        f"  RSI {float(getattr(a, 'rsi_value', 0.0) or 0.0):.1f} | "
        f"MACD {float(getattr(a, 'macd_hist', 0.0) or 0.0):+.2f} | "
        f"Stoch {float(getattr(a, 'stoch_k_val', 0.0) or 0.0):.1f}/"
        f"{float(getattr(a, 'stoch_d_val', 0.0) or 0.0):.1f}",
        f"  CCI {float(getattr(a, 'cci_value', 0.0) or 0.0):.0f} | "
        f"VWAP {float(getattr(a, 'vwap', 0.0) or 0.0):,.2f} | "
        f"BB%B {float(getattr(a, 'bb_pct', 0.0) or 0.0):.1f}",
        f"  Supertrend {supertrend_text} | Regime {getattr(a, 'market_regime', 'NORMAL')}",
        f"  Levels R2 {fmt_price(a.resistance2)} | R1 {fmt_price(a.resistance1)} | "
        f"Price {fmt_price(a.price)}",
        f"  Levels S1 {fmt_price(a.support1)} | S2 {fmt_price(a.support2)} | "
        f"ATR {fmt_price(a.atr)}",
        "",
        "── BLOCKERS ───────────────────────",
        f"  Waiting for: {blocker_text}",
         f"  Reason     : "
        f"{(getattr(a, 'wait_reason', '') or getattr(a, 'verdict_reason', '') or 'No additional blocker')[:110]}",
    ]
    invalidating = getattr(a, "invalidating_conditions", []) or []
    if invalidating:
        lines.append("  Invalidation: " + "; ".join(str(x) for x in invalidating[:2]))
    if not ms["is_open"]:
        lines.append(f"  Market note: {ms['status_text']} — {ms['note']}")
    lines += ["", "  Not financial advice.", "</pre>"]
    escaped_lines = [
        lines[0],
        *(html.escape(str(line), quote=False) for line in lines[1:-1]),
        lines[-1],
    ]
    return safe_html("\n".join(escaped_lines))


def _reference_analysis_card(
    a: MarketAnalysis, account_id: int | None = None, *, split: bool = False
) -> str | list[str]:
    """Render the full analysis board in the reference screenshot's order."""
    del account_id  # The card is informational; active trades use /active.

    ms = market_status()
    report = getattr(a, "institutional_report", {}) or {}
    framework = report.get("score_breakdown", {}) or {}
    multi_timeframe = report.get("multi_timeframe", {}) or {}
    legacy = report.get("legacy", {}) or {}
    combined = report.get("combined", {}) or {}
    data_quality = report.get("data_quality", "REAL_OHLCV")
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and data_quality == "REAL_OHLCV"
    )
    direction = getattr(a, "directional_indication", "NEUTRAL") or "NEUTRAL"
    action = getattr(a, "action", "WAIT") or "WAIT"
    consensus_score = int(multi_timeframe.get("consensus_score", 0) or 0)
    strict_ready = (
        action in ("BUY", "SELL")
        and data_is_real
        and (not mtf_directions or bool(multi_timeframe.get("aligned", False)))
    )

    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    score = int(getattr(a, "confidence_score", 0) or 0)
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    matching_votes = buy_votes if direction == "BUY" else sell_votes
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    if adx >= 18:
        evidence.append(f"ADX {adx:.1f}")
    if getattr(a, "choch", "NONE") != "NONE":
        evidence.append(str(a.choch).replace("_", " "))
    if getattr(a, "bos", "NONE") in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(str(a.bos).replace("_", " "))

    watch_ready = direction in ("BUY", "SELL") and score >= 55 and bool(evidence)
    wait_status, wait_detail = _wait_status(a)
    watch_entry = (
        float(getattr(a, "early_entry", 0.0) or 0.0)
        or float(getattr(a, "limit_entry", 0.0) or 0.0)
    )
    zone = getattr(a, "best_entry_zone", {}) or report.get("best_entry_zone", {}) or {}

    if not data_is_real:
        early_status = "DATA UNAVAILABLE"
        early_analysis = "Real OHLCV data is unavailable; no setup can be validated."
        early_decision = "Do not use this board for an entry until live data returns."
    elif direction not in ("BUY", "SELL"):
        early_status = "NO EARLY DIRECTION"
        early_analysis = "Liquidity confirmation has not been established."
        early_decision = "No early watch is active; await clear directional evidence."
    else:
        early_status = "WATCH READY" if watch_ready else "FORMING"
        early_analysis = ", ".join(evidence) if evidence else "Directional evidence remains inconclusive."
        early_decision = "For manual review only; this does not constitute an active trade."

    layer_1 = (
        f"T {framework.get('trend_alignment', 0)}/25 | "
        f"S {framework.get('market_structure', 0)}/25 | "
        f"L {framework.get('liquidity_confirmation', 0)}/20"
    )
    layer_2 = (
        f"OB {framework.get('order_block_reaction', 0)}/15 | "
        f"FVG {framework.get('fair_value_gap_confirmation', 0)}/10 | "
        f"C {framework.get('candlestick_confirmation', 0)}/5"
    )
    strict_reason = _strict_gate_reason(a, data_is_real)
    engine_reason = (
        getattr(a, "wait_reason", "")
        or getattr(a, "verdict_reason", "")
        or wait_detail
    )

    early_status_display = (
        "Awaiting confirmation"
        if direction in ("BUY", "SELL") and not strict_ready
        else early_status
    )
    setup_grade = (
        getattr(a, "setup_quality", "")
        or getattr(a, "setup_grade", "")
        or "WAIT"
    )
    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  TRANSPARENT ANALYSIS  ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Current price : {fmt_price(a.price)}",
        f"  Timeframe    : {a.timeframe}   {_mkt_line()}",
        f"  Session      : {a.session or 'Not used'}",
        "",
        "──────────────────────────────────",
        "  MARKET STRUCTURE",
        "──────────────────────────────────",
        f"  Structure : {_struct_label(a.market_structure)}",
        f"  Trend     : {a.trend}",
        f"  Momentum  : {a.momentum}",
        f"  ADX       : {a.adx:.1f}",
        "",
        "──────────────────────────────────",
        "  INSTITUTIONAL SCORE",
        "──────────────────────────────────",
        f"  Institutional: {score}/100",
        "  Maximum   : 100/100",
        f"  Bias      : {report.get('direction', 'WAIT')}",
        f"  Legacy    : {legacy.get('direction', 'WAIT')} "
        f"({legacy.get('confirmation', 'NEUTRAL')})",
        f"  Legacy Conf.: {legacy.get('confidence', getattr(a, 'confidence', 0))}%",
        f"  Combined  : {combined.get('direction', 'WAIT')}",
        f"  Mode Scope: "
        f"{_mtf_chain_text(multi_timeframe) if mtf_directions else f'{a.timeframe} only'}",
        f"  Layers    : {layer_1} |",
        f"              {layer_2}",
        "",
        "──────────────────────────────────",
        "  EARLY ENTRY WATCH",
        "──────────────────────────────────",
        "  Separate from strict confirmation.",
        f"  Status    : {early_status_display}",
        f"  Direction : {direction if direction in ('BUY', 'SELL') else 'WAIT'}",
        f"  Analysis  : {early_analysis}",
        f"  Decision  : {early_decision}",
        (
            "  Strict    : WAITING for Daily → H4 → H1 → M15 alignment."
            if mtf_directions
            else "  Strict    : WAITING for selected mode timeframe evidence."
        ),
    ]
    if direction in ("BUY", "SELL") and not strict_ready:
        lines += [
            f"  INDICATION: {direction} (not confirmed)",
            "  Status    : Awaiting confirmation",
            f"  Setup Grade: {setup_grade}",
            f"  Confidence: {getattr(a, 'confidence', 0)}%",
        ]
    lines += [
        *_compact_indicator_lines(a),
        "",
        "──────────────────────────────────",
        "  KEY LEVELS",
        "──────────────────────────────────",
        f"  R2        : {fmt_price(a.resistance2)}",
        f"  R1        : {fmt_price(a.resistance1)}",
        f"  -- Price  : {fmt_price(a.price)}",
        f"  S1        : {fmt_price(a.support1)}",
        f"  S2        : {fmt_price(a.support2)}",
        f"  ATR(14)   : {fmt_price(a.atr)}",
        "",
        "──────────────────────────────────",
        "  STRICT CONFIRMED ENTRY",
        "──────────────────────────────────",
        f"  Status              : {'CONFIRMED ' + action if strict_ready else 'WAITING / REJECTED'}",
        f"  Decision reason     : {(engine_reason or strict_reason)[:180]}",
        *_path_market_data_lines(a, report, direction, section="STRICT CONFIRMED ENTRY"),
        *_strict_confirmation_lines(
            a, report, direction, data_is_real=data_is_real, strict_ready=strict_ready
        ),
        *_path_plan_lines(
            a,
            entry=float(getattr(a, "entry", 0.0) or 0.0),
            label="STRICT CONFIRMED ENTRY",
        ),
        "",
        "  Trade state         : ACTIVE PLAN"
        if strict_ready
        else "  Trade state         : NO ACTIVE TRADE",
        "",
        "──────────────────────────────────",
        "  EARLY WATCH DETAILS",
        "──────────────────────────────────",
        *_path_market_data_lines(a, report, direction, section="EARLY WATCH ENTRY"),
        *_early_trigger_lines(
            a, report, direction, data_is_real=data_is_real, watch_ready=watch_ready
        ),
        *_path_plan_lines(a, entry=watch_entry, label="EARLY WATCH ENTRY (PROVISIONAL)"),
        f"  Entry zone          : "
        f"{_detail_price(zone.get('low'))} – {_detail_price(zone.get('high'))}"
        if zone.get("low") and zone.get("high")
        else "  Entry zone          : Not formed",
        f"  Entry method        : {_detail_value(getattr(a, 'early_entry_reason', ''))}",
        "  Trade state         : MANUAL REVIEW ONLY — NEVER ACTIVE",
        *_path_invalidation_lines(
            a, strict_ready=strict_ready, watch_ready=watch_ready
        ),
    ]

    if not ms["is_open"]:
        lines += ["", f"  ! {ms['status_text']} — {ms['note']}"]
    lines += ["", "  Not financial advice.", "</pre>"]
    if split:
        early_index = lines.index("  EARLY WATCH ENTRY")
        first = lines[:early_index] + [
            "",
            "  Continued in the EARLY WATCH ENTRY card.",
            "</pre>",
        ]
        second = [
            "<pre>",
            "╔══════════════════════════════════╗",
            "║   EARLY WATCH ENTRY DETAILS      ║",
            "╚══════════════════════════════════╝",
            "",
        ] + lines[early_index:]
        return [
            safe_html(_escape_analysis_lines(first)),
            safe_html(_escape_analysis_lines(second)),
        ]
    escaped_lines = [
        lines[0],
        *(html.escape(str(line), quote=False) for line in lines[1:-1]),
        lines[-1],
    ]
    return safe_html("\n".join(escaped_lines))


def _escape_analysis_lines(lines: list[str]) -> str:
    """Escape a complete analysis card while preserving its pre wrapper."""
    return "\n".join(
        [
            lines[0],
            *(html.escape(str(line), quote=False) for line in lines[1:-1]),
            lines[-1],
        ]
    )


def transparent_analysis_cards(
    a: MarketAnalysis, account_id: int | None = None
) -> list[str]:
    """Return the full analysis board used by the Telegram /analyze command.

    The board intentionally stays as one screenshot-style card.  Early Entry
    Watch is already a separate section inside that card; splitting strict and
    early paths into different cards changes the user-facing arrangement.
    """
    return [_legacy_analysis_card(a, account_id)]


def _transparent_entry_card(a: MarketAnalysis, path: str) -> str:
    """Render one complete entry path with the values behind its decision."""
    report = getattr(a, "institutional_report", {}) or {}
    multi = report.get("multi_timeframe", {}) or {}
    data_quality = report.get("data_quality", "UNKNOWN")
    data_is_real = (
        not getattr(a, "is_simulated", False)
        and data_quality == "REAL_OHLCV"
    )
    direction = getattr(a, "directional_indication", "NEUTRAL") or "NEUTRAL"
    action = getattr(a, "action", "WAIT") or "WAIT"
    score = int(getattr(a, "confidence_score", 0) or 0)
    directions = multi.get("directions", {}) or {}
    strict_ready = (
        action in ("BUY", "SELL")
        and data_is_real
        and (not directions or bool(multi.get("aligned", False)))
    )
    early_evidence = _early_evidence(a, direction)
    watch_ready = (
        direction in ("BUY", "SELL")
        and score >= 55
        and bool(early_evidence)
        and data_is_real
    )
    zone = getattr(a, "best_entry_zone", {}) or report.get("best_entry_zone", {}) or {}
    zone_low, zone_high = zone.get("low"), zone.get("high")
    zone_text = (
        f"{_detail_price(zone_low)} – {_detail_price(zone_high)}"
        if zone_low and zone_high
        else "Not formed"
    )
    is_strict = path == "strict"
    status = (
        ("CONFIRMED " + action) if strict_ready else "WAITING / REJECTED"
        if is_strict else
        "WATCH READY " + direction if watch_ready else "FORMING / NOT READY"
    )
    reason = (
        getattr(a, "wait_reason", "")
        or getattr(a, "verdict_reason", "")
        or _strict_gate_reason(a, data_is_real)
        if is_strict else
        ", ".join(early_evidence)
        or "No direction-aligned early evidence factor is active"
    )
    entry = (
        float(getattr(a, "entry", 0.0) or 0.0)
        if is_strict
        else float(getattr(a, "early_entry", 0.0) or 0.0)
        or float(getattr(a, "limit_entry", 0.0) or 0.0)
    )
    title = "STRICT CONFIRMED ENTRY" if is_strict else "EARLY WATCH ENTRY"
    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        f"║ {title:<32} ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Status              : {status}",
        f"  Current price       : {_detail_price(a.price)}",
        f"  Timeframe / session : {a.timeframe} / {a.session or 'Not used'}",
        f"  Market              : {_mkt_line()}",
        f"  Data source         : {data_quality} "
        f"({'usable' if data_is_real else 'NOT USABLE FOR ENTRY'})",
        f"  Directional bias    : {direction}",
        f"  Decision reason     : {str(reason)[:240]}",
        "",
        "──────────────────────────────────",
        "  ACTUAL MARKET DATA USED",
        "──────────────────────────────────",
        *_path_market_data_lines(a, report, direction, section=title),
        *_indicator_transparency_lines(a, direction),
    ]
    if is_strict:
        lines += [
            "",
            "──────────────────────────────────",
            "  STRICT CONFIRMATION GATE",
            "──────────────────────────────────",
            "  Additional confirmation required:",
            "  D1 → H4 → H1 → M15 must all show the same",
            "  direction at >=60/100 with real OHLCV data.",
            *_strict_confirmation_lines(
                a,
                report,
                direction,
                data_is_real=data_is_real,
                strict_ready=strict_ready,
            ),
            *_path_plan_lines(
                a,
                entry=float(getattr(a, "entry", 0.0) or 0.0),
                label="STRICT PLAN (active only if confirmed)",
            ),
            f"  Entry zone          : {zone_text}",
            f"  Conditions used     : MTF alignment, institutional score, "
            f"HTF bias, legacy conflict check",
            f"  Conditions not used : No external order-book or dealer-positioning "
            f"feed is available; macro is {_detail_value(getattr(a, 'macro_status', 'Not used'))}",
        ]
    else:
        lines += [
            "",
            "──────────────────────────────────",
            "  EARLY WATCH TRIGGER GATE",
            "──────────────────────────────────",
            "  Early Watch is a provisional review signal.",
            "  It never opens or activates a trade.",
            *_early_trigger_lines(
                a,
                report,
                direction,
                data_is_real=data_is_real,
                watch_ready=watch_ready,
            ),
            *_path_plan_lines(
                a,
                entry=entry,
                label="EARLY PLAN (provisional)",
            ),
            f"  Entry zone          : {zone_text}",
            f"  Entry method        : {_detail_value(getattr(a, 'early_entry_reason', ''))}",
            f"  Conditions used     : real data, directional bias, score >=55, "
            f"one direction-aligned evidence factor",
            f"  Conditions not used : MTF alignment is not an Early Watch gate; "
            f"liquidity sweep is context only",
        ]
    lines += [
        *_path_invalidation_lines(
            a, strict_ready=strict_ready, watch_ready=watch_ready
        ),
        "",
        "  Not financial advice.",
        "</pre>",
    ]
    escaped = [
        lines[0],
        *(html.escape(str(line), quote=False) for line in lines[1:-1]),
        lines[-1],
    ]
    return safe_html("\n".join(escaped))


def analysis_card(a: MarketAnalysis, account_id: int | None = None) -> str:
    """Render one concise, scannable analysis board.

    The board is intentionally ordered by the user's decision flow:
    what to do, what levels matter, why the engine says it, then what blocks
    the trade.  Detailed indicator output stays available without competing
    with the decision at the top of the message.
    """
    return _legacy_analysis_card(a, account_id)

    ms = market_status()
    report = getattr(a, "institutional_report", {}) or {}
    framework = report.get("score_breakdown", {}) or {}
    multi = report.get("multi_timeframe", {}) or {}
    directions = multi.get("directions", {}) or {}
    scores = multi.get("scores", {}) or {}

    direction = getattr(a, "directional_indication", "NEUTRAL") or "NEUTRAL"
    action = getattr(a, "action", "WAIT") or "WAIT"
    score = int(getattr(a, "confidence_score", 0) or 0)
    strict_ready = action in ("BUY", "SELL")
    buy_votes = int(getattr(a, "buy_votes", 0) or 0)
    sell_votes = int(getattr(a, "sell_votes", 0) or 0)
    legacy = report.get("legacy", {}) or {}
    legacy_status = legacy.get(
        "confirmation", getattr(a, "legacy_confirmation", "NEUTRAL")
    )
    htf_bias = getattr(a, "htf_bias", "Neutral") or "Neutral"

    def _short_direction(value: object) -> str:
        text = str(value or "NO DATA").upper()
        return {"BUY": "B", "SELL": "S", "WAIT": "-", "NEUTRAL": "-"}.get(
            text, "?"
        )

    def _mtf_row(timeframes: tuple[str, ...]) -> str:
        return "  " + "  ".join(
            f"{tf} {_short_direction(directions.get(tf))}/"
            f"{int(scores.get(tf, 0) or 0)}"
            for tf in timeframes
        )

    matching_votes = buy_votes if direction == "BUY" else sell_votes
    evidence = []
    if matching_votes >= 2:
        evidence.append(f"{direction} votes {matching_votes}")
    adx = float(getattr(a, "adx", 0.0) or 0.0)
    if adx >= 18:
        evidence.append(f"ADX {adx:.1f}")
    choch = getattr(a, "choch", "NONE") or "NONE"
    bos = getattr(a, "bos", "NONE") or "NONE"
    if choch != "NONE":
        evidence.append(str(choch).replace("_", " "))
    if bos in ("BULLISH_BOS", "BEARISH_BOS"):
        evidence.append(str(bos).replace("_", " "))
    watch_ready = direction in ("BUY", "SELL") and score >= 55 and bool(evidence)

    watch_entry = (
        float(getattr(a, "early_entry", 0.0) or 0.0)
        or float(getattr(a, "limit_entry", 0.0) or 0.0)
    )
    zone = getattr(a, "best_entry_zone", {}) or report.get("best_entry_zone", {}) or {}
    zone_text = (
        f"{fmt_price(zone.get('low', 0))} – {fmt_price(zone.get('high', 0))}"
        if zone.get("low") and zone.get("high")
        else "Not formed"
    )

    blockers = []
    if score < 60:
        blockers.append(f"score {score}/60")
    for tf in ("D1", "H4", "H1", "M15"):
        if (
            direction not in ("BUY", "SELL")
            or directions.get(tf, "NO DATA") != direction
            or int(scores.get(tf, 0) or 0) < 60
        ):
            blockers.append(f"{tf} alignment")
    if legacy_status == "CONFLICT":
        blockers.append("legacy conflict")
    if not blockers and not strict_ready:
        blockers.append("strict gate")

    structure = report.get("market_structure", {}) or {}
    structure_trend = structure.get("trend", getattr(a, "trend", "N/A"))
    framework_line_1 = (
        f"T {framework.get('trend_alignment', 0)}/25  "
        f"S {framework.get('market_structure', 0)}/25  "
        f"L {framework.get('liquidity_confirmation', 0)}/20"
    )
    framework_line_2 = (
        f"OB {framework.get('order_block_reaction', 0)}/15  "
        f"FVG {framework.get('fair_value_gap_confirmation', 0)}/10  "
        f"C {framework.get('candlestick_confirmation', 0)}/5"
    )

    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║ XAU/USD  ANALYSIS BOARD          ║",
        "╚══════════════════════════════════╝",
        f"  {fmt_price(a.price)}  |  {a.timeframe}  |  {a.session or 'N/A'}",
        f"  Market: {_mkt_line()}",
        "",
        "1) DECISION",
        "──────────────────────────────────",
        f"  Strict : {'CONFIRMED ' + action if strict_ready else 'WAITING'}",
        f"  Early  : {'WATCH READY ' + direction if watch_ready else 'FORMING'}",
        f"  Score  : {score}/100  (watch 55 | strict 60)",
        _mtf_row(("D1", "H4")),
        _mtf_row(("H1", "M15")),
        f"  HTF    : {htf_bias}  |  Legacy {legacy_status}",
        f"  Data   : {report.get('data_quality', 'REAL_OHLCV')}",
        "",
        "2) ENTRY / ACTION",
        "──────────────────────────────────",
    ]

    if strict_ready:
        lines += [
            f"  Plan   : CONFIRMED {action}",
            f"  Entry  : {fmt_price(getattr(a, 'entry', 0.0))}",
            f"  SL     : {fmt_price(getattr(a, 'stop_loss', 0.0))}",
            f"  TP1/2  : {fmt_price(getattr(a, 'tp1', 0.0))} / "
            f"{fmt_price(getattr(a, 'tp2', 0.0))}",
            f"  TP3/RR : {fmt_price(getattr(a, 'tp3', 0.0))} / "
            f"1:{getattr(a, 'rr_ratio', 0)}",
            "  State  : active plan — confirmation passed",
        ]
    else:
        wait_status, wait_detail = _wait_status(a)
        indication = direction if direction in ("BUY", "SELL") else "WAIT"
        setup_grade = getattr(a, "setup_quality", "WAIT") or getattr(
            a, "setup_grade", "WAIT"
        )
        lines += [
            f"  INDICATION: {indication} (not confirmed)",
            "  Status    : Awaiting confirmation",
            f"  Setup Grade: {setup_grade}",
            f"  Confidence: {getattr(a, 'confidence', 0)}%",
            f"  Blocked   : {wait_detail}",
            f"  Reason    : {(getattr(a, 'wait_reason', '') or getattr(a, 'verdict_reason', '') or wait_status)[:90]}",
        ]

    lines += [
        f"  Early watch: {direction if direction in ('BUY', 'SELL') else 'WAIT'}"
        f"  | Entry {fmt_price(watch_entry) if watch_entry > 0 else 'N/A'}",
        f"  Watch zone : {zone_text}",
        "  Early plan : provisional review only — never active",
        "",
        "3) WHY",
        "──────────────────────────────────",
        f"  Structure : {structure_trend} | BOS {structure.get('bos', bos)}",
        f"  CHoCH     : {structure.get('choch', choch)}",
        f"  Evidence  : {', '.join(evidence) if evidence else 'None yet'}",
        f"  Framework : {framework_line_1}",
        f"              {framework_line_2}",
        f"  ADX/Votes: {adx:.1f}  |  B {buy_votes}/8  S {sell_votes}/8",
        "",
        "4) MARKET SNAPSHOT",
        "──────────────────────────────────",
        f"  RSI      : {float(getattr(a, 'rsi_value', 0.0) or 0.0):.1f}"
        f"  | MACD {float(getattr(a, 'macd_hist', 0.0) or 0.0):+.2f}",
        f"  Stoch    : {float(getattr(a, 'stoch_k_val', 0.0) or 0.0):.1f}/"
        f"{float(getattr(a, 'stoch_d_val', 0.0) or 0.0):.1f}"
        f"  | CCI {float(getattr(a, 'cci_value', 0.0) or 0.0):.0f}",
        f"  VWAP     : {float(getattr(a, 'vwap', 0.0) or 0.0):,.2f}"
        f"  | BB%B {float(getattr(a, 'bb_pct', 0.0) or 0.0):.1f}",
        f"  Trend    : {'Bullish' if getattr(a, 'supertrend_direction', '') == 'BUY' else 'Bearish' if getattr(a, 'supertrend_direction', '') == 'SELL' else 'Neutral'}"
        f"  | Regime {getattr(a, 'market_regime', 'NORMAL')}",
        f"  Levels   : R2 {fmt_price(a.resistance2)}  R1 {fmt_price(a.resistance1)}",
        f"             S1 {fmt_price(a.support1)}  S2 {fmt_price(a.support2)}",
        f"  ATR      : {fmt_price(a.atr)}",
        "",
        "5) RISK / BLOCKERS",
        "──────────────────────────────────",
        f"  Risk     : {getattr(a, 'risk_level', 'HIGH')}",
        f"  Macro    : {getattr(a, 'macro_status', 'UNAVAILABLE')}",
        f"  Intermkt : {getattr(a, 'intermarket_status', 'UNAVAILABLE')}",
        f"  Waiting  : {', '.join(blockers[:5]) if blockers else 'None'}",
    ]

    invalidating = getattr(a, "invalidating_conditions", []) or []
    if invalidating:
        lines.append("  Invalid  : " + "; ".join(str(x) for x in invalidating[:2]))
    if not ms["is_open"]:
        lines.append(f"  Market   : {ms['status_text']} — {ms['note']}")
    lines += ["", "  Not financial advice.", "</pre>"]

    escaped_lines = [
        lines[0],
        *(html.escape(str(line), quote=False) for line in lines[1:-1]),
        lines[-1],
    ]
    return safe_html("\n".join(escaped_lines))


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
    return _legacy_analysis_card(a)

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
    htf_map = {
        "M1": "H1", "M3": "H1", "M5": "H1", "M15": "H1",
        "M30": "H4", "H1": "H4", "H4": "D1",
        "D1": "W1", "W1": "MN1", "MN1": "MN1",
    }
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
                "EQUILIBRIUM": "◆ EQUILIBRIUM (consolidation)"}
    institutional_report = getattr(a, "institutional_report", {}) or {}
    framework_scores = institutional_report.get("score_breakdown", {}) or {}
    framework_direction = institutional_report.get("direction", "WAIT")
    multi_timeframe = institutional_report.get("multi_timeframe", {}) or {}
    report = institutional_report

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
        "  INSTITUTIONAL FRAMEWORK SCORE",
        "══════════════════════════════════",
        f"  Trend Alignment : {framework_scores.get('trend_alignment', 0)}/25",
        f"  Market Structure: {framework_scores.get('market_structure', 0)}/25",
        f"  Liquidity       : {framework_scores.get('liquidity_confirmation', 0)}/20",
        f"  Order Block     : {framework_scores.get('order_block_reaction', 0)}/15",
        f"  Fair Value Gap  : {framework_scores.get('fair_value_gap_confirmation', 0)}/10",
        f"  Candle Confirm. : {framework_scores.get('candlestick_confirmation', 0)}/5",
        f"  Framework Bias   : {framework_direction}",
        f"  Institutional Score: {getattr(a, 'confidence_score', 0)}/100 | Max 100/100",
        f"  Legacy Confirm.  : {report.get('legacy', {}).get('confirmation', 'NEUTRAL')}",
        f"  Legacy Confidence : {report.get('legacy', {}).get('confidence', getattr(a, 'confidence', 0))}%",
        f"  Combined Result  : {report.get('combined', {}).get('direction', 'WAIT')}",
        f"  MTF Chain        : {_mtf_chain_text(multi_timeframe)}",
    ]

    if a.candle_pattern and a.candle_pattern != "None":
        lines += [
            "──────────────────────────────────",
            f"  Candle  : {a.candle_pattern}",
        ]
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
        *_entry_paths_lines(a),
        "",
        "══════════════════════════════════",
        "  INSTITUTIONAL ENGINE v5",
        "══════════════════════════════════",
        f"  Bull / Bear : {getattr(a, 'bullish_probability', 50)}% / {getattr(a, 'bearish_probability', 50)}%",
        f"  Confidence  : Earned {getattr(a, 'confidence_score', 0)}/100 | Max 100/100",
        f"  Risk        : {getattr(a, 'risk_level', 'HIGH')}",
        f"  Macro       : {getattr(a, 'macro_status', 'UNAVAILABLE')}",
        f"  Intermarket : {getattr(a, 'intermarket_status', 'UNAVAILABLE')}",
    ]
    report = institutional_report
    structure = report.get("market_structure", {})
    smc = report.get("smc", {})
    volatility = report.get("volatility", {})
    if structure:
        lines += [
            f"  Structure   : {structure.get('trend', 'N/A')} | "
            f"BOS {structure.get('bos', 'NONE')} | "
            f"CHoCH {structure.get('choch', 'NONE')}",
            f"  Swings      : {', '.join(structure.get('labels', [])[-4:]) or 'N/A'}",
            f"  Regime      : {volatility.get('regime', 'N/A')} | "
            f"ATR {volatility.get('atr', 0):,.2f}",
            f"  Liquidity   : {smc.get('liquidity_sweep', 'NONE')} | "
            f"FVG {smc.get('fair_value_gap', 'NONE')} | "
            f"OB {smc.get('order_block', 'NONE')}",
        ]
    supporting = getattr(a, "reasons_supporting", []) or []
    against = getattr(a, "reasons_against", []) or []
    if supporting:
        lines.append("  Supports    : " + "; ".join(supporting[:3]))
    if against:
        lines.append("  Against     : " + "; ".join(against[:3]))
    if getattr(a, "invalidating_conditions", None):
        lines.append("  Invalidates : " + "; ".join(a.invalidating_conditions[:2]))

    plan_zone = report.get("best_entry_zone", {}) or {}
    plan_direction = report.get("direction", "WAIT")
    plan_stop = report.get("suggested_stop_loss", 0.0)
    plan_target = report.get("suggested_take_profit", 0.0)
    plan_rr = report.get("recommended_rr", 0.0)
    lines += [
        "",
        "  CONDITIONAL TRADE PLAN",
        f"  Bias       : {plan_direction} (framework only)",
        f"  Entry Zone : {fmt_price(plan_zone.get('low', 0))} – {fmt_price(plan_zone.get('high', 0))}"
        if plan_zone.get("low") and plan_zone.get("high")
        else "  Entry Zone : N/A — no confirmed directional setup",
        f"  Stop Loss  : {fmt_price(plan_stop)}" if plan_stop else "  Stop Loss  : N/A",
        f"  Take Profit: {fmt_price(plan_target)}" if plan_target else "  Take Profit: N/A",
        f"  R:R        : 1:{plan_rr}" if plan_rr else "  R:R        : N/A",
    ]
    if a.action not in ("BUY", "SELL"):
        lines.append("  Status     : NO TRADE — wait for confirmation")

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
    return safe_html("\n".join(lines))


# ─── PART 2: Early entry signal (only for A/A+ grade) ────────────────────────

def entry_card(a: MarketAnalysis, alert_label: str = "") -> str:
    """Single direct-entry alert card using the simple analysis framework."""
    return _simple_analysis_card(a, alert_label=alert_label)

    # Kept below as a reference for older layout experiments.
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

    stream_heading = {
        "SCALP": "⚡ SCALP ENTRY",
        "INTRA-HOUR": "📊 INTRA-HOUR ENTRY",
    }.get(alert_label, f"{alert_label} ALERT" if alert_label else "")
    stream_line = f"{stream_heading}  |  " if stream_heading else ""
    lines = ["<pre>",
        f"{stream_line}XAU/USD  {a.action}  {a.timeframe}  {a.session or 'N/A'}",
        f"Grade {a.setup_quality}  |  Strength {a.win_probability}%  |  {a.trade_type}",
        sep,
        "INSTITUTIONAL CONTEXT",
        f"  Daily : {db or 'N/A'}  |  HTF: {a.htf_bias}",
        f"  Zone  : {pd_arrow}  |  KZ: {'✓ ' + kz if is_kz else 'Outside active hours'}",
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

    if a.entry:
        lines += [
            f"  Entry : {fmt_price(a.entry)}",
        ]

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
    return safe_html("\n".join(lines))


def early_entry_card(a: MarketAnalysis, alert_label: str = "") -> str:
    """Compatibility name for the direct entry alert renderer."""
    return entry_card(a, alert_label=alert_label)


def no_entry_card(a: MarketAnalysis) -> str:
    """Shown when the complete analysis has not produced a valid setup."""
    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  NO ENTRY  Pt.2        ║",
        "╚══════════════════════════════════╝",
        "",
        f"  Direction : {a.action}  (grade {a.setup_quality})",
        f"  Win Rate  : {_win_bar(a.win_probability) if a.win_probability else 'N/A'}",
        "",
        "  The complete entry criteria have not been met.",
        "  No entry has been issued.",
        "",
        "  What needs to improve:",
        "──────────────────────────────────",
    ]

    # Tell the user what is missing
    missing = []
    if a.confidence < 80:
        missing.append(f"Confidence {a.confidence}% &lt; 80% (more evidence required)")
    if len(a.confluence_list) < 4:
        missing.append(f"Confluence {len(a.confluence_list)}/4+ factors required")
    if a.adx < 20:
        missing.append(f"ADX {a.adx:.1f} is too low — the market is ranging")
    if a.htf_bias in ("Neutral",):
        missing.append("HTF bias is neutral — clear macro alignment is required")
    if a.session in ("Asian",):
        missing.append("Asian session — await London/New York volume")
    if not missing:
        missing.append("Signal criteria: R:R or ADX conditions have not been met")

    for m in missing:
        lines.append(f"  - {m}")

    lines += [
        "",
        "  Continue monitoring the conditions listed above.",
        "  Use /alerts to receive notifications",
        "  when a valid setup is detected.",
        "",
        "  Not financial advice.", "</pre>",
    ]
    return "\n".join(lines)


def recommend_card(a: MarketAnalysis) -> str:
    """Single-TF fallback (kept for internal use)."""
    return pro_analysis_card(a)


def recommend_multi_card(analyses: list) -> str:
    """
    All-timeframe recommendation card in the same scan-friendly layout as the
    reference analysis card.

    Keep this as a thin wrapper so refreshes and combined recommendations do
    not drift into a second presentation format.
    """
    return multi_timeframe_card(analyses)


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
        f"  Williams%R: {getattr(a,'willr_value',-50):.1f}" + (f"  ← {a.willr_caution}" if getattr(a,'willr_caution','') else ""),
        f"  Supertrend: {'▲ bullish' if getattr(a,'supertrend_direction','')=='BUY' else ('▼ bearish' if getattr(a,'supertrend_direction','')=='SELL' else 'neutral')}",
        f"  CCI(20)   : {getattr(a,'cci_value',0.0):.0f}",
        f"  VWAP      : {getattr(a,'vwap',0.0):,.2f}   ({'above' if a.price > getattr(a,'vwap',a.price) else 'below'} VWAP)",
        f"  Regime    : {getattr(a,'market_regime','NORMAL')}",
        f"  Votes     : BUY {a.buy_votes}/8  SELL {a.sell_votes}/8",
    ]
    if a.candle_pattern and a.candle_pattern != "None":
        lines.append(f"  Pattern   : {a.candle_pattern}")
    _cp = getattr(a, "chart_pattern", "None")
    _cp_sig = getattr(a, "chart_pattern_signal", "NEUTRAL")
    if _cp and _cp != "None":
        lines.append(f"  Chrt Pat  : {_cp} → {_cp_sig}")
    _hd = getattr(a, "hidden_divergence", "NONE")
    if _hd != "NONE":
        lines.append(f"  Hidden Div: {_hd.replace('_', ' ').title()}")
    if a.breakout:
        lines.append("  Note      : Breakout in progress")
    if a.reversal:
        lines.append("  Note      : Divergence reversal signal")
    lines += ["", "  Not financial advice.", "</pre>"]
    return safe_html("\n".join(lines))


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
    return safe_html("\n".join(lines))


# ─── OUTLOOK CARD ─────────────────────────────────────────────────────────────

def outlook_card(a: MarketAnalysis) -> str:
    ms  = market_status()
    mkt = f"CLOSED — {ms['note']}" if not ms["is_open"] else ms["note"]
    SEP = "──────────────────────────────────"

    # Determine HTF vs LTF context. These are calculated independently from
    # OHLC data; do not substitute the current signal for a trend label.
    htf_bias = a.htf_bias or "Neutral"
    ltf_bias = a.bias or "Neutral"
    h4_bias = getattr(a, "htf_h4_bias", htf_bias) or "Neutral"
    d1_bias = getattr(a, "htf_d1_bias", "Neutral") or "Neutral"
    ltf_trends = getattr(a, "ltf_trends", {}) or {}

    # Market structure label
    struct = _struct_label(a.market_structure)
    choch  = _choch_label(a.choch)

    # Probability distribution
    if a.action == "BUY":
        bull_pct, bear_pct = a.win_probability, 100 - a.win_probability
    elif a.action == "SELL":
        bull_pct, bear_pct = 100 - a.win_probability, a.win_probability
    else:
        bull_pct = bear_pct = 50
    bull_bar = "█" * round(bull_pct / 10) + "░" * (10 - round(bull_pct / 10))
    bear_bar = "█" * round(bear_pct / 10) + "░" * (10 - round(bear_pct / 10))
    conf_bar = _win_bar(a.confidence)

    # Evidence-first pressure narrative. The engine compares recent candle
    # bodies and +DI/-DI; the report must not claim HH/HL or rejection unless
    # those observations were actually detected.
    buy_press = getattr(a, "buying_pressure", "") or "Buying-pressure evidence unavailable."
    sell_press = getattr(a, "selling_pressure", "") or "Selling-pressure evidence unavailable."
    advantage = getattr(a, "pressure_advantage", "Neutral") or "Neutral"
    structure_detail = getattr(a, "structure_detail", "") or "Not detected"
    bos = getattr(a, "bos", "NONE") or "NONE"
    bos_label = {
        "BULLISH_BOS": "Bullish BOS — close above latest confirmed swing high",
        "BEARISH_BOS": "Bearish BOS — close below latest confirmed swing low",
        "NONE": "No confirmed BOS on the latest data",
    }.get(bos, bos)
    choch_label = _choch_label(a.choch)
    sweep = getattr(a, "liquidity_evidence", "") or "Not detected"
    candle_evidence = getattr(a, "candle_evidence", "") or "No candle evidence"
    fvg_dir = getattr(a, "fvg_direction", "NONE") or "NONE"
    if fvg_dir != "NONE":
        fvg_evidence = (
            f"{fvg_dir} FVG {fmt_price(getattr(a, 'fvg_bottom', 0))}–"
            f"{fmt_price(getattr(a, 'fvg_top', 0))}"
        )
    else:
        fvg_evidence = "No unfilled FVG detected at current price"
    ob_dir = getattr(a, "order_block_direction", "NONE") or "NONE"
    if ob_dir != "NONE":
        ob_evidence = (
            f"{ob_dir} order block {fmt_price(getattr(a, 'order_block_low', 0))}–"
            f"{fmt_price(getattr(a, 'order_block_high', 0))}"
        )
    else:
        ob_evidence = "No nearby order block detected"

    if a.action == "BUY":
        bull_scene = (
            f"Buyers would be better supported if price holds {fmt_price(a.support1)} "
            f"and closes above {fmt_price(a.resistance1)} with momentum."
        )
        bear_scene = (
            f"Sellers would gain the advantage if price closes below "
            f"{fmt_price(a.support1)}; until then a dip is not automatically a reversal."
        )
        inval = fmt_price(a.support2)
    elif a.action == "SELL":
        bull_scene = (
            f"Buyers would gain the advantage if price closes above "
            f"{fmt_price(a.resistance1)} with momentum and holds the breakout."
        )
        bear_scene = (
            f"Sellers would be better supported if price holds below "
            f"{fmt_price(a.resistance1)} and closes below {fmt_price(a.support1)}."
        )
        inval = fmt_price(a.resistance2)
    else:
        bull_scene = f"A close above {fmt_price(a.resistance1)} with strong momentum would favour buyers."
        bear_scene = f"A close below {fmt_price(a.support1)} with strong momentum would favour sellers."
        inval = "No directional invalidation; range break is required"

    # Trade quality
    if a.action in ("BUY", "SELL"):
        if a.win_probability >= 68 and a.setup_quality in ("A+", "A"):
            quality, risk = "Excellent", "Low"
        elif a.win_probability >= 62:
            quality, risk = "Good", "Medium"
        elif a.win_probability >= 55:
            quality, risk = "Average", "Medium"
        else:
            quality, risk = "Poor", "High"
    else:
        quality, risk = "N/A", "High"

    lines = [
        "<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  MARKET OUTLOOK        ║",
        "╚══════════════════════════════════╝",
        "",
        f"  {a.timeframe}  |  {a.session or 'N/A'}  |  {mkt}",
        "",
        SEP,
        "  TREND",
        SEP,
        f"  HTF Context   : {htf_bias}",
        f"  LTF ({a.timeframe})  : {ltf_bias}  ({a.strength})",
        "",
        SEP,
        "  MARKET STRUCTURE",
        SEP,
        f"  {struct}",
        f"  CHoCH : {choch}",
        f"  ADX   : {a.adx:.1f}  ({'Trending' if a.adx > 25 else 'Ranging / Weak'})",
        f"  Momentum: {a.momentum}",
    ]

    # S/R and key levels
    lines += [
        "",
        SEP,
        "  KEY LEVELS",
        SEP,
        f"  Resistance 1 : {fmt_price(a.resistance1)}",
        f"  Resistance 2 : {fmt_price(a.resistance2)}",
        f"  Support 1    : {fmt_price(a.support1)}",
        f"  Support 2    : {fmt_price(a.support2)}",
    ]
    if getattr(a, "pdh", 0) > 0:
        lines.append(f"  PDH          : {fmt_price(a.pdh)}")
    if getattr(a, "pdl", 0) > 0:
        lines.append(f"  PDL          : {fmt_price(a.pdl)}")

    # Candlestick
    if a.candle_pattern and a.candle_pattern not in ("None", "Doji", ""):
        lines += ["", SEP, "  CANDLESTICK", SEP,
                  f"  {a.candle_pattern}"]

    # Buying vs selling pressure
    lines += ["", SEP, "  BUYING vs SELLING PRESSURE", SEP]
    for line in _wrap_text(buy_press, 34):
        lines.append(f"  Buy  | {line}")
    lines.append("")
    for line in _wrap_text(sell_press, 34):
        lines.append(f"  Sell | {line}")
    lines.append(f"  Advantage: {advantage}")

    # Probability
    lines += [
        "",
        SEP,
        "  PROBABILITY",
        SEP,
        f"  Bullish [{bull_bar}] {bull_pct}%",
        f"  Bearish [{bear_bar}] {bear_pct}%",
        f"  Confidence  {conf_bar}",
    ]

    # Trade setup when there is a signal
    if a.action in ("BUY", "SELL"):
        sl_dist = abs(a.entry - a.stop_loss) if a.stop_loss else 0
        rr1 = round(abs(a.tp1 - a.entry) / sl_dist, 1) if sl_dist and a.tp1 else 0
        rr2 = round(abs(a.tp2 - a.entry) / sl_dist, 1) if sl_dist and a.tp2 else 0
        lines += [
            "",
            SEP,
            f"  TRADE SETUP  ({a.action})",
            SEP,
            f"  Entry    : {fmt_price(a.entry)}",
            f"  Stop Loss: {fmt_price(a.stop_loss)}",
            f"  TP1      : {fmt_price(a.tp1)}  (1:{rr1})",
            f"  TP2      : {fmt_price(a.tp2)}  (1:{rr2})",
        ]
        if getattr(a, "tp3", 0):
            rr3 = round(abs(a.tp3 - a.entry) / sl_dist, 1) if sl_dist else 0
            lines.append(f"  TP3      : {fmt_price(a.tp3)}  (1:{rr3})")

    # Evidence sections
    lines += [
        "",
        SEP,
        "  PRICE-ACTION EVIDENCE",
        SEP,
        f"  HH/HL/LH/LL : {structure_detail}",
        f"  BOS         : {bos_label}",
        f"  CHoCH       : {choch}",
        f"  Liquidity   : {sweep}",
        f"  FVG         : {fvg_evidence}",
        f"  Order Block : {ob_evidence}",
        f"  Candles     : {candle_evidence}",
    ]

    # Confluence
    if a.confluence_list:
        lines += ["", SEP, f"  CONFLUENCE  ({len(a.confluence_list)} factors)", SEP]
        for cf in a.confluence_list:
            lines.append(f"  + {cf}")

    # Final summary — scenarios + invalidation
    lines += ["", SEP, "  FINAL SUMMARY", SEP]
    lines += ["  Market Bias : " + (a.bias or "Neutral")]
    lines += ["  Confidence  : " + f"{a.confidence}%"]
    confidence_reason = (
        f"Based on {max(a.buy_votes, a.sell_votes)}/5 directional indicator votes, "
        f"{a.htf_bias} higher-timeframe context, ADX {a.adx:.1f}, and the "
        f"structure/candle evidence above."
    )
    lines += ["  Confidence Rationale:"]
    for ln in _wrap_text(confidence_reason, 34):
        lines.append(f"    {ln}")
    lines += ["", "  Reasons:"]
    for cf in (a.confluence_list or [])[:4]:
        lines.append(f"    • {cf}")
    lines += ["", "  Bullish Scenario:"]
    for ln in _wrap_text(bull_scene, 34):
        lines.append(f"    {ln}")
    lines += ["", "  Bearish Scenario:"]
    for ln in _wrap_text(bear_scene, 34):
        lines.append(f"    {ln}")
    lines += [f"", f"  Invalidation Level: {inval}"]

    # Risk assessment
    lines += [
        "",
        SEP,
        "  RISK ASSESSMENT",
        SEP,
        f"  Current Bias  : {a.bias or 'Neutral'}",
        f"  Probability   : Bullish {bull_pct}%  Bearish {bear_pct}%",
        f"  Key Resistance: {fmt_price(a.resistance1)}",
        f"  Key Support   : {fmt_price(a.support1)}",
        f"  Trade Quality : {quality}",
        f"  Risk Level    : {risk}",
        "",
        SEP,
        "  ⚠️  This analysis is based solely on",
        "  current price action and cannot",
        "  guarantee future market movement.",
        "  Always use proper risk management",
        "  and stop losses.",
        "</pre>",
    ]

    # If a trade exists, keep its frozen levels separate from this fresh read.
    # A negative P&L alone does not invalidate the trade; only a stored stop
    # breach or a confirmed structural invalidation does.
    try:
        from src import trade_tracker
        open_trades = trade_tracker.get_active_trades(account_id)
    except Exception:
        open_trades = []
    matching_trade = next((t for t in open_trades if t.get("timeframe") == a.timeframe), None)
    if matching_trade:
        direction = matching_trade.get("direction", "UNKNOWN")
        entry = float(matching_trade.get("entry", 0))
        sl = float(matching_trade.get("sl", 0))
        distance = a.price - entry if direction == "BUY" else entry - a.price
        signed_distance = f"{distance:+,.2f}"
        stop_breached = (
            (direction == "BUY" and a.price <= sl)
            or (direction == "SELL" and a.price >= sl)
        )
        technically_valid = not stop_breached
        trade_validity = "INVALIDATED by stored SL breach" if stop_breached else "STILL VALID — price has not breached stored SL"
        strengthening = (
            f"BUY: hold above support {fmt_price(a.support1)} and confirm a bullish BOS."
            if direction == "BUY"
            else f"SELL: hold below resistance {fmt_price(a.resistance1)} and confirm a bearish BOS."
        )
        invalidating = (
            f"BUY: close below {fmt_price(a.support1)} / stored SL {fmt_price(sl)}."
            if direction == "BUY"
            else f"SELL: close above {fmt_price(a.resistance1)} / stored SL {fmt_price(sl)}."
        )
        lines = lines[:-1]  # remove </pre>, append trade analysis before closing
        lines += [
            "",
            SEP,
            "  OPEN TRADE — SEPARATE READ",
            SEP,
            f"  Direction   : {direction} ({matching_trade.get('timeframe', '?')})",
            f"  Entry       : {fmt_price(entry)}",
            f"  Current     : {fmt_price(a.price)}",
            f"  Distance    : {signed_distance} points",
            f"  Validity    : {trade_validity}",
            f"  Nearby R/S  : R1 {fmt_price(a.resistance1)} | S1 {fmt_price(a.support1)}",
            f"  Strengthens : {strengthening}",
            f"  Invalidates : {invalidating}",
            "  Note        : Loss status alone is not a close instruction.",
            "</pre>",
        ]

    return safe_html("\n".join(lines))


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
        _mode_risk_line(a),
        "",
        "──────────────────────────────────",
        f"  Entry     : {fmt_price(a.entry)}",
    ]
    if a.trade_type != "Scalp" and a.limit_entry and a.limit_entry != a.entry:
        lines.append(f"  Limit     : {fmt_price(a.limit_entry)}  (preferred execution)")
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
            "Continue monitoring for a clear directional setup.",
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
    from html import escape
    now = datetime.now(timezone.utc).strftime("%d %b %Y  %H:%M UTC")
    lines = ["<pre>",
        "GOLD NEWS  |  XAU/USD",
        "=" * 32,
        f"Updated: {now}",
        "=" * 32,
    ]
    if not items:
        lines += ["No headlines are currently available.", "Please try again in a few minutes."]
    else:
        for i, item in enumerate(items, 1):
            date_part = f"  [{item['date']}]" if item.get("date") else ""
            title = escape(item.get("title", ""))
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
            src = escape(item.get("source", ""))
            lines.append(f"   {src}{date_part}")
            if i < len(items):
                lines.append("─" * 32)
    lines += ["=" * 32, "Source: Google News RSS", "Refreshes every 15 minutes.", "</pre>"]
    links = [
        f'<a href="{escape(item["url"], quote=True)}">Open article {i}</a>'
        for i, item in enumerate(items, 1)
        if item.get("url")
    ]
    return "\n".join(lines + ([""] + links if links else []))


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
    conflict  = resolved["conflict"]
    advice    = resolved["advice"]
    counter   = resolved["counter_tfs"]

    lines = [
        "<pre>",
        "XAU/USD  ANALYSIS CARD",
        WIDE,
        f"Price   : {fmt_price(first.price)}  |  {mkt_line}",
        f"Session : {session}",
        WIDE,
        "WHAT TO DO",
        SEP,
    ]

    if master in ("BUY", "SELL"):
        lines += [
            f"Direction : {master}",
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
            f"RSI     : {a.rsi_value:.0f}  |  "
            f"Stoch {a.stoch_k_val:.0f}/{a.stoch_d_val:.0f}",
        ]
        lines += _entry_confirmation_lines(a, compact=True)
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
                if win_pct < 68:      reasons.append(f"win {win_pct}%&lt;68%")
                if a.adx < 25:        reasons.append(f"ADX {a.adx:.0f}&lt;25")
                if a.setup_quality not in ("A+", "A"): reasons.append(f"grade {a.setup_quality}")
                gate_note = f" ({', '.join(reasons)})"
            else:
                gate_note = " — alert will fire"
            lines.append(f"Win %   : {_win_bar(win_pct)}")
            lines.append(f"Alert   : {gate_icon}{gate_note}")
        else:
            lines.append(f"Win %   : —")
            lines.append("Alert   : ⏳ no active signal")
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
    return safe_html("\n".join(lines))


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


def institutional_multi_timeframe_card(combined: dict) -> str:
    """Render the strict Daily → H4 → H1 → M15 institutional decision."""
    analyses = combined.get("analyses", {})
    lines = [
        "<pre>",
        "XAU/USD  INSTITUTIONAL MULTI-TIMEFRAME",
        "══════════════════════════════════",
        f"FINAL BIAS : {combined.get('final_bias', 'Neutral')}",
        f"BULL / BEAR: {combined.get('bullish_probability', 50)}% / {combined.get('bearish_probability', 50)}%",
        f"CONFIDENCE : Earned {combined.get('confidence_score', 0)}/100 | Max 100/100",
        "══════════════════════════════════",
        "DAILY → H4 → H1 → M15",
        "All four timeframes must align before a trade.",
        "",
    ]
    for tf in ("D1", "H4", "H1", "M15"):
        a = analyses.get(tf)
        if not a:
            continue
        report = getattr(a, "institutional_report", {}) or {}
        structure = report.get("market_structure", {})
        score_breakdown = report.get("score_breakdown", {}) or {}
        lines += [
            f"{tf:<4} {report.get('direction', 'WAIT'):<4} "
            f"Score {getattr(a, 'confidence_score', 0)}/100 of max 100 "
            f"Risk {getattr(a, 'risk_level', 'HIGH')}",
            f"     Trend {structure.get('trend', 'N/A')} | "
            f"BOS {structure.get('bos', 'NONE')} | "
            f"CHoCH {structure.get('choch', 'NONE')}",
            f"     T {score_breakdown.get('trend_alignment', 0)}/25 "
            f"S {score_breakdown.get('market_structure', 0)}/25 "
            f"L {score_breakdown.get('liquidity_confirmation', 0)}/20 "
            f"OB {score_breakdown.get('order_block_reaction', 0)}/15 "
            f"FVG {score_breakdown.get('fair_value_gap_confirmation', 0)}/10 "
            f"C {score_breakdown.get('candlestick_confirmation', 0)}/5",
        ]
        for reason in (getattr(a, "reasons_supporting", []) or [])[:2]:
            lines.append(f"     + {reason}")
        for reason in (getattr(a, "reasons_against", []) or [])[:1]:
            lines.append(f"     - {reason}")
        lines.append("")
    if combined.get("reversal_timeframes"):
        lines.append("REVERSAL EVIDENCE: " + ", ".join(combined["reversal_timeframes"]))
    lines += [
        "",
        "Rule: mixed evidence = Neutral / No Trade.",
        "Confidence below 60 = No Trade.",
        "Not financial advice.",
        "</pre>",
    ]
    return safe_html("\n".join(lines))


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
        f"  Williams%R: {getattr(a,'willr_value',-50):.1f}" + (f"  ← {a.willr_caution}" if getattr(a,'willr_caution','') else ""),
        f"  Supertrend: {'▲ bullish' if getattr(a,'supertrend_direction','')=='BUY' else ('▼ bearish' if getattr(a,'supertrend_direction','')=='SELL' else 'neutral')}",
        f"  CCI(20)   : {getattr(a,'cci_value',0.0):.0f}",
        f"  VWAP      : {getattr(a,'vwap',0.0):,.2f}   ({'above' if a.price > getattr(a,'vwap',a.price) else 'below'} VWAP)",
        f"  BB BW     : {getattr(a,'bb_bandwidth',0.0):.2f}%   Regime: {getattr(a,'market_regime','NORMAL')}",
        f"  Votes     : BUY {a.buy_votes}/8   SELL {a.sell_votes}/8",
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
        "  Use /signal to request an on-demand scan.",
        "</pre>",
    ]
    return "\n".join(lines)


def history_card(trades: list, stats: dict) -> str:
    """Signal history panel — today's trades only (UTC day boundary)."""
    from datetime import datetime, timezone

    def _status_label(t: dict) -> str:
        s = t.get("status", "")
        if s == "open":         return "OPEN     "
        if s == "tp3_hit":      return "ALL TP HIT"
        if s == "tp2_hit":      return "WIN   TP2"
        if s == "tp1_hit":      return "WIN   TP1"
        if s == "tp1_sl_hit":   return "TP1 / SL "
        if s == "sl_hit":       return "LOSS  SL "
        if s == "expired":      return "EXPIRED  "
        if s == "replaced":     return "REPLACED "
        return s.upper()[:9]

    def _fmt_time(ts) -> str:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
        except Exception:
            return "-----"

    def _stream_label(t: dict) -> str:
        return {
            "scalp": "SCALP",
            "intraday": "INTRA-HOUR",
        }.get(str(t.get("mode") or "").lower(), "")

    # ── Today's UTC day boundary ───────────────────────────────────────────────
    now_utc   = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%d %b %Y")
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    today_trades = [t for t in trades if t.get("opened_at", 0) >= day_start]
    # Older open trades that are still in play → should use /active
    older_open   = [
        t for t in trades
        if t.get("opened_at", 0) < day_start
        and t.get("status") in ("open", "tp1_hit", "tp2_hit")
    ]

    # Today-only stats
    wins         = sum(1 for t in today_trades if t.get("status") in ("tp1_hit", "tp2_hit", "tp3_hit", "tp1_sl_hit"))
    losses       = sum(1 for t in today_trades if t.get("status") == "sl_hit")
    open_today   = sum(1 for t in today_trades if t.get("status") in ("open", "tp1_hit", "tp2_hit"))
    total_closed = wins + losses
    win_rate     = round((wins / total_closed) * 100) if total_closed > 0 else 0

    lines = ["<pre>",
        "╔══════════════════════════════════╗",
        "║   XAU/USD  TODAY'S SIGNALS       ║",
        "╚══════════════════════════════════╝",
        f"  {today_str}  (UTC)",
        "",
        "  TODAY'S SUMMARY",
        "──────────────────────────────────",
        f"  Signals today : {len(today_trades)}",
        f"  Wins          : {wins}",
        f"  Losses        : {losses}",
        f"  Open          : {open_today}",
        f"  Win rate      : {win_rate}%"
        + ("" if total_closed == 0 else f"  ({total_closed} closed)"),
        "──────────────────────────────────",
    ]

    if older_open:
        lines += [
            "",
            f"  ⚠️  {len(older_open)} trade(s) from previous day(s)",
            "  still running — use /active to track.",
            "──────────────────────────────────",
        ]

    if not today_trades:
        lines += [
            "",
            "  No signals fired today yet.",
            "  Alerts fire automatically when",
            "  a BUY or SELL is detected.",
            "</pre>",
        ]
        return "\n".join(lines)

    lines += ["", "  SIGNALS  (newest first)", "──────────────────────────────────"]

    for t in today_trades:
        time_str = _fmt_time(t.get("opened_at", 0))
        stream    = _stream_label(t)
        dir_     = t.get("direction", "???")
        tf       = t.get("timeframe", "??")
        entry    = t.get("entry", 0)
        conf     = t.get("confidence", 0)
        outcome  = _status_label(t)
        lines += [
            f"  {stream + '  ' if stream else ''}{time_str}  {dir_:<4} {tf:<3}  Conf: {conf}%",
            f"  Entry: {entry:,.2f}",
            f"  Result: {outcome}",
            "  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·",
        ]

    lines += ["", "  Use /active to view live P&L.", "</pre>"]
    return safe_html("\n".join(lines))


def restart_summary_card(open_trades: list, recent_trades: list, stats: dict) -> str:
    """Sent to all subscribers when the bot restarts — shows open positions + last 5 signals."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%d %b  %H:%M UTC")

    def _status_label(s: str) -> str:
        return {
            "open":        "OPEN",
            "tp3_hit":     "ALL TP HIT",
            "tp2_hit":     "WIN  TP2",
            "tp1_hit":     "WIN  TP1",
            "tp1_sl_hit":  "TP1 / SL",
            "sl_hit":      "LOSS SL",
            "expired":     "EXPIRED",
            "replaced":    "REPLACED",
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
        "  Use /signal to request a live scan.",
        "  Use /history to view the complete log.",
        "</pre>",
    ]
    return "\n".join(lines)


def welcome_text(name: str) -> str:
    ms = market_status()
    if ms["is_open"]:
        mkt = f"Market is OPEN — {ms['note']}. Live signals are available."
    else:
        mkt = f"Market is CLOSED — {ms['note']}."
    return (
        f"Welcome, {name}.\n\n"
        "<b>XAU/USD Gold Analysis Bot</b>\n\n"
        "Institutional-grade market analysis and structured trade plans.\n"
        f"{mkt}\n\n"
        "Select an option from the menu below."
    )


def help_text() -> str:
    cmds = [
        ("/signal",    "Trade signal — BUY/SELL with a complete trade plan"),
        ("/analyze",   "Complete analysis — structure, indicators, levels, and entry"),
        ("/recommend", "Recommendation with an indicator breakdown"),
        ("/chart",     "Live chart with AI vision analysis"),
        ("/trend",     "Trend direction, structure, and momentum"),
        ("/levels",    "Support, resistance, BB, and ATR levels"),
        ("/outlook",   "Market outlook and potential scenarios"),
        ("/active",    "View open trades and live P&L"),
        ("/history",   "View recent trade results"),
        ("/news",      "View the latest gold market headlines"),
        ("/alerts",    "Manage automatic alert preferences"),
        ("/mode",      "Select Scalp, Intra-hour, combined, Swing, or Position mode"),
        ("/settings",  "Change the analysis mode and timeframe"),
        ("/help",      "Display this command reference"),
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
        "<b>Analysis Modes:</b>",
        "⚡ Scalp    — M1/M3/M5/M15 (short-term momentum)",
        "📊 Intraday — M15/M30/H1 (same-session analysis)",
        "🌊 Swing    — H4/D1/W1 (multi-day structure)",
        "🏛️ Position — D1/W1/MN1 (macro-trend analysis)",
    ]
    return "\n".join(lines)


def active_trades_card(open_trades: list, current_price: float) -> str:
    """Render the live trade panel without showing misleading fallback data.

    A failed spot-price request is represented by ``0.0`` by the data layer.
    That value must never be rendered as a real market price or used to
    calculate P&L.  Trade records are persisted JSON, so this renderer also
    validates the fields it reads instead of allowing one malformed record to
    break the whole Telegram message.
    """
    SEP  = "─" * 30
    WIDE = "═" * 30

    try:
        live_price = float(current_price)
        price_available = math.isfinite(live_price) and live_price > 0
    except (TypeError, ValueError):
        live_price = 0.0
        price_available = False

    lines = [
        "<pre>",
        "ACTIVE TRADES  |  XAU/USD",
        WIDE,
        (
            f"Live Price : {live_price:,.2f}"
            if price_available
            else "Live Price : UNAVAILABLE"
        ),
        SEP,
    ]

    if not open_trades:
        lines += ["No open trades.", WIDE, "</pre>"]
        return safe_html("\n".join(lines))

    for i, t in enumerate(open_trades):
        direction = _esc(str(t.get("direction", "?")).upper())
        tf        = _esc(t.get("timeframe", "?"))
        conf      = _esc(t.get("confidence", "—"))
        opened_at = t.get("opened_at", 0)

        try:
            entry = float(t["entry"])
            limit_entry = (
                float(t["limit_entry"])
                if t.get("limit_entry") is not None
                else None
            )
            sl = float(t["sl"])
            tp1 = float(t["tp1"])
            tp2 = float(t["tp2"]) if t.get("tp2") is not None else None
            tp3 = float(t["tp3"]) if t.get("tp3") is not None else None
            valid_levels = all(
                math.isfinite(value)
                for value in (entry, sl, tp1)
                if value is not None
            )
            valid_levels = valid_levels and all(
                value is None or math.isfinite(value)
                for value in (limit_entry, tp2, tp3)
            )
        except (KeyError, TypeError, ValueError):
            valid_levels = False

        if not valid_levels:
            lines += [
                f"Trade {i + 1}  |  DATA ERROR",
                "This saved trade has incomplete price levels.",
            ]
            if i < len(open_trades) - 1:
                lines.append(WIDE)
            continue

        # This is the quoted XAU/USD price move from the tracked market entry,
        # not account currency or broker pips. Pip size varies by broker, while
        # the trade levels in this bot are stored directly as XAU/USD prices.
        if price_available:
            pnl = (live_price - entry) if direction == "BUY" else (entry - live_price)
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_label = "IN PROFIT" if pnl >= 0 else "IN LOSS"
            pnl_line = f"Price Move  : {pnl_sign}{pnl:,.2f} USD/oz  ({pnl_label})"
            now_line = f"Now         : {live_price:,.2f}"
            limit_move_line = ""
            if limit_entry and not math.isclose(limit_entry, entry, abs_tol=0.005):
                limit_move = (
                    (live_price - limit_entry)
                    if direction == "BUY"
                    else (limit_entry - live_price)
                )
                limit_sign = "+" if limit_move >= 0 else ""
                limit_move_line = (
                    f"Limit Move  : {limit_sign}{limit_move:,.2f} USD/oz  "
                    "(from limit level; fill not confirmed)"
                )
        else:
            pnl_line = "Price Move  : unavailable (no live price)"
            now_line = "Now         : unavailable"
            limit_move_line = ""

        # Distances
        # The trade plan is frozen at entry.  Distances in this panel must
        # describe the actual risk/reward plan, not change every time price
        # moves; using current_price here made SL/TP look misleadingly alike.
        risk_dist = abs(entry - sl)
        tp1_dist = abs(entry - tp1)

        # Age
        try:
            age_secs = max(0, time.time() - float(opened_at)) if opened_at else 0
        except (TypeError, ValueError):
            age_secs = 0
        if age_secs < 3600:
            age_str = f"{int(age_secs // 60)}m ago"
        else:
            age_str = f"{int(age_secs // 3600)}h {int((age_secs % 3600) // 60)}m ago"

        # Flags are retained on every trade and are more reliable than a
        # status string from an older saved record.  If the live quote has
        # already crossed a target but the background tracker has not recorded
        # it yet (for example while the market is closed), show that explicitly
        # instead of making the trade look as though it is still before TP1.
        if t.get("tp2_hit") and tp3:
            status_note = "TP1 + TP2 HIT — next TP3"
        elif t.get("tp1_hit"):
            status_note = "TP1 HIT — next TP2"
        else:
            status_note = "OPEN — watching TP1"

        def _target_reached(target):
            if not price_available or target is None:
                return False
            return live_price >= target if direction == "BUY" else live_price <= target

        crossed_target = None
        for target_name, target_value in (
            ("TP1", tp1),
            ("TP2", tp2),
            ("TP3", tp3),
        ):
            if _target_reached(target_value):
                crossed_target = target_name
        tracker_status = ""
        if crossed_target:
            status_note = f"{crossed_target} REACHED — tracker update pending"
            tracker_status = f"Tracker     : saved as {str(t.get('status', 'unknown')).upper()}"

        mode = _esc(str(t.get("mode", "unknown")).title())
        lines += [
            f"{tf}  {direction}  |  {status_note}",
            f"Mode        : {mode}  |  Confidence: {conf}%",
            f"Opened      : {age_str}",
            f"Market Entry: {entry:,.2f}  (tracked basis)",
            *(
                [f"Limit Entry : {limit_entry:,.2f}  (optional pullback level)"]
                if limit_entry and not math.isclose(limit_entry, entry, abs_tol=0.005)
                else []
            ),
            now_line,
            pnl_line,
            limit_move_line,
            "Unit        : XAU/USD price difference (not broker pips)",
            SEP,
            f"SL          : {sl:,.2f}  (distance {risk_dist:,.2f})",
        ]
        if tracker_status:
            lines.insert(-2, tracker_status)
        tp1_r = (tp1_dist / risk_dist) if risk_dist else 0
        tp1_mark = "  ✓ recorded" if t.get("tp1_hit") else (
            "  ⚠ crossed" if _target_reached(tp1) else ""
        )
        lines.append(f"TP1         : {tp1:,.2f}  (distance {tp1_dist:,.2f}, 1:{tp1_r:.1f}R){tp1_mark}")
        if tp2:
            tp2_dist = abs(entry - tp2)
            tp2_r = (tp2_dist / risk_dist) if risk_dist else 0
            tp2_mark = "  ✓ recorded" if t.get("tp2_hit") else (
                "  ⚠ crossed" if _target_reached(tp2) else ""
            )
            lines.append(f"TP2         : {tp2:,.2f}  (distance {tp2_dist:,.2f}, 1:{tp2_r:.1f}R){tp2_mark}")
        if tp3:
            tp3_dist = abs(entry - tp3)
            tp3_r = (tp3_dist / risk_dist) if risk_dist else 0
            tp3_mark = "  ✓ recorded" if t.get("tp3_hit") else (
                "  ⚠ crossed" if _target_reached(tp3) else ""
            )
            lines.append(f"TP3         : {tp3:,.2f}  (distance {tp3_dist:,.2f}, 1:{tp3_r:.1f}R){tp3_mark}")

        if i < len(open_trades) - 1:
            lines.append(WIDE)

    lines += [WIDE, "</pre>"]
    return safe_html("\n".join(lines))


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
