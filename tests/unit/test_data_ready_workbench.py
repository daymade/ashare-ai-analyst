"""Regression tests for real-data loading, source failures and unknown state."""

import asyncio
import json
import time
from threading import Event
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from src.data.ai_news_aggregator import AiNewsAggregator
from src.web.services import ai_news_service
from src.web.routes.api_v1.bootstrap import _get_regime_data

FEED = """<rss version="2.0"><channel><item><title>Published article</title>
<link>https://example.org/article</link><description>Source text</description>
<pubDate>Tue, 08 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>"""


@pytest.fixture
def news(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_news_service, "_DB_DIR", tmp_path)
    monkeypatch.setattr(ai_news_service, "_DB_PATH", tmp_path / "news.db")
    aggregator = AiNewsAggregator(
        sources=[
            {
                "id": "sample",
                "name": "Sample",
                "category": "official",
                "url": "https://example.org/feed",
            }
        ]
    )
    response = MagicMock(text=FEED)
    monkeypatch.setattr(aggregator._session, "get", MagicMock(return_value=response))
    service = ai_news_service.AiNewsService(aggregator)
    yield service, aggregator
    service._conn.close()


def test_news_deduplicates_and_persists_source_status(news):
    service, aggregator = news
    assert service.refresh("sample") == {"sample": 1}
    assert service.refresh("sample") == {"sample": 0}
    assert service.list_news()["total"] == 1
    assert (
        service.list_news()["items"][0]["published_at"] == "2026-09-08T10:00:00+00:00"
    )
    assert aggregator._session.get.call_count == 2
    assert aggregator._session.get.call_args.kwargs["timeout"] == (5.0, 20.0)
    reopened = ai_news_service.AiNewsService(
        AiNewsAggregator(sources=aggregator._sources)
    )
    try:
        status = next(
            s for s in reopened.get_source_stats() if s["source_id"] == "sample"
        )
        assert status["status"] == "ok"
        assert status["last_attempt"]
    finally:
        reopened._conn.close()


def test_news_failure_preserves_articles_and_recovers(news):
    service, aggregator = news
    service.refresh("sample")
    aggregator._cache.clear()
    aggregator._session.get.side_effect = TimeoutError("upstream timeout")
    assert service.refresh("sample") == {"sample": 0}
    status = next(s for s in service.get_source_stats() if s["source_id"] == "sample")
    assert status["status"] == "error"
    assert service.list_news()["total"] == 1
    aggregator._session.get.side_effect = None
    assert service.refresh("sample") == {"sample": 0}
    assert (
        next(s for s in service.get_source_stats() if s["source_id"] == "sample")[
            "status"
        ]
        == "ok"
    )


def test_invalid_feed_is_not_successful_empty_feed(news):
    service, aggregator = news
    aggregator._session.get.return_value.text = "not XML"
    service.refresh("sample")
    assert (
        next(s for s in service.get_source_stats() if s["source_id"] == "sample")[
            "status"
        ]
        == "error"
    )


@pytest.mark.asyncio
async def test_refresh_does_not_block_feed_reads(news):
    from src.web.dependencies import get_ai_news_service
    from src.web.routes.api_v1.ai_news import router

    service, aggregator = news
    release = Event()
    entered = Event()
    response = aggregator._session.get.return_value

    def slow_http(*args, **kwargs):
        entered.set()
        release.wait(2)
        return response

    aggregator._session.get.side_effect = slow_http
    app = FastAPI()
    app.include_router(router, prefix="/ai-news")
    app.dependency_overrides[get_ai_news_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        start = time.monotonic()
        refresh = asyncio.create_task(client.post("/ai-news/refresh?source=sample"))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.01)
            response = await client.get("/ai-news/")
            assert response.status_code == 200
            assert time.monotonic() - start < 1
        finally:
            release.set()
            await refresh


def test_missing_belief_state_does_not_invent_risk_budget():
    assert _get_regime_data(None)["risk_budget_remaining"] is None
    assert _get_regime_data(None)["hmm_probability"] is None
    redis = MagicMock()
    redis.hget.side_effect = [
        json.dumps(
            {"sentiment_phase_cn": "回暖", "updated_at": "2026-09-08T15:00:00+08:00"}
        ),
        json.dumps({"remaining_pct": 0.025}),
    ]
    actual = _get_regime_data(redis)
    assert actual["sentiment_phase_cn"] == "回暖"
    assert actual["risk_budget_remaining"] == 0.025
    assert actual["updated_at"] == "2026-09-08T15:00:00+08:00"


def test_news_missing_date_is_unknown_and_timezone_sort_is_chronological(news):
    from datetime import datetime
    from src.data.ai_news_aggregator import AiNewsItem, _parse_datetime

    assert _parse_datetime(None) is None
    assert _parse_datetime("malformed") is None
    service, _ = news
    for url, date in [
        ("older", "2026-09-08T18:00:00+08:00"),
        ("newer", "2026-09-08T13:00:00+00:00"),
        ("unknown", None),
    ]:
        service._upsert_item(
            AiNewsItem(
                title=url,
                url=f"https://example.org/{url}",
                summary="",
                source_id="sample",
                source_name="Sample",
                category="official",
                published_at=datetime.fromisoformat(date) if date else None,
            )
        )
    service._conn.commit()
    rows = service.list_news()["items"]
    assert [row["title"] for row in rows] == ["newer", "older", "unknown"]
    assert rows[-1]["published_at"] is None
