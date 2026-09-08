"""AI news persistence service — SQLite WAL-backed storage for aggregated AI news.

Deduplicates by URL, supports category/source filtering and pagination.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.data.ai_news_aggregator import AiNewsAggregator, AiNewsItem
from src.utils.logger import get_logger

logger = get_logger("web.services.ai_news")

_DB_DIR = Path("data")
_DB_PATH = _DB_DIR / "ai_news.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS ai_news (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    summary     TEXT DEFAULT '',
    source_id   TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category    TEXT NOT NULL,
    icon        TEXT DEFAULT '',
    tags        TEXT DEFAULT '[]',
    published_at TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    is_read     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ai_news_published ON ai_news(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_news_source ON ai_news(source_id);
CREATE INDEX IF NOT EXISTS idx_ai_news_category ON ai_news(category);
CREATE TABLE IF NOT EXISTS ai_news_sync (
    source_id TEXT PRIMARY KEY,
    last_attempt TEXT,
    status TEXT NOT NULL,
    error TEXT
);
"""


class AiNewsService:
    """Persistent AI news store with fetch + query capabilities."""

    def __init__(self, aggregator: AiNewsAggregator | None = None) -> None:
        self._aggregator = aggregator or AiNewsAggregator()
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        for stmt in _SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        self._conn.commit()

    # ── Write ────────────────────────────────────────────────────────

    def _upsert_item(self, item: AiNewsItem) -> bool:
        """Insert item if URL not already present. Returns True if inserted."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO ai_news
                   (title, url, summary, source_id, source_name, category,
                    icon, tags, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.title,
                    item.url,
                    item.summary,
                    item.source_id,
                    item.source_name,
                    item.category,
                    item.icon,
                    json.dumps(item.tags),
                    item.published_at.isoformat() if item.published_at else "",
                    now,
                ),
            )
            inserted = cursor.rowcount > 0
            if not inserted:
                # Correct dates from older parsers without resetting read state
                # or counting an existing URL as a newly discovered article.
                self._conn.execute(
                    "UPDATE ai_news SET published_at = ? WHERE url = ?",
                    (
                        item.published_at.isoformat() if item.published_at else "",
                        item.url,
                    ),
                )
            return inserted
        except sqlite3.IntegrityError:
            return False

    def refresh(self, source_id: str | None = None) -> dict[str, int]:
        """Fetch fresh items and persist. Returns {source_id: new_count}."""
        results: dict[str, int] = {}
        self._aggregator.invalidate_cache(source_id)
        if source_id:
            items = self._aggregator.fetch_source(source_id)
            new_count = 0
            for item in items:
                if self._upsert_item(item):
                    new_count += 1
            results[source_id] = new_count
        else:
            all_items = self._aggregator.fetch_all()
            by_source: dict[str, int] = {}
            for item in all_items:
                if self._upsert_item(item):
                    by_source[item.source_id] = by_source.get(item.source_id, 0) + 1
            results = by_source

        for status in self._aggregator.get_source_status():
            if status.get("last_attempt"):
                self._conn.execute(
                    "INSERT OR REPLACE INTO ai_news_sync VALUES (?, ?, ?, ?)",
                    (
                        status["id"],
                        status["last_attempt"],
                        status["status"],
                        status.get("error"),
                    ),
                )
        self._conn.commit()
        total = sum(results.values())
        logger.info(
            "AI news refresh: %d new items from %d sources", total, len(results)
        )
        return results

    # ── Read ─────────────────────────────────────────────────────────

    def list_news(
        self,
        *,
        category: str | None = None,
        source_id: str | None = None,
        search: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List news items with filters and pagination."""
        where_parts: list[str] = []
        params: list[Any] = []

        if category:
            where_parts.append("category = ?")
            params.append(category)
        if source_id:
            # Support comma-separated source IDs
            ids = [s.strip() for s in source_id.split(",")]
            placeholders = ",".join("?" * len(ids))
            where_parts.append(f"source_id IN ({placeholders})")
            params.extend(ids)
        if search:
            where_parts.append("(title LIKE ? OR summary LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if unread_only:
            where_parts.append("is_read = 0")

        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        # Count
        count_row = self._conn.execute(
            f"SELECT COUNT(*) FROM ai_news {where_clause}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        # Items
        rows = self._conn.execute(
            f"""SELECT * FROM ai_news {where_clause}
                ORDER BY julianday(published_at) DESC, id DESC
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()

        items = [self._row_to_dict(r) for r in rows]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_news(self, news_id: int) -> dict[str, Any] | None:
        """Get a single news item by ID."""
        row = self._conn.execute(
            "SELECT * FROM ai_news WHERE id = ?", (news_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def mark_read(self, news_id: int) -> bool:
        """Mark a news item as read."""
        self._conn.execute("UPDATE ai_news SET is_read = 1 WHERE id = ?", (news_id,))
        self._conn.commit()
        return True

    def get_unread_count(self) -> int:
        """Count unread news items."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ai_news WHERE is_read = 0"
        ).fetchone()
        return row[0] if row else 0

    def get_source_stats(self) -> list[dict[str, Any]]:
        """Get article count per source + aggregator circuit status."""
        rows = self._conn.execute(
            """SELECT source_id, source_name, category,
                      COUNT(*) as count,
                      MAX(published_at) as latest
               FROM ai_news
               GROUP BY source_id
               ORDER BY count DESC"""
        ).fetchall()

        source_status = {s["id"]: s for s in self._aggregator.get_source_status()}
        stats = []
        for r in rows:
            sid = r["source_id"]
            status = source_status.pop(sid, {})
            stats.append(
                {
                    "source_id": sid,
                    "source_name": r["source_name"],
                    "category": r["category"],
                    "article_count": r["count"],
                    "latest": r["latest"],
                    "icon": status.get("icon", ""),
                    "circuit_open": status.get("circuit_open", False),
                }
            )
        # Include sources with no articles yet
        for sid, status in source_status.items():
            stats.append(
                {
                    "source_id": sid,
                    "source_name": status.get("name", sid),
                    "category": status.get("category", ""),
                    "article_count": 0,
                    "latest": None,
                    "icon": status.get("icon", ""),
                    "circuit_open": status.get("circuit_open", False),
                }
            )
        sync = {
            r["source_id"]: dict(r)
            for r in self._conn.execute("SELECT * FROM ai_news_sync").fetchall()
        }
        for stat in stats:
            stat.update(sync.get(stat["source_id"], {"status": "not_fetched"}))
        return stats

    def cleanup_old(self, days: int = 30) -> int:
        """Remove items older than N days."""
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - days * 86400),
        )
        cur = self._conn.execute(
            "DELETE FROM ai_news WHERE julianday(COALESCE(NULLIF(published_at, ''), fetched_at)) < julianday(?)",
            (cutoff,),
        )
        self._conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info("Cleaned up %d old AI news items", deleted)
        return deleted

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["published_at"] = d["published_at"] or None
        if "tags" in d and isinstance(d["tags"], str):
            try:
                d["tags"] = json.loads(d["tags"])
            except json.JSONDecodeError:
                d["tags"] = []
        d["is_read"] = bool(d.get("is_read", 0))
        return d
