import re
import hashlib
from typing import List, Dict, Set
from collections import deque
from difflib import SequenceMatcher

class Deduplicator:
    def __init__(self, max_cache_size: int = 1000, similarity_threshold: float = 0.72):
        self.max_cache_size = max_cache_size
        self.similarity_threshold = similarity_threshold
        self.seen_url_hashes: Set[str] = set()
        # Window of recent normalized titles: [(title_normalized, title_tokens_set)]
        self.recent_articles: deque = deque(maxlen=max_cache_size)

    @staticmethod
    def hash_url(url: str) -> str:
        # Strip query parameters that change per tracking link
        clean_url = url.split("?")[0].strip().lower()
        return hashlib.sha256(clean_url.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_text(text: str) -> str:
        # Lowercase, remove special characters, remove trailing agency tags (e.g. - NDTV, | Reuters)
        text = text.lower()
        text = re.sub(r'[\-|–|—]\s*(reuters|bbc|al jazeera|ndtv|the hindu|times of india|pib).*$', '', text)
        text = re.sub(r'[^\w\s]', '', text)
        return " ".join(text.split())

    def is_duplicate(self, title: str, url: str) -> bool:
        url_h = self.hash_url(url)
        if url_h in self.seen_url_hashes:
            return True

        norm_title = self.normalize_text(title)
        if not norm_title:
            return False

        current_words = set(norm_title.split())
        if len(current_words) < 3:
            return False

        # Compare against sliding window of recent stories
        for past_norm, past_words in self.recent_articles:
            # Quick Jaccard token overlap filter first
            intersection = len(current_words.intersection(past_words))
            union = len(current_words.union(past_words))
            jaccard = intersection / union if union > 0 else 0

            if jaccard > 0.60:
                # High word overlap, do character sequence match for confirmation
                matcher = SequenceMatcher(None, norm_title, past_norm)
                if matcher.ratio() >= self.similarity_threshold:
                    return True

        return False

    def mark_seen(self, title: str, url: str):
        url_h = self.hash_url(url)
        self.seen_url_hashes.add(url_h)
        if len(self.seen_url_hashes) > self.max_cache_size * 2:
            self.seen_url_hashes.clear()

        norm_title = self.normalize_text(title)
        self.recent_articles.append((norm_title, set(norm_title.split())))
