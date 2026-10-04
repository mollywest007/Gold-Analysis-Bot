"""Real-candle replay for the selected mode's momentum-pullback strategy."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List

from src.analysis import engine
from src.analysis.market_data import (
    OHLCVData,
    fetch_historical_ohlcv,
)
from src.analysis.modes import MODES
from src.trade_tracker import _DEFAULT_MAX_TRADE_AGE, _TF_MAX_AGE


def _timeframe_seconds(timeframe: str) -> int:
    return {
        "M1": 60,
        "M3": 3 * 60,
        "M5": 5 * 60,
        "M15": 15 * 60,
        "M30": 30 * 60,
        "H1": 60 * 60,
        "H4": 4 * 60 * 60,
        "D1": 24 * 60 * 60,
        "W1": 7 * 24 * 60 * 60,
        "MN1": 30 * 24 * 60 * 60,
    }[timeframe]


def _seeded_ema_series(values: List[float], period: int) -> List[float]:
    """Match engine._ema(values[:i + 1], period) at every candle in O(n)."""
    if not values:
        return []
    result = []
    alpha = 2.0 / (period + 1.0)
    ema = 0.0
    for index, value in enumerate(values):
        if index + 1 < period:
            ema = sum(values[:index + 1]) / (index + 1)
        elif index + 1 == period:
            ema = sum(values[:period]) / period
        else:
            ema = float(value) * alpha + ema * (1.0 - alpha)
        result.append(ema)
    return result


def _rsi_series(values: List[float], period: int = 14) -> List[float]:
    """Compute causal RSI values using the engine's Wilder smoothing."""
    result = [50.0] * len(values)
    if len(values) < period + 2:
        return result

    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for close_index in range(period + 1, len(values)):
        delta_index = close_index - 1
        average_gain = (
            average_gain * (period - 1) + gains[delta_index]
        ) / period
        average_loss = (
            average_loss * (period - 1) + losses[delta_index]
        ) / period
        result[close_index] = round(
            100.0
            if average_loss == 0
            else 100.0 - (100.0 / (1.0 + average_gain / average_loss)),
            2,
        )
    return result


