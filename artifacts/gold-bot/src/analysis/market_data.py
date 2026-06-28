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
GOLDPRICE  = "https://data-asg.goldprice.org/dbXRates/USD"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept":     "application/json, text/plain, */*",
}

TF_PARAMS: Dict[str, Dict] = {
    "M5":  {"interval": "5m",  "range": "2d"},
    "M15": {"interval": "15m", "range": "5d"},
    "M30": {"interval": "30m", "range": "10d"},
    "H1":  {"interval": "1h",  "range": "5d"},
    "H4":  {"interval": "1h",  "range": "60d"},
    "D1":  {"interval": "1d",  "range": "6mo"},
}

MIN_CANDLES = 30

# ─── TTL Cache ────────────────────────────────────────────────────────────────
OHLCV_TTL  = 5 * 60   # 5 minutes
PRICE_TTL  = 30       # 30 seconds

_ohlcv_cache: Dict[str, Tuple["OHLCVData", float]] = {}
_price_cache: Tuple[float, float] = (0.0, 0.0)   # (price, timestamp)
_cache_lock = asyncio.Lock()


class OHLCVData:
    def __init__(self, opens, highs, lows, closes, volumes, spot_price: float = 0.0):
        self.opens   = opens
        self.highs   = highs
        self.lows    = lows
        self.closes  = closes
        self.volumes = volumes
        self.price   = spot_price if spot_price > 0 else (closes[-1] if closes else 0.0)

    def __len__(self):
        return len(self.closes)


def _clean(series: list) -> list:
    return [x for x in series if x is not None and x > 0]


def _aggregate_to_h4(data: "OHLCVData") -> "OHLCVData":
    step = 4
    n    = len(data.closes)
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i in range(0, n - step + 1, step):
        opens.append(data.opens[i])
        highs.append(max(data.highs[i:i + step]))
        lows.append(min(data.lows[i:i + step]))
        closes.append(data.closes[i + step - 1])
        volumes.append(sum(v for v in data.volumes[i:i + step] if v))
    result       = OHLCVData(opens, highs, lows, closes, volumes)
    result.price = data.price
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
        logger.debug(f"gold-api.com: {e}")
    return None


async def _fetch_goldprice_org(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        import json as _json
        async with session.get(GOLDPRICE, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status == 200:
                text  = await r.text()
                d     = _json.loads(text)
                for item in d.get("items", []):
                    if item.get("curr") == "USD":
                        p = item.get("xauPrice")
                        if p and 500 < float(p) < 25000:
                            return float(p)
    except Exception as e:
        logger.debug(f"goldprice.org: {e}")
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
        logger.debug(f"YF futures: {e}")
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
        tasks = [_fetch_goldapi(session), _fetch_goldprice_org(session)]
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
    url          = f"{YF_CHART}?interval={params['interval']}&range={params['range']}"

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch OHLCV and spot price concurrently
            ohlcv_resp, spot_price = await asyncio.gather(
                session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)),
                _fetch_goldapi(session),
            )

            async with ohlcv_resp as resp:
                if resp.status != 200:
                    logger.warning(f"YF returned {resp.status} for {timeframe}")
                    return None
                raw = await resp.json(content_type=None)

        result = raw["chart"]["result"][0]
        quote  = result["indicators"]["quote"][0]

        opens   = _clean(quote.get("open",   []))
        highs   = _clean(quote.get("high",   []))
        lows    = _clean(quote.get("low",    []))
        closes  = _clean(quote.get("close",  []))
        volumes = _clean(quote.get("volume", []))

        min_len = min(len(opens), len(highs), len(lows), len(closes))
        if min_len < MIN_CANDLES:
            logger.warning(f"Not enough candles for {timeframe}: {min_len}")
            return None

        opens   = opens[:min_len]
        highs   = highs[:min_len]
        lows    = lows[:min_len]
        closes  = closes[:min_len]
        volumes = volumes[:min_len] if volumes else [0] * min_len

        # Use spot price for display; historical closes are futures data (fine for TA)
        effective_spot = (spot_price or 0.0) if (spot_price and spot_price > 0) else closes[-1]
        data = OHLCVData(opens, highs, lows, closes, volumes, effective_spot)

        if aggregate_h4:
            data = _aggregate_to_h4(data)
            if len(data) < 10:
                logger.warning("Not enough H4 candles after aggregation")
                return None

        logger.info(
            f"Fetched {len(data)} {timeframe} candles | "
            f"Spot: {data.price:.2f}  Futures close: {closes[-1]:.2f}"
        )
        return data

    except Exception as e:
        logger.error(f"OHLCV fetch failed [{timeframe}]: {e}")
        return None


async def fetch_ohlcv(timeframe: str) -> Optional["OHLCVData"]:
    """Fetch with 5-minute TTL cache per timeframe. Falls back to simulation if YF fails."""
    async with _cache_lock:
        if timeframe in _ohlcv_cache:
            cached_data, cached_ts = _ohlcv_cache[timeframe]
            if (time.time() - cached_ts) < OHLCV_TTL:
                logger.debug(f"OHLCV cache hit [{timeframe}]")
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
        "M5": 0.0008, "M15": 0.0015, "M30": 0.0025,
        "H1": 0.004,  "H4": 0.010,   "D1":  0.018,
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

    return OHLCVData(opens, highs, lows, closes, volumes, spot_price=closes[-1])


def invalidate_cache(timeframe: str = None) -> None:
    """Force-expire cache — call after trade alert fires."""
    global _price_cache
    if timeframe:
        _ohlcv_cache.pop(timeframe, None)
    else:
        _ohlcv_cache.clear()
    _price_cache = (0.0, 0.0)
