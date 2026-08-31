import asyncio
import logging
import math
import random
import time
from typing import Optional, Dict, List, Tuple

import aiohttp

logger = logging.getLogger(__name__)

YF_CHART   = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
GOLDAPI    = "https://api.gold-api.com/price/XAU"
SWISSQUOTE = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept":     "application/json, text/plain, */*",
}

TF_PARAMS: Dict[str, Dict] = {
    "M1":  {"interval": "1m",  "range": "7d"},
    # Yahoo does not provide a dependable native 3-minute interval. M3 is
    # built from 1-minute candles below so Scalp Mode can still use it.
    "M3":  {"interval": "1m",  "range": "7d", "aggregate": 3},
    "M5":  {"interval": "5m",  "range": "2d"},
    "M15": {"interval": "15m", "range": "5d"},
    "M30": {"interval": "30m", "range": "10d"},
    "H1":  {"interval": "1h",  "range": "5d"},
    "H4":  {"interval": "1h",  "range": "60d"},
    "D1":  {"interval": "1d",  "range": "6mo"},
    "W1":  {"interval": "1wk", "range": "5y"},
    "MN1": {"interval": "1mo", "range": "10y"},
}

MIN_CANDLES = 30

# ─── TTL Cache ────────────────────────────────────────────────────────────────
OHLCV_TTL  = 5 * 60   # 5 minutes
PRICE_TTL  = 30       # 30 seconds

_ohlcv_cache: Dict[str, Tuple["OHLCVData", float]] = {}
_price_cache: Tuple[float, float] = (0.0, 0.0)   # (price, timestamp)
_cache_lock = asyncio.Lock()


class OHLCVData:
    def __init__(self, opens, highs, lows, closes, volumes, spot_price: float = 0.0,
                 is_simulated: bool = False, timestamps: list = None):
        self.opens        = opens
        self.highs        = highs
        self.lows         = lows
        self.closes       = closes
        self.volumes      = volumes
        self.price        = spot_price if spot_price > 0 else (closes[-1] if closes else 0.0)
        self.is_simulated = is_simulated  # True when real data fetch failed — signals unreliable
        self.timestamps   = timestamps or []  # Unix timestamps per candle (open time)

    def __len__(self):
        return len(self.closes)


def _clean(series: list) -> list:
    return [x for x in series if x is not None and x > 0]


def _aligned_ohlcv_rows(quote: dict, raw_timestamps: list) -> tuple:
    """Clean OHLCV rows without breaking candle-column alignment.

    Yahoo can return an occasional null inside one quote column. Cleaning each
    column independently shifts later values against the wrong timestamp and
    creates synthetic candles. Invalid price rows are discarded as a unit;
    missing/zero volume is safe and becomes zero.
    """
    price_keys = ("open", "high", "low", "close")
    price_columns = [quote.get(key, []) for key in price_keys]
    if not all(price_columns):
        return [], [], [], [], [], []

    row_count = min(len(column) for column in price_columns)
    raw_volumes = quote.get("volume", [])
    rows = []
    valid_indices = []
    for i in range(row_count):
        try:
            prices = tuple(float(column[i]) for column in price_columns)
        except (TypeError, ValueError):
            continue
        if any(price <= 0 for price in prices):
            continue
        try:
            volume = float(raw_volumes[i]) if i < len(raw_volumes) and raw_volumes[i] else 0.0
        except (TypeError, ValueError):
            volume = 0.0
        rows.append((*prices, max(volume, 0.0)))
        valid_indices.append(i)

    opens = [row[0] for row in rows]
    highs = [row[1] for row in rows]
    lows = [row[2] for row in rows]
    closes = [row[3] for row in rows]
    volumes = [row[4] for row in rows]

    # Timestamps must stay aligned with the filtered rows. If Yahoo's
    # timestamps are incomplete, omit them rather than guessing.
    timestamps = []
    if len(raw_timestamps) >= row_count:
        try:
            timestamps = [float(raw_timestamps[i]) for i in valid_indices]
        except (TypeError, ValueError):
            timestamps = []
        if len(timestamps) != len(rows):
            timestamps = []

    return opens, highs, lows, closes, volumes, timestamps


def _aggregate_bars(data: "OHLCVData", step: int) -> "OHLCVData":
    n    = len(data.closes)
    opens, highs, lows, closes, volumes, timestamps = [], [], [], [], [], []
    for i in range(0, n - step + 1, step):
        opens.append(data.opens[i])
        highs.append(max(data.highs[i:i + step]))
        lows.append(min(data.lows[i:i + step]))
        closes.append(data.closes[i + step - 1])
        volumes.append(sum(v for v in data.volumes[i:i + step] if v))
        if data.timestamps:
            timestamps.append(data.timestamps[i])
    result              = OHLCVData(opens, highs, lows, closes, volumes,
                                    is_simulated=data.is_simulated,
                                    timestamps=timestamps)
    result.price        = data.price
    return result


# ─── Spot price sources ───────────────────────────────────────────────────────

async def _fetch_goldapi(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        async with session.get(GOLDAPI, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status == 200:
                d     = await r.json(content_type=None)
                price = d.get("price")
                if price and 500 < float(price) < 25000:
                    return float(price)
    except Exception as e:
        logger.warning(f"gold-api.com fetch failed: {e}")
    return None


async def _fetch_swissquote(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        async with session.get(SWISSQUOTE, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status == 200:
                d = await r.json(content_type=None)
                if isinstance(d, list) and d:
                    profiles = d[0].get("spreadProfilePrices", [])
                    if profiles:
                        bid = profiles[0].get("bid", 0)
                        ask = profiles[0].get("ask", 0)
                        mid = (float(bid) + float(ask)) / 2
                        if 500 < mid < 25000:
                            return mid
    except Exception as e:
        logger.warning(f"swissquote fetch failed: {e}")
    return None


def _first_valid_spot(results: list) -> Optional[float]:
    """Return the first validated spot quote from concurrent source results."""
    for result in results:
        if isinstance(result, (int, float)) and not isinstance(result, bool):
            value = float(result)
            if 500 < value < 25000:
                return value
    return None


async def _fetch_yf_last_close(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        url = f"{YF_CHART}?interval=1m&range=1d"
        async with session.get(url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d     = await r.json(content_type=None)
                price = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if price and 500 < float(price) < 25000:
                    logger.info(f"Fallback: YF futures {price:.2f} (includes basis)")
                    return float(price)
    except Exception as e:
        logger.warning(f"YF futures fetch failed: {e}")
    return None


async def get_gold_price() -> float:
    """XAU/USD spot price with 30-second TTL cache."""
    global _price_cache
    async with _cache_lock:
        cached_price, cached_ts = _price_cache
        if cached_price > 0 and (time.time() - cached_ts) < PRICE_TTL:
            return cached_price

    async with aiohttp.ClientSession() as session:
        # Race the two spot sources, fall back to futures
        tasks = [_fetch_goldapi(session), _fetch_swissquote(session)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, float) and res > 0:
                async with _cache_lock:
                    _price_cache = (res, time.time())
                logger.info(f"Spot price (gold-api): {res:.2f}")
                return res

        # Both spot sources failed — use futures
        price = await _fetch_yf_last_close(session)
        if price:
            async with _cache_lock:
                _price_cache = (price, time.time())
            return price

    logger.error("All price sources failed")
    return 0.0


# ─── Historical OHLCV ─────────────────────────────────────────────────────────

async def _fetch_ohlcv_raw(timeframe: str) -> Optional["OHLCVData"]:
    params       = TF_PARAMS.get(timeframe, TF_PARAMS["H1"])
    aggregate_h4 = timeframe == "H4"
    aggregate_m3 = timeframe == "M3"
    url          = f"{YF_CHART}?interval={params['interval']}&range={params['range']}"

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch OHLCV and spot price concurrently
            ohlcv_resp, spot_results = await asyncio.gather(
                session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)),
                asyncio.gather(
                    _fetch_goldapi(session),
                    _fetch_swissquote(session),
                    return_exceptions=True,
                ),
            )
            spot_price = _first_valid_spot(spot_results)

            async with ohlcv_resp as resp:
                if resp.status != 200:
                    logger.warning(f"YF returned {resp.status} for {timeframe}")
                    return None
                raw = await resp.json(content_type=None)

        chart   = raw.get("chart", {})
        results = chart.get("result") or []
        if not results:
            err = (chart.get("error") or {})
            logger.warning(
                f"YF returned no result for {timeframe}: "
                f"{err.get('description', 'unknown error')}"
            )
            return None
        result = results[0]
        quote  = result["indicators"]["quote"][0]
        opens, highs, lows, closes, volumes, timestamps = _aligned_ohlcv_rows(
            quote, result.get("timestamp", [])
        )

        min_len = min(len(opens), len(highs), len(lows), len(closes))
        if min_len < MIN_CANDLES:
            logger.warning(f"Not enough candles for {timeframe}: {min_len}")
            return None

        opens   = opens[:min_len]
        highs   = highs[:min_len]
        lows    = lows[:min_len]
        closes  = closes[:min_len]
        volumes = volumes[:min_len] if volumes else [0] * min_len
        timestamps = timestamps[:min_len]

        # Normalize futures OHLCV to spot prices by subtracting the basis.
        # Futures trade at a premium (cost of carry). Without this, all
        # calculated levels (SL, TP, S/R) come out ~$10-15 too high vs spot.
        futures_last = closes[-1]
        if spot_price and spot_price > 0 and 0 < (futures_last - spot_price) < 60:
            basis = futures_last - spot_price
            opens   = [round(o - basis, 2) for o in opens]
            highs   = [round(h - basis, 2) for h in highs]
            lows    = [round(l - basis, 2) for l in lows]
            closes  = [round(c - basis, 2) for c in closes]
            logger.info(f"[{timeframe}] Basis-adjusted {basis:+.2f} (futures {futures_last:.2f} → spot {spot_price:.2f})")
            effective_spot = spot_price
        else:
            effective_spot = spot_price if (spot_price and spot_price > 0) else futures_last

        data = OHLCVData(opens, highs, lows, closes, volumes, effective_spot,
                         timestamps=timestamps)

        if aggregate_h4:
            data = _aggregate_bars(data, 4)
        elif aggregate_m3:
            data = _aggregate_bars(data, 3)
            if len(data) < 10:
                logger.warning(f"Not enough {timeframe} candles after aggregation")
                return None

        logger.info(
            f"Fetched {len(data)} {timeframe} candles | "
            f"Spot: {data.price:.2f}  Basis: {futures_last - effective_spot:+.2f}"
        )
        return data

    except Exception as e:
        logger.error(f"OHLCV fetch failed [{timeframe}]: {e}")
        return None


async def fetch_ohlcv(timeframe: str) -> Optional["OHLCVData"]:
    """Fetch with 5-minute TTL cache per timeframe. Falls back to simulation if YF fails."""
    cached_data = None
    async with _cache_lock:
        if timeframe in _ohlcv_cache:
            cached_data, cached_ts = _ohlcv_cache[timeframe]
            if (time.time() - cached_ts) < OHLCV_TTL:
                logger.debug(f"OHLCV cache hit [{timeframe}]")
            else:
                cached_data = None

    if cached_data is not None:
        # Candle history can remain cached, but the live spot snapshot must not
        # become the entry price for several minutes. Fetch outside the cache
        # lock because get_gold_price uses the same lock internally.
        spot = await get_gold_price()
        if spot > 0:
            cached_data.price = spot
        return cached_data

    data = await _fetch_ohlcv_raw(timeframe)

    if data is None:
        logger.warning(f"OHLCV fetch failed for {timeframe} — using simulation fallback.")
        data = _simulate_ohlcv(timeframe)

    if data is not None:
        async with _cache_lock:
            _ohlcv_cache[timeframe] = (data, time.time())

    return data


def _simulate_ohlcv(timeframe: str, n: int = 80) -> "OHLCVData":
    """
    Generate realistic simulated OHLCV data seeded on the current time bucket.
    Used when Yahoo Finance is unreachable (e.g. weekend, network error).
    Produces a plausible random-walk chart around ~3,300 USD for chart rendering.
    """
    # Seed is stable per timeframe + 4-hour bucket so results are consistent
    bucket = int(time.time() // (4 * 3600))
    rng = random.Random(f"{timeframe}:{bucket}")

    # Base price — use last cached price if available, else 3300
    base = 3300.0
    for tf_key, (cached, _) in _ohlcv_cache.items():
        if cached and cached.price and cached.price > 500:
            base = cached.price
            break

    tf_volatility = {
        "M1": 0.00035, "M3": 0.00055,
        "M5": 0.0008, "M15": 0.0015, "M30": 0.0025,
        "H1": 0.004,  "H4": 0.010,   "D1":  0.018,
        "W1": 0.035, "MN1": 0.070,
    }.get(timeframe, 0.004)

    opens, highs, lows, closes, volumes = [], [], [], [], []
    price = base * rng.uniform(0.985, 1.015)

    for _ in range(n):
        move = rng.gauss(0, tf_volatility) * price
        open_p  = price
        close_p = price + move
        wick_h  = abs(move) * rng.uniform(0.3, 1.5)
        wick_l  = abs(move) * rng.uniform(0.3, 1.5)
        high_p  = max(open_p, close_p) + wick_h
        low_p   = min(open_p, close_p) - wick_l
        vol     = rng.uniform(800, 4000)

        opens.append(round(open_p, 2))
        highs.append(round(high_p, 2))
        lows.append(round(low_p, 2))
        closes.append(round(close_p, 2))
        volumes.append(round(vol))
        price = close_p

    return OHLCVData(opens, highs, lows, closes, volumes, spot_price=closes[-1],
                     is_simulated=True)


def invalidate_cache(timeframe: str = None) -> None:
    """Force-expire cache — call after trade alert fires."""
    global _price_cache
    if timeframe:
        _ohlcv_cache.pop(timeframe, None)
    else:
        _ohlcv_cache.clear()
    _price_cache = (0.0, 0.0)
