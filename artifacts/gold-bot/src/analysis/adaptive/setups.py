"""Early but rule-based pullback, sweep, breakout, retest and zone reactions."""


def detect_setups(data, structure, context, ema20, ema50, atr_value, profile):
    if not atr_value or atr_value <= 0:
        return []
    i = len(data.closes) - 1
    o, h, l, c = map(float, (data.opens[i], data.highs[i], data.lows[i], data.closes[i]))
    prev_c = float(data.closes[i-1])
    body = abs(c-o)
    span = max(h-l, .00001)
    lower_wick, upper_wick = min(o,c)-l, h-max(o,c)
    close_strength_buy = (c-l)/span
    close_strength_sell = (h-c)/span
    items = []

    # Structural boundaries exclude the current candle to avoid self-confirming breaks.
    window = min(profile.structure_lookback, i)
    prior_high = max(data.highs[i-window:i]) if window >= 12 else None
    prior_low = min(data.lows[i-window:i]) if window >= 12 else None
    max_break_range = 2.0 if profile.setup_timeframe in ("D1", "W1", "MN1", "H4") else 1.65

    if prior_high and c > prior_high and c > o and body >= atr_value*.25 and close_strength_buy >= .68 and span <= atr_value*max_break_range:
        items.append({"direction":"BUY","type":"breakout","level":float(prior_high),"trigger":"completed close above structural range with a directional body"})
    if prior_low and c < prior_low and c < o and body >= atr_value*.25 and close_strength_sell >= .68 and span <= atr_value*max_break_range:
        items.append({"direction":"SELL","type":"breakout","level":float(prior_low),"trigger":"completed close below structural range with a directional body"})

    # Retest: previous close broke a confirmed boundary; current candle tests and holds it.
    if i >= 2:
        prior_boundary_high = max(data.highs[i-window-1:i-1]) if window >= 12 else None
        prior_boundary_low = min(data.lows[i-window-1:i-1]) if window >= 12 else None
        tol = atr_value*.22
        if prior_boundary_high and data.closes[i-1] > prior_boundary_high and l <= prior_boundary_high+tol and c > prior_boundary_high and c >= o:
            items.append({"direction":"BUY","type":"breakout_retest","level":float(prior_boundary_high),"trigger":"broken resistance retested and held on the completed candle"})
        if prior_boundary_low and data.closes[i-1] < prior_boundary_low and h >= prior_boundary_low-tol and c < prior_boundary_low and c <= o:
            items.append({"direction":"SELL","type":"breakout_retest","level":float(prior_boundary_low),"trigger":"broken support retested and held on the completed candle"})

    # A meaningful sweep must pierce a prior range boundary, close back inside, and reject.
    from .structure import liquidity_sweep
    sweep = liquidity_sweep(data, lookback=min(16, max(8, window//2)), atr_value=atr_value)
    if sweep:
        level = structure["support"] if sweep == "BUY" else structure["resistance"]
        items.append({"direction":sweep,"type":"liquidity_sweep","level":level,"trigger":"prior range liquidity swept and price closed back inside with rejection"})

    # S/R rejection and pullback continuation are distinct from a raw wick.
    near = atr_value*.45
    if structure["support"] is not None and abs(l-structure["support"]) <= near and c > o and lower_wick >= max(body*.45, atr_value*.04):
        items.append({"direction":"BUY","type":"support_resistance_reaction","level":structure["support"],"trigger":"support was tested and rejected on a completed candle"})
    if structure["resistance"] is not None and abs(h-structure["resistance"]) <= near and c < o and upper_wick >= max(body*.45, atr_value*.04):
        items.append({"direction":"SELL","type":"support_resistance_reaction","level":structure["resistance"],"trigger":"resistance was tested and rejected on a completed candle"})

    if context and context["trend"] in ("BULLISH", "BEARISH"):
        direction = "BUY" if context["trend"] == "BULLISH" else "SELL"
        near_ma = ema20 is not None and abs(c-ema20) <= atr_value*.65
        near_level = (
            structure["support"] is not None and abs(l-structure["support"]) <= near
            if direction == "BUY"
            else structure["resistance"] is not None and abs(h-structure["resistance"]) <= near
        )
        rejection = (
            c > o and (lower_wick >= body*.35 or c > prev_c)
            if direction == "BUY"
            else c < o and (upper_wick >= body*.35 or c < prev_c)
        )
        if (near_ma or near_level) and rejection:
            items.append({"direction":direction,"type":"trend_pullback","level":structure["support"] if direction=="BUY" else structure["resistance"],"trigger":"higher-timeframe trend pullback reached structure/EMA20 and began to recover"})

    # Preserve independent setup names but avoid duplicate points for the same evidence layer.
    unique = {}
    for item in items:
        unique[(item["direction"], item["type"])] = item
    return list(unique.values())
