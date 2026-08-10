"""Strict gold-market news fetcher.

Yahoo's symbol RSS and broad search endpoint have both returned unrelated
equity headlines in practice, so they are not safe to label as gold news.
Google News RSS is used with gold-specific queries, then every result is
filtered again before it reaches Telegram.
"""

import asyncio
import datetime
import email.utils
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import List, Tuple

import aiohttp

logger = logging.getLogger(__name__)

NEWS_RSS_URLS = (
    "https://news.google.com/rss/search?q=gold%20XAU%20USD%20when%3A2d&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold%20price%20bullion%20when%3A2d&hl=en-US&gl=US&ceid=US:en",
)

HEADERS = {
    "User-Agent": "GoldAnalysisBot/1.0",
    "Accept": "application/rss+xml, application/xml, text/xml",
}

NEWS_TTL = 15 * 60   # Keep headlines reasonably fresh.
NEWS_MAX_AGE = 2 * 24 * 60 * 60
TRUSTED_SOURCE_TERMS = (
    "reuters", "bloomberg", "cnbc", "financial times",
    "wall street journal", "marketwatch", "kitco", "fxstreet",
    "fxleaders", "forex.com", "investing.com", "dailyfx",
    "associated press",
)

_news_cache: Tuple[List[dict], float] = ([], 0.0)
_news_lock  = asyncio.Lock()


def _is_gold_relevant(title: str) -> bool:
    """Reject generic finance stories even if a search provider returns them."""
    normalized = " ".join(title.lower().replace("/", " ").split())
    return bool(re.search(
        r"\b золотото \b|\bgold\b|\bxau\b|\bbullion\b|"
        r"\bprecious metals?\b|\bcomex\b|\bgold futures?\b|"
        r"\bgold price\b",
        normalized,
    ))


def _is_trusted_source(source: str) -> bool:
    """Prefer established financial publishers over user-generated posts."""
    normalized = source.casefold().strip()
    return any(term in normalized for term in TRUSTED_SOURCE_TERMS)


def _is_news_story(title: str) -> bool:
    """Reject quote/chart pages that are not reported news stories."""
    normalized = title.casefold()
    excluded_terms = (
        "streaming chart", "live price chart", "chart image",
        "technical analysis", "weekly analysis",
    )
    return not any(term in normalized for term in excluded_terms)


def _published_timestamp(value: str) -> float:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _parse_rss(xml_text: str) -> List[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title   = (item.findtext("title")       or "").strip()
            pubdate = (item.findtext("pubDate")      or "").strip()
            source  = (item.findtext("source") or "Unknown source").strip()
            link    = (item.findtext("link") or "").strip()
            published_at = _published_timestamp(pubdate)
            if (
                title
                and link
                and _is_gold_relevant(title)
                and _is_trusted_source(source)
                and _is_news_story(title)
            ):
                items.append({
                    "title":   title,
                    "date":    _short_date(pubdate),
                    "source":  source,
                    "url":     link,
                    "published_at": published_at,
                })
            if len(items) >= 20:
                break
    except Exception as e:
        logger.debug(f"RSS parse error: {e}")
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


async def fetch_gold_news() -> List[dict]:
    """Return up to 8 recent, filtered gold-market headlines."""
    global _news_cache
    cached_items, cached_ts = _news_cache
    if cached_items and (time.time() - cached_ts) < NEWS_TTL:
        return cached_items

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[_fetch_rss(session, url) for url in NEWS_RSS_URLS],
            return_exceptions=True,
        )

    cutoff = time.time() - NEWS_MAX_AGE
    items: List[dict] = []
    seen: set = set()
    for result in results:
        if not isinstance(result, list):
            continue
        for item in result:
            published_at = item.get("published_at", 0.0)
            if published_at and published_at < cutoff:
                continue
            key = item["title"].casefold().strip()
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= 8:
                break
        if len(items) >= 8:
            break

    # Newest first, while keeping undated items at the end.
    items.sort(key=lambda item: item.get("published_at", 0.0), reverse=True)
    items = items[:8]

    async with _news_lock:
        _news_cache = (items, time.time())

    logger.info(f"Gold news fetched: {len(items)} filtered headlines")
    return items
