import aiosqlite
import os
import json
import re
from typing import List, Dict, Optional, Any
from datetime import datetime

class Database:
    def __init__(self, db_path: str = "data/intelligence_bot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def init_db(self):
        """Creates tables, indices, and Full-Text-Search (FTS5) virtual tables."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")

            # 1. News Articles Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS news_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url_hash TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    published_at TIMESTAMP,
                    state TEXT,
                    category TEXT,
                    importance_score INTEGER DEFAULT 3,
                    is_breaking INTEGER DEFAULT 0,
                    summary_hinglish TEXT NOT NULL,
                    full_content TEXT,
                    image_url TEXT,
                    dedup_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 2. Full-Text Search (FTS5) for RAG semantic/keyword retrieval
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(
                    title,
                    summary_hinglish,
                    full_content,
                    state,
                    category,
                    content='news_articles',
                    content_rowid='id'
                );
            """)

            # Triggers to keep FTS table in sync
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS news_ai AFTER INSERT ON news_articles BEGIN
                    INSERT INTO news_fts(rowid, title, summary_hinglish, full_content, state, category)
                    VALUES (new.id, new.title, new.summary_hinglish, new.full_content, new.state, new.category);
                END;
            """)

            # 3. User Preferences (Settings, Quiet Hours, Digest Mode)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    min_importance INTEGER DEFAULT 2,
                    quiet_hours_enabled INTEGER DEFAULT 1,
                    quiet_hours_start TEXT DEFAULT '23:00',
                    quiet_hours_end TEXT DEFAULT '07:00',
                    digest_mode INTEGER DEFAULT 0,
                    subscribed_states TEXT DEFAULT 'ALL',
                    muted_categories TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 4. Digest Queue Table (holds small news during digest or quiet hours)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS digest_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    article_id INTEGER NOT NULL,
                    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(article_id) REFERENCES news_articles(id)
                );
            """)

            # 5. Market Watchlist & Price Alerts
            await db.execute("""
                CREATE TABLE IF NOT EXISTS market_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    condition TEXT NOT NULL, -- 'ABOVE' or 'BELOW'
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    UNIQUE(user_id, symbol)
                );
            """)

            await db.commit()

    async def article_exists(self, url_hash: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT 1 FROM news_articles WHERE url_hash = ?", (url_hash,)) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def insert_article(self, article: Dict[str, Any]) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT OR IGNORE INTO news_articles (
                    url_hash, title, source_url, source_name, published_at,
                    state, category, importance_score, is_breaking,
                    summary_hinglish, full_content, image_url, dedup_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                article["url_hash"], article["title"], article["source_url"], article["source_name"],
                article.get("published_at"), article.get("state", "National"), article.get("category", "General"),
                article.get("importance_score", 3), 1 if article.get("is_breaking") else 0,
                article["summary_hinglish"], article.get("full_content", ""), article.get("image_url"),
                article.get("dedup_hash", "")
            ))
            await db.commit()
            return cursor.lastrowid or 0

    async def get_article_by_id(self, article_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM news_articles WHERE id = ?", (article_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_recent_articles(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM news_articles ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def search_articles_fts(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches articles using FTS5 with fallback to LIKE query."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            tokens = [w for w in re.sub(r'[^\w\s]', '', query).split() if len(w) >= 2]
            if tokens:
                fts_query = " OR ".join([f'"{t}"' for t in tokens])
            else:
                clean_q = query.replace('"', '""')
                fts_query = f'"{clean_q}"'

            sql = """
                SELECT a.* FROM news_articles a
                JOIN news_fts f ON a.id = f.rowid
                WHERE news_fts MATCH ?
                ORDER BY a.id DESC LIMIT ?
            """
            try:
                async with db.execute(sql, (fts_query, limit)) as cursor:
                    rows = await cursor.fetchall()
                    if rows:
                        return [dict(r) for r in rows]
            except Exception:
                pass

            # Fallback to LIKE if FTS returns nothing or fails
            fallback_sql = """
                SELECT * FROM news_articles
                WHERE title LIKE ? OR summary_hinglish LIKE ? OR state LIKE ?
                ORDER BY id DESC LIMIT ?
            """
            term = f"%{query}%"
            async with db.execute(fallback_sql, (term, term, term, limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_user_preferences(self, user_id: int) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                # Defaults
                await db.execute("INSERT OR IGNORE INTO user_preferences (user_id) VALUES (?)", (user_id,))
                await db.commit()
                return {
                    "user_id": user_id,
                    "min_importance": 2,
                    "quiet_hours_enabled": 1,
                    "quiet_hours_start": "23:00",
                    "quiet_hours_end": "07:00",
                    "digest_mode": 0,
                    "subscribed_states": "ALL",
                    "muted_categories": ""
                }

    async def update_user_preferences(self, user_id: int, updates: Dict[str, Any]):
        async with aiosqlite.connect(self.db_path) as db:
            fields = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [user_id]
            await db.execute(f"UPDATE user_preferences SET {fields} WHERE user_id = ?", values)
            await db.commit()

    async def queue_for_digest(self, user_id: int, article_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO digest_queue (user_id, article_id) VALUES (?, ?)", (user_id, article_id))
            await db.commit()

    async def pop_digest_articles(self, user_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT a.* FROM news_articles a
                JOIN digest_queue q ON a.id = q.article_id
                WHERE q.user_id = ?
                ORDER BY a.id ASC
            """, (user_id,)) as cursor:
                rows = await cursor.fetchall()
            await db.execute("DELETE FROM digest_queue WHERE user_id = ?", (user_id,))
            await db.commit()
            return [dict(r) for r in rows]

    async def get_active_market_alerts(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM market_alerts WHERE is_active = 1") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def add_market_alert(self, user_id: int, symbol: str, target: float, condition: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO market_alerts (user_id, symbol, target_price, condition) VALUES (?, ?, ?, ?)",
                (user_id, symbol.upper(), target, condition.upper())
            )
            await db.commit()

    async def mark_alert_triggered(self, alert_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE market_alerts SET is_active = 0 WHERE id = ?", (alert_id,))
            await db.commit()

    async def get_watchlist(self, user_id: int) -> List[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT symbol FROM watchlist WHERE user_id = ?", (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def add_to_watchlist(self, user_id: int, symbol: str, name: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR IGNORE INTO watchlist (user_id, symbol, name) VALUES (?, ?, ?)",
                             (user_id, symbol.upper(), name))
            await db.commit()
