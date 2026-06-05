import aiohttp
import random
import math
import time
from typing import Optional

BASE_PRICE = 3350.0

async def fetch_gold_price() -> Optional[float]:
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    return float(price)
    except Exception:
        pass
    return None


def get_simulated_price() -> float:
    t = time.time()
    noise = math.sin(t / 300) * 15 + math.sin(t / 900) * 8 + random.uniform(-3, 3)
    return round(BASE_PRICE + noise, 2)


async def get_gold_price() -> float:
    live = await fetch_gold_price()
    if live and 1000 < live < 5000:
        return live
    return get_simulated_price()