def _atr_series(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> List[float]:
    """Compute causal ATR values using the engine's Wilder smoothing."""
    result = [close * 0.005 for close in closes]
    if len(closes) < 2:
        return result

    true_ranges = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
        count = len(true_ranges)
        if count < period:
            result[index] = sum(true_ranges) / count
        elif count == period:
            result[index] = sum(true_ranges[:period]) / period
        else:
            result[index] = (
                result[index - 1] * (period - 1) + true_ranges[-1]
            ) / period
    return result


def _next_open_is_actionable(
    direction: str,
    next_open: float,
    entry: float,
    stop: float,
    tp1: float,
    atr: float,
) -> bool:
    near_entry_distance = max(abs(entry) * 0.00015, atr * 0.35, 0.05)
    if abs(next_open - entry) > near_entry_distance:
        return False
    if direction == "BUY":
        return stop < next_open < tp1
    if direction == "SELL":
        return tp1 < next_open < stop
    return False


def replay_momentum_pullback(
    data: OHLCVData,
    mode: str,
    timeframe: str,
) -> Dict:
    """Replay one mode on completed candles with no look-ahead or simulation.

    Each signal is considered at its candle close and may enter at the next
    candle's open only if it remains near the plan and between SL and TP1.
    If SL and TP1 are both touched within one candle, SL is counted first.
    Results describe first-touch outcomes, not net broker P&L: spread, fees,
    slippage, and partial-position sizing are not available in the feed.
    """
    mode_cfg = MODES.get(mode)
    if mode_cfg is None:
        raise ValueError(f"Unknown backtest mode: {mode}")
    if timeframe not in mode_cfg.scan_timeframes:
        raise ValueError(
            f"{timeframe} is not scanned by {mode_cfg.label} mode; "
            "choose a timeframe belonging to the selected mode."
        )
    if data is None or data.is_simulated:
        raise ValueError("Backtest requires real historical candles; simulated data is rejected.")
    if (
        not data.timestamps
        or len(data.timestamps) != len(data.closes)
        or any(
            len(column) != len(data.closes)
            for column in (data.opens, data.highs, data.lows, data.volumes)
        )
    ):
        raise ValueError("Backtest requires aligned candle timestamps and OHLC data.")
    if len(data.closes) < 100:
        raise ValueError(
            f"Only {len(data.closes)} completed candles are available; "
            "at least 100 are needed for a meaningful replay."
        )

    profile = mode_cfg.momentum_pullback
    closes = data.closes
    highs = data.highs
    lows = data.lows
    opens = data.opens
    ema20_live = _seeded_ema_series(closes, 20)
    ema50_live = _seeded_ema_series(closes, 50)
    ema20_chop = engine._ema_series(closes, 20)
    ema50_chop = engine._ema_series(closes, 50)
    rsi_values = _rsi_series(closes, 14)
    atr_values = _atr_series(highs, lows, closes, 14)

    # Preserve enough local history for every selected-mode lookback while
    # keeping the replay linear in the size of the downloaded dataset.
    analysis_window = max(
        80,
        mode_cfg.breakout_lookback + 3,
        profile.range_trap_lookback_candles + 3,
        profile.chop_lookback_candles * 2 + 3,
        profile.impulse_lookback_candles + profile.pullback_candles + 5,
    )
    first_signal_index = max(50, analysis_window - 1)
    if first_signal_index >= len(closes) - 1:
        raise ValueError("Not enough completed candles for this mode's lookback.")

    expected_bar_seconds = _timeframe_seconds(timeframe)
    max_age_seconds = _TF_MAX_AGE.get(timeframe, _DEFAULT_MAX_TRADE_AGE)

    trades = []
    generated_signals = 0
    skipped_stale_entries = 0
    expired = 0
    entry_delays_minutes = []
    index = first_signal_index

    while index < len(closes) - 1:
        window_start = max(0, index + 1 - analysis_window)
        local_closes = closes[window_start:index + 1]
        local_highs = highs[window_start:index + 1]
        local_lows = lows[window_start:index + 1]
        local_opens = opens[window_start:index + 1]
        local_ema20_chop = ema20_chop[window_start:index + 1]
        local_ema50_chop = ema50_chop[window_start:index + 1]
        price = closes[index]
        atr = max(float(atr_values[index] or 0.0), price * 0.0001, 0.01)
        previous_rsi = rsi_values[index - 1] if index > 0 else rsi_values[index]

        decision = engine._momentum_pullback_decision(
            local_opens,
            local_highs,
            local_lows,
            local_closes,
            price,
            ema20_live[index],
            ema50_live[index],
            atr,
            rsi_values[index],
            previous_rsi,
            mode_cfg=mode_cfg,
            ema20_history=local_ema20_chop,
            ema50_history=local_ema50_chop,
        )
        if decision.get("status") != "MODERATE ENTRY":
            index += 1
            continue

        generated_signals += 1
        direction = decision["direction"]
        entry = float(price)
        stop = float(decision["stop_loss"])
        tp1 = float(decision["tp1"])
        next_open = float(opens[index + 1])
        entry_delay_minutes = (
            float(data.timestamps[index + 1])
            - float(data.timestamps[index])
        ) / 60.0
        if (
            entry_delay_minutes <= 0
            or entry_delay_minutes * 60 > expected_bar_seconds * 1.5
        ):
            # Do not pretend a setup filled after a market closure or data gap.
            skipped_stale_entries += 1
            index += 1
            continue
        if not _next_open_is_actionable(
            direction, next_open, entry, stop, tp1, atr
        ):
            skipped_stale_entries += 1
            index += 1
            continue
        entry_delays_minutes.append(entry_delay_minutes)

        expiry_timestamp = float(data.timestamps[index + 1]) + max_age_seconds
        outcome = None
        exit_index = None
        expired_at_index = None
        for future_index in range(index + 1, len(closes)):
            if float(data.timestamps[future_index]) >= expiry_timestamp:
                expired_at_index = future_index
                break

            candle_high = float(highs[future_index])
            candle_low = float(lows[future_index])
            if direction == "BUY":
                stop_hit = candle_low <= stop
                target_hit = candle_high >= tp1
            else:
                stop_hit = candle_high >= stop
                target_hit = candle_low <= tp1

            # Same-candle ordering is unknowable from OHLC bars. Count the
            # adverse stop first rather than overstating target performance.
            if stop_hit:
                outcome = "SL"
                exit_index = future_index
                break
            if target_hit:
                outcome = "TP1"
                exit_index = future_index
                break

        if outcome:
            trades.append({
                "direction": direction,
                "entry": entry,
                "sl": stop,
                "tp1": tp1,
                "planned_rr": float(decision.get("rr") or 0.0),
                "setup_type": "Fast momentum pullback",
                "outcome": outcome,
                "bars_to_event": exit_index - index,
                "entry_delay_minutes": entry_delay_minutes,
                "signal_timestamp": float(data.timestamps[index]),
                "exit_timestamp": float(data.timestamps[exit_index]),
            })
            index = exit_index + 1
            continue

        if expired_at_index is not None:
            expired += 1
            index = expired_at_index + 1
        else:
            # The historical window ended before a verified exit or expiry.
            break

    wins = sum(trade["outcome"] == "TP1" for trade in trades)
    losses = sum(trade["outcome"] == "SL" for trade in trades)
    total_closed = wins + losses
    planned_rrs = [trade["planned_rr"] for trade in trades]
    bars_to_events = [trade["bars_to_event"] for trade in trades]
    longest_losing_streak = 0
    current_losing_streak = 0
    for trade in trades:
        if trade["outcome"] == "SL":
            current_losing_streak += 1
            longest_losing_streak = max(
                longest_losing_streak, current_losing_streak
            )
        else:
            current_losing_streak = 0

    return {
        "mode": mode,
        "mode_label": mode_cfg.label,
        "timeframe": timeframe,
        "historical_symbol": "GC=F",
        "historical_candle_source": data.candle_source,
        "candles": len(closes),
        "history_start": float(data.timestamps[0]),
        "history_end": float(data.timestamps[-1]),
        "generated_signals": generated_signals,
        "skipped_stale_entries": skipped_stale_entries,
        "closed_trades": total_closed,
        "tp1_first": wins,
        "sl_first": losses,
        "expired": expired,
        "unresolved": max(0, generated_signals - skipped_stale_entries - total_closed - expired),
        "first_target_rate": round(wins / total_closed * 100, 1) if total_closed else None,
        "average_planned_rr": (
            round(sum(planned_rrs) / len(planned_rrs), 2) if planned_rrs else None
        ),
        "average_bars_to_event": (
            round(sum(bars_to_events) / len(bars_to_events), 1)
            if bars_to_events else None
        ),
        "average_entry_delay_minutes": (
            round(sum(entry_delays_minutes) / len(entry_delays_minutes), 1)
            if entry_delays_minutes else None
        ),
        "loss_setup_types": {"Fast momentum pullback": losses} if losses else {},
        "longest_losing_streak": longest_losing_streak,
        "sample_sufficient": total_closed >= 50,
    }


async def run_historical_backtest(mode: str, timeframe: str) -> Dict:
    """Download the selected mode's real history and run its candle replay."""
    data = await fetch_historical_ohlcv(timeframe)
    if data is None:
        raise ValueError(
            f"Real completed historical candles are unavailable for {timeframe}; "
            "no simulated fallback was used."
        )
    return await asyncio.to_thread(replay_momentum_pullback, data, mode, timeframe)


def format_backtest_report(report: Dict) -> str:
    start = datetime.fromtimestamp(
        report["history_start"], tz=timezone.utc
    ).strftime("%Y-%m-%d")
    end = datetime.fromtimestamp(
        report["history_end"], tz=timezone.utc
    ).strftime("%Y-%m-%d")
    if report["sample_sufficient"]:
        sample_note = (
            "50+ closed outcomes; still a historical estimate, not a guarantee."
        )
        outcomes = (
            f"Closed first-touch outcomes: {report['closed_trades']} — "
            f"TP1 first: {report['tp1_first']}, SL first: {report['sl_first']}\n"
        )
        metrics = (
            f"TP1-first rate: {report['first_target_rate']:.1f}% | "
            f"Average planned R:R: {report['average_planned_rr']:.2f}R\n"
            f"Average setup-to-entry delay: "
            f"{report['average_entry_delay_minutes']:.1f} minutes | "
            f"Average bars to TP1/SL: "
            f"{report['average_bars_to_event']:.1f} | "
            f"Longest consecutive SLs: "
            f"{report['longest_losing_streak']}\n"
        )
        loss_setup = (
            "Fast momentum pullback (the only setup type in this engine)"
            if report["sl_first"]
            else "No SL-first outcomes"
        )
        loss_setup_line = f"SL loss setup: {loss_setup}\n"
        coverage = (
            f"Expired without target/stop: {report['expired']} | "
            f"Unresolved at history end: {report['unresolved']}\n"
        )
    else:
        sample_note = (
            f"Only {report['closed_trades']} valid closed outcomes were available "
            "(50 required); performance metrics are withheld."
        )
        outcomes = ""
        metrics = ""
        loss_setup_line = ""
        coverage = ""
    return (
        f"<b>Historical replay — {report['mode_label']} / {report['timeframe']}</b>\n"
        f"Historical source: Yahoo GC=F gold futures proxy, not XAU/USD spot.\n"
        f"Real completed candles: {report['candles']:,} ({start} to {end} UTC)\n"
        f"Generated setups: {report['generated_signals']} "
        f"(stale next-open entries skipped: {report['skipped_stale_entries']})\n"
        f"{outcomes}{metrics}{loss_setup_line}{coverage}"
        f"<i>{sample_note}</i>\n"
        "<i>First-touch replay only: same-candle SL/TP counts as SL. "
        "Spread, fees, slippage, and partial-position sizing are not modeled; "
        "this is not net P&amp;L.</i>"
    )