"""Candle validation and completed-bar filtering for live weighted analysis."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from src.analysis.market_data import OHLCVData, _candle_end_timestamp


@dataclass(frozen=True)
class PreparedCandles:
    data: OHLCVData | None
    status: str
    reason: str
    latest_completed_timestamp: float | None


def prepare_completed_candles(
    data: OHLCVData | None,
    timeframe: str,
    *,
    now: float | None = None,
    max_age_bars: float = 3.0,
) -> PreparedCandles:
    if data is None:
        return PreparedCandles(None, "missing", "No candle data was returned.", None)
    size = len(data.closes)
    columns = (data.opens, data.highs, data.lows, data.closes, data.volumes)
    if size < 2 or any(len(column) != size for column in columns):
        return PreparedCandles(None, "invalid", "OHLCV columns are not aligned.", None)
    if not data.timestamps or len(data.timestamps) != size:
        return PreparedCandles(
            None,
            "timestamps_unavailable",
            "Candle timestamps are required to exclude an unfinished candle.",
            None,
        )
    current_time = float(now if now is not None else time.time())
    valid_indices = []
    try:
        for index, (opening, high, low, close, volume, timestamp) in enumerate(
            zip(*columns, data.timestamps)
        ):
            values = (opening, high, low, close, volume, timestamp)
            if not all(math.isfinite(float(value)) for value in values):
                continue
            opening, high, low, close = map(float, values[:4])
            if (
                opening <= 0 or high <= 0 or low <= 0 or close <= 0
                or high < max(opening, close)
                or low > min(opening, close)
                or high < low
            ):
                continue
            candle_end = _candle_end_timestamp(float(timestamp), timeframe)
            if candle_end <= current_time:
                valid_indices.append(index)
    except (TypeError, ValueError, OverflowError):
        return PreparedCandles(None, "invalid", "Candle values or timestamps are invalid.", None)

    if len(valid_indices) < 30:
        return PreparedCandles(
            None,
            "insufficient",
            "Fewer than 30 valid completed candles are available.",
            None,
        )
    last_index = valid_indices[-1]
    latest_timestamp = float(data.timestamps[last_index])
    latest_end = _candle_end_timestamp(latest_timestamp, timeframe)
    try:
        seconds_per_bar = latest_end - latest_timestamp
    except Exception:
        seconds_per_bar = 0
    if seconds_per_bar <= 0:
        return PreparedCandles(None, "invalid", "Timeframe duration is invalid.", None)
    if current_time - latest_end > seconds_per_bar * max(1.0, max_age_bars):
        return PreparedCandles(
            None,
            "stale",
            f"Latest completed {timeframe} candle is older than the configured freshness window.",
            latest_timestamp,
        )

    indices = valid_indices
    prepared = OHLCVData(
        [data.opens[i] for i in indices],
        [data.highs[i] for i in indices],
        [data.lows[i] for i in indices],
        [data.closes[i] for i in indices],
        [data.volumes[i] for i in indices],
        spot_price=float(data.price or data.closes[last_index]),
        is_simulated=bool(data.is_simulated),
        timestamps=[float(data.timestamps[i]) for i in indices],
        fetched_at=float(data.fetched_at or current_time),
        price_source=data.price_source,
        candle_source=data.candle_source,
        symbol=data.symbol,
    )
    return PreparedCandles(prepared, "ready", "", latest_timestamp)

