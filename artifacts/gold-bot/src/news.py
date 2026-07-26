"""
Gold news fetcher — pulls today's XAU/USD headlines from Yahoo Finance RSS.
Caches for 30 minutes so repeated /news calls don't hammer the feed.
"""

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional

import aiohttp

logger = logging.getLogger(__name__)

NEWS_RSS   = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC%3DF&region=US&lang=en-US"
BACKUP_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=XAUUSD%3DX&region=US&lang=en-US"
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search?q=gold+XAU&newsCount=8&lang=en-US&region=US"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept":     "application/json, text/plain, */*",
}

NEWS_TTL = 30 * 60   # 30 minutes

_news_cache: Tuple[List[dict], float] = ([], 0.0)
_news_lock  = asyncio.Lock()


def _parse_rss(xml_text: str) -> List[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
        ns   = {"dc": "http://purl.org/dc/elements/1.1/"}
        for item in root.iter("item"):
            title   = (item.findtext("title")       or "").strip()
            pubdate = (item.findtext("pubDate")      or "").strip()
            source  = (item.findtext("dc:creator", namespaces=ns) or
                       item.findtext("source")       or "Yahoo Finance").strip()
            if title:
                items.append({
                    "title":   title,
                    "date":    _short_date(pubdate),
                    "source":  source,
                })
            if len(items) >= 8:
                break
    except Exception as e:
        logger.debug(f"RSS parse error: {e}")
    return items


def _parse_search_json(data: dict) -> List[dict]:
    items = []
    try:
        for n in data.get("news", []):
            title     = (n.get("title") or "").strip()
            publisher = (n.get("publisher") or "").strip()
            ptime     = n.get("providerPublishTime", 0)
            date_str  = _ts_to_short(ptime) if ptime else ""
            if title:
                items.append({"title": title, "date": date_str, "source": publisher})
            if len(items) >= 8:
                break
    except Exception as e:
        logger.debug(f"Search JSON parse: {e}")
    return items


def _short_date(rfc_str: str) -> str:
    """Convert 'Thu, 25 Jun 2026 09:00:00 +0000' → 'Jun 25 09:00'."""
    try:
        parts = rfc_str.split()
        # parts: [Thu,] [25] [Jun] [2026] [09:00:00] [+0000]
        if len(parts) >= 5:
            day   = parts[1].zfill(2)
            month = parts[2]
            hhmm  = parts[4][:5]
            return f"{month} {day}  {hhmm}"
    except Exception:
        pass
    return rfc_str[:16]


def _ts_to_short(ts: int) -> str:
    import datetime
    try:
        dt = datetime.datetime.utcfromtimestamp(ts)
        return dt.strftime("%b %d  %H:%M")
    except Exception:
        return ""


async def _fetch_rss(session: aiohttp.ClientSession, url: str) -> List[dict]:
    try:
        async with session.get(url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                text = await r.text()
                return _parse_rss(text)
    except Exception as e:
        logger.debug(f"RSS fetch ({url}): {e}")
    return []


async def _fetch_search(session: aiohttp.ClientSession) -> List[dict]:
    try:
        async with session.get(SEARCH_URL, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return _parse_search_json(data)
    except Exception as e:
        logger.debug(f"Search fetch: {e}")
    return []


async def fetch_gold_news() -> List[dict]:
    """Return up to 8 gold news items. Uses 30-minute TTL cache."""
    global _news_cache
    cached_items, cached_ts = _news_cache
    if cached_items and (time.time() - cached_ts) < NEWS_TTL:
        return cached_items

    async with aiohttp.ClientSession() as session:
        # Try all three sources concurrently
        results = await asyncio.gather(
            _fetch_rss(session, NEWS_RSS),
            _fetch_rss(session, BACKUP_RSS),
            _fetch_search(session),
            return_exceptions=True,
        )

    items: List[dict] = []
    seen: set = set()
    for result in results:
        if isinstance(result, list):
            for item in result:
                key = item["title"][:40].lower()
                if key not in seen:
                    seen.add(key)
                    items.append(item)
        if len(items) >= 8:
            break

    items = items[:8]

    async with _news_lock:
        _news_cache = (items, time.time())

    logger.info(f"Gold news fetched: {len(items)} headlines")
    return items
