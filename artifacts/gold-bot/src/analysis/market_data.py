import logging
from typing import Optional, Dict, List
import aiohttp

logger = logging.getLogger(__name__)

YF_CHART   = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
GOLDAPI    = "https://api.gold-api.com/price/XAU"
GOLDPRICE  = "https://data-asg.goldprice.org/dbXRates/USD"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept": "application/json, text/plain, */*",
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
    n = len(data.closes)
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i in range(0, n - step + 1, step):
        opens.append(data.opens[i])
        highs.append(max(data.highs[i:i + step]))
        lows.append(min(data.lows[i:i + step]))
        closes.append(data.closes[i + step - 1])
        volumes.append(sum(v for v in data.volumes[i:i + step] if v))
    result = OHLCVData(opens, highs, lows, closes, volumes)
    result.price = data.price
    return result


# ─── Spot price (XAU/USD) ─────────────────────────────────────────────────────

async def _fetch_goldapi(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        async with session.get(GOLDAPI, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json(content_type=None)
                price = d.get("price")
                if price and 500 < float(price) < 20000:
                    logger.info(f"Spot price from gold-api.com: {price:.2f}")
                    return float(price)
    except Exception as e:
        logger.debug(f"gold-api.com failed: {e}")
    return None


async def _fetch_goldprice_org(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        async with session.get(GOLDPRICE, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                import json as _json
                text = await r.text()
                d = _json.loads(text)
                # field is 'xauPrice' (price per troy oz in USD)
                items = d.get("items", [])
                for item in items:
                    if item.get("curr") == "USD":
                        p = item.get("xauPrice")
                        if p and 500 < float(p) < 20000:
                            logger.info(f"Spot price from goldprice.org: {p:.2f}")
                            return float(p)
    except Exception as e:
        logger.debug(f"goldprice.org failed: {e}")
    return None


async def _fetch_yf_futures(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        url = f"{YF_CHART}?interval=1m&range=1d"
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json(content_type=None)
                price = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if price and 500 < float(price) < 20000:
                    logger.info(f"Price from YF GC=F futures: {price:.2f} (futures premium included)")
                    return float(price)
    except Exception as e:
        logger.debug(f"YF futures fallback failed: {e}")
    return None


async def get_gold_price() -> float:
    """Return XAU/USD spot price. Falls back to futures if spot sources fail."""
    async with aiohttp.ClientSession() as session:
        price = await _fetch_goldapi(session)
        if price:
            return price
        price = await _fetch_goldprice_org(session)
        if price:
            return price
        price = await _fetch_yf_futures(session)
        if price:
            return price
    logger.error("All gold price sources failed")
    return 0.0


# ─── Historical OHLCV (Yahoo Finance GC=F) ───────────────────────────────────

async def fetch_ohlcv(timeframe: str) -> Optional[OHLCVData]:
    params       = TF_PARAMS.get(timeframe, TF_PARAMS["H1"])
    aggregate_h4 = timeframe == "H4"
    # H4: fetch 60 days of 1h candles, then aggregate 4 per bar
    fetch_params = params if aggregate_h4 else params

    url = f"{YF_CHART}?interval={fetch_params['interval']}&range={fetch_params['range']}"

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch OHLCV and spot price concurrently
            import asyncio
            ohlcv_task = session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12))
            spot_task  = _fetch_goldapi(session)

            async with ohlcv_task as resp:
                if resp.status != 200:
                    logger.warning(f"YF returned status {resp.status} for {timeframe}")
                    return None
                raw = await resp.json(content_type=None)

            spot_price = await spot_task

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

        data = OHLCVData(opens, highs, lows, closes, volumes, spot_price or 0.0)

        if aggregate_h4:
            data = _aggregate_to_h4(data)
            if len(data) < 10:
                logger.warning("Not enough H4 candles after aggregation")
                return None

        logger.info(
            f"Fetched {len(data)} {timeframe} candles. "
            f"Spot: {data.price:.2f}  Last futures close: {closes[-1]:.2f}"
        )
        return data

    except Exception as e:
        logger.error(f"OHLCV fetch failed for {timeframe}: {e}")
        return None
