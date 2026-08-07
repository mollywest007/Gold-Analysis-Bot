"""
Analysis result cache — keeps the latest MarketAnalysis per timeframe in memory.
Commands serve from cache (<100 ms). Background job refreshes every 3 minutes.
"""
import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_TTL = 3 * 60   # 3 minutes

_cache: Dict[Tuple[str, str], Tuple[object, float]] = {}   # (mode, tf) -> (analysis, ts)
_lock  = asyncio.Lock()


async def get_analysis(timeframe: str, max_age: int = CACHE_TTL) -> "MarketAnalysis":
    """Return cached analysis if fresh; otherwise fetch fresh and cache it."""
    from .engine import analyze

    from src.mode_manager import get_mode
    mode = get_mode()
    cache_key = (mode, timeframe)
    async with _lock:
        if cache_key in _cache:
            result, ts = _cache[cache_key]
            age = time.time() - ts
            if age < max_age:
                logger.debug(f"Cache hit [{timeframe}] — {int(age)}s old")
                return result

    result = await analyze(timeframe)

    async with _lock:
        _cache[cache_key] = (result, time.time())

    return result


async def warm(timeframes: Optional[list] = None) -> None:
    """Pre-fetch analysis for given timeframes — called once on bot startup."""
    if timeframes is None:
        timeframes = ["H1"]
    for tf in timeframes:
        try:
            logger.info(f"Warming analysis cache [{tf}]...")
            result = await get_analysis(tf, max_age=0)   # force fresh fetch
            logger.info(f"Cache warm [{tf}] done — action={result.action} conf={result.confidence}%")
        except Exception as e:
            logger.warning(f"Cache warm [{tf}] failed: {e}")


def cache_age(timeframe: str) -> Optional[int]:
    """Return seconds since last update, or None if not cached."""
    from src.mode_manager import get_mode
    entry = _cache.get((get_mode(), timeframe))
    return int(time.time() - entry[1]) if entry else None


def invalidate(timeframe: Optional[str] = None) -> None:
    """Expire cache after a trade alert fires so next fetch is fresh."""
    if timeframe:
        for key in list(_cache):
            if key[1] == timeframe:
                _cache.pop(key, None)
    else:
        _cache.clear()
