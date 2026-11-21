import asyncio
import logging
from typing import List, Dict, Any, Optional
import feedparser
import trafilatura
import httpx
from datetime import datetime
import email.utils

from config import RSS_FEEDS
from deduplicator import Deduplicator

logger = logging.getLogger(__name__)

class NewsScraper:
    def __init__(self, deduplicator: Deduplicator):
        self.deduplicator = deduplicator
        self.client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    async def fetch_feed(self, feed_meta: Dict[str, str]) -> List[Dict[str, Any]]:
        """Parses a single RSS feed asynchronously."""
        articles = []
        try:
            # Run feedparser in threadpool to avoid blocking event loop
            parsed = await asyncio.to_thread(feedparser.parse, feed_meta["url"])
            if not parsed or not parsed.entries:
                return articles

            for entry in parsed.entries[:10]: # Process latest 10 items per cycle
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()

                if not title or not link:
                    continue

                # Check duplicate filter early
                if self.deduplicator.is_duplicate(title, link):
                    continue

                # Parse published time
                pub_time = datetime.now()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub_time = datetime(*entry.published_parsed[:6])
                    except Exception:
                        pass
                elif hasattr(entry, "published") and entry.published:
                    try:
                        parsed_tuple = email.utils.parsedate(entry.published)
                        if parsed_tuple:
                            pub_time = datetime(*parsed_tuple[:6])
                    except Exception:
                        pass

                # Extract initial image if in RSS enclosure
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
                    "published_at": pub_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "initial_summary": summary_text,
                    "image_url": image_url,
                    "url_hash": Deduplicator.hash_url(link)
                })

        except Exception as e:
            logger.warning(f"Error fetching feed {feed_meta['name']}: {e}")

        return articles

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
        # Low concurrency chunk size of 3 prevents OOM on Render 512MB tier
        chunk_size = 3
        all_results = []
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            results = await asyncio.gather(*chunk, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    all_results.extend(res)
            # Brief pause to yield event loop and release memory
            await asyncio.sleep(0.2)

        return all_results
