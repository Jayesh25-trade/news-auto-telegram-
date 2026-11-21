import asyncio
import logging
from typing import List, Dict, Any, Optional
import feedparser
import trafilatura
import httpx
import calendar
from datetime import datetime, timezone
import email.utils
import pytz

from config import (
    RSS_FEEDS,
    MAX_HOURS_OLD,
    MAX_ARTICLES_PER_FEED,
    TIMEZONE,
    get_google_news_rss
)
from deduplicator import Deduplicator

logger = logging.getLogger(__name__)

class NewsScraper:
    def __init__(self, deduplicator: Deduplicator):
        self.deduplicator = deduplicator
        self.ist_tz = pytz.timezone(TIMEZONE)
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def _parse_entry_pub_time(self, entry) -> Optional[datetime]:
        """Parses entry publication time and returns timezone-aware UTC datetime."""
        try:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                ts = calendar.timegm(entry.published_parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            if hasattr(entry, "published") and entry.published:
                dt = email.utils.parsedate_to_datetime(entry.published)
                if dt:
                    return dt.astimezone(timezone.utc)
        except Exception as e:
            logger.debug(f"Could not parse pub_time: {e}")
        return None

    async def fetch_feed(self, feed_meta: Dict[str, str], max_items: int = MAX_ARTICLES_PER_FEED) -> List[Dict[str, Any]]:
        """Parses a single RSS feed asynchronously with strict publication date filtering."""
        articles = []
        try:
            parsed = await asyncio.to_thread(feedparser.parse, feed_meta["url"])
            if not parsed or not parsed.entries:
                return articles

            now_utc = datetime.now(timezone.utc)

            for entry in parsed.entries[:max_items * 2]:
                if len(articles) >= max_items:
                    break

                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()

                if not title or not link:
                    continue

                # Check duplicate filter early
                if self.deduplicator.is_duplicate(title, link):
                    continue

                # Parse and validate published time (Strict 24h filter for fresh news only)
                pub_dt = self._parse_entry_pub_time(entry)
                if pub_dt:
                    age_hours = (now_utc - pub_dt).total_seconds() / 3600.0
                    # Skip if older than MAX_HOURS_OLD or from the future (>2h)
                    if age_hours > MAX_HOURS_OLD or age_hours < -2.0:
                        continue
                    pub_time_str = pub_dt.astimezone(self.ist_tz).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # If date is missing, treat as current time
                    pub_time_str = datetime.now(self.ist_tz).strftime("%Y-%m-%d %H:%M:%S")

                # Extract initial image if present
                image_url = None
                if hasattr(entry, "media_content") and entry.media_content:
                    image_url = entry.media_content[0].get("url")
                elif hasattr(entry, "enclosures") and entry.enclosures:
                    for enc in entry.enclosures:
                        if enc.get("type", "").startswith("image"):
                            image_url = enc.get("href")
                            break

                summary_text = getattr(entry, "summary", "") or getattr(entry, "description", "")

                articles.append({
                    "title": title,
                    "source_url": link,
                    "source_name": feed_meta.get("name", "News"),
                    "region_hint": feed_meta.get("region", "National"),
                    "published_at": pub_time_str,
                    "initial_summary": summary_text,
                    "image_url": image_url,
                    "url_hash": Deduplicator.hash_url(link)
                })

        except Exception as e:
            logger.warning(f"Error fetching feed {feed_meta['name']}: {e}")

        return articles

    async def fetch_topic_news(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Dynamically searches Google News RSS for a user query (e.g. 'Shegaon') within 1-3 days."""
        url = get_google_news_rss(query, time_window="when:3d")
        feed_meta = {"name": f"Search-{query}", "url": url, "region": query}
        return await self.fetch_feed(feed_meta, max_items=limit)

    async def extract_full_content_and_image(self, url: str) -> Dict[str, Optional[str]]:
        """Fetches full page content with trafilatura and finds high-res og:image."""
        try:
            downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
            if not downloaded:
                return {"full_text": "", "image_url": None}

            full_text = await asyncio.to_thread(trafilatura.extract, downloaded, include_comments=False)
            metadata = await asyncio.to_thread(trafilatura.extract_metadata, downloaded)
            
            image_url = metadata.image if metadata and metadata.image else None

            return {
                "full_text": full_text or "",
                "image_url": image_url
            }
        except Exception as e:
            logger.debug(f"Full text extraction error for {url}: {e}")
            return {"full_text": "", "image_url": None}

    async def fetch_all_feeds(self) -> List[Dict[str, Any]]:
        """Fetches all registered feeds concurrently with bounded concurrency to preserve RAM on free cloud tiers."""
        tasks = [self.fetch_feed(f) for f in RSS_FEEDS]
        chunk_size = 4
        all_results = []
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            results = await asyncio.gather(*chunk, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    all_results.extend(res)
            await asyncio.sleep(0.1)

        return all_results
