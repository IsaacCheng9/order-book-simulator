from typing import Any
from uuid import uuid4

import pytest

from order_book_simulator.common.cache import OrderBookCache, order_book_cache


@pytest.mark.asyncio
async def test_get_all_order_books_uses_scan_iter_instead_of_keys(
    monkeypatch: pytest.MonkeyPatch,
):
    """Iterate order book keys with SCAN rather than blocking KEYS."""
    stock_a = uuid4()
    stock_b = uuid4()
    await order_book_cache.set_order_book(stock_a, {"bids": [], "asks": []})
    await order_book_cache.set_order_book(stock_b, {"bids": [], "asks": []})
    await order_book_cache.redis.set(f"delta_seq:{stock_a}", 3)

    async def fail_on_keys(pattern: str) -> list[str]:
        raise AssertionError(f"Unexpected blocking KEYS call: {pattern}")

    monkeypatch.setattr(order_book_cache.redis, "keys", fail_on_keys)

    books = await order_book_cache.get_all_order_books()
    assert set(books.keys()) == {str(stock_a), str(stock_b)}


def test_order_book_cache_reads_redis_url_from_env(
    monkeypatch: pytest.MonkeyPatch,
):
    """Resolve REDIS_URL lazily when no explicit URL is passed."""
    captured_urls: list[str] = []

    def fake_from_url(url: str, **kwargs: Any) -> Any:
        del kwargs
        captured_urls.append(url)
        return object()

    monkeypatch.setenv("REDIS_URL", "redis://custom-redis:6380/2")
    monkeypatch.setattr(
        "order_book_simulator.common.cache.Redis.from_url", fake_from_url
    )

    cache = OrderBookCache()
    _ = cache.redis
    assert captured_urls == ["redis://custom-redis:6380/2"]
