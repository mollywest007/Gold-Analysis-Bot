import logging
from typing import Optional, Dict, List
import aiohttp

logger = logging.getLogger(__name__)

YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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
    def __init__(self, opens, highs, lows, closes, volumes):
        self.opens = opens
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.volumes = volumes
        self.price = closes[-1] if closes else 0.0

    def __len__(self):
        return len(self.closes)


def _clean(series: list) -> list:
    return [x for x in series if x is not None]


def _aggregate_to_h4(data: OHLCVData) -> OHLCVData:
    opens, highs, lows, closes, volumes = [], [], [], [], []
    step = 4
    n = len(data.closes)
    for i in range(0, n - step + 1, step):
        opens.append(data.opens[i])
        highs.append(max(data.highs[i:i+step]))
        lows.append(min(data.lows[i:i+step]))
        closes.append(data.closes[i+step-1])
        volumes.append(sum(v for v in data.volumes[i:i+step] if v))
    return OHLCVData(opens, highs, lows, closes, volumes)


async def fetch_ohlcv(timeframe: str) -> Optional[OHLCVData]:
    params = TF_PARAMS.get(timeframe, TF_PARAMS["H1"])
    aggregate_h4 = timeframe == "H4"
    fetch_params = TF_PARAMS["H1"] if aggregate_h4 else params

    url = f"{YF_BASE}?interval={fetch_params['interval']}&range={fetch_params['range']}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"YF returned status {resp.status} for {timeframe}")
                    return None
                raw = await resp.json()

        result = raw["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]

        opens  = _clean(quote.get("open",  []))
        highs  = _clean(quote.get("high",  []))
        lows   = _clean(quote.get("low",   []))
        closes = _clean(quote.get("close", []))
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

        data = OHLCVData(opens, highs, lows, closes, volumes)

        if aggregate_h4:
            data = _aggregate_to_h4(data)
            if len(data) < 10:
                logger.warning("Not enough H4 candles after aggregation")
                return None

        logger.info(f"Fetched {len(data)} {timeframe} candles. Price: {data.price:.2f}")
        return data

    except Exception as e:
        logger.error(f"OHLCV fetch failed for {timeframe}: {e}")
        return None


async def get_gold_price() -> float:
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{YF_BASE}?interval=1m&range=1d"
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    raw = await resp.json()
                    price = raw["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    if price and 500 < float(price) < 20000:
                        return float(price)
    except Exception as e:
        logger.warning(f"Price fetch failed: {e}")
    return 0.0
