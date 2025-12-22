import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from order_book_simulator.market_data.db_consumer import MarketDataDBConsumer


def create_market_data(stock_id: str | None = None) -> dict:
    """Creates test market data."""
    return {
        "stock_id": stock_id or str(uuid4()),
        "ticker": "TEST",
        "bids": [{"price": "100.00", "quantity": "10", "order_count": 1}],
        "asks": [{"price": "101.00", "quantity": "5", "order_count": 1}],
        "trades": [],
    }


@pytest.fixture
def consumer() -> MarketDataDBConsumer:
    """Creates a consumer with small batch size for testing."""
    return MarketDataDBConsumer(batch_size=3, batch_timeout_ms=100)


@pytest.mark.asyncio
async def test_flush_batch_empty_does_nothing(consumer: MarketDataDBConsumer):
    """Flushing an empty batch should not interact with the database."""
    with patch(
        "order_book_simulator.market_data.db_consumer.AsyncSessionLocal"
    ) as mock_session:
        await consumer._flush_batch()
        mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_flush_batch_persists_market_data(consumer: MarketDataDBConsumer):
    """Flushing should persist each item in the batch."""
    stock_id = str(uuid4())
    consumer.batch = [create_market_data(stock_id), create_market_data(stock_id)]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock()

    with (
        patch(
            "order_book_simulator.market_data.db_consumer.AsyncSessionLocal",
            return_value=mock_session,
        ),
        patch(
            "order_book_simulator.market_data.db_consumer.persist_market_snapshot"
        ) as mock_persist_snapshot,
        patch(
            "order_book_simulator.market_data.db_consumer.persist_trades"
        ) as mock_persist_trades,
    ):
        await consumer._flush_batch()

        assert mock_persist_snapshot.call_count == 2
        # No trades in test data, so persist_trades should not be called.
        mock_persist_trades.assert_not_called()


@pytest.mark.asyncio
async def test_flush_batch_persists_trades_when_present(consumer: MarketDataDBConsumer):
    """Flushing should persist trades when they exist."""
    stock_id = str(uuid4())
    data = create_market_data(stock_id)
    data["trades"] = [
        {
            "price": "100.50",
            "quantity": "5",
            "buyer_order_id": str(uuid4()),
            "seller_order_id": str(uuid4()),
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
    ]
    consumer.batch = [data]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock()

    with (
        patch(
            "order_book_simulator.market_data.db_consumer.AsyncSessionLocal",
            return_value=mock_session,
        ),
        patch(
            "order_book_simulator.market_data.db_consumer.persist_market_snapshot"
        ) as mock_persist_snapshot,
        patch(
            "order_book_simulator.market_data.db_consumer.persist_trades"
        ) as mock_persist_trades,
    ):
        await consumer._flush_batch()

        mock_persist_snapshot.assert_called_once()
        mock_persist_trades.assert_called_once()


@pytest.mark.asyncio
async def test_flush_batch_clears_batch(consumer: MarketDataDBConsumer):
    """Flushing should clear the batch."""
    consumer.batch = [create_market_data(), create_market_data()]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock()

    with (
        patch(
            "order_book_simulator.market_data.db_consumer.AsyncSessionLocal",
            return_value=mock_session,
        ),
        patch("order_book_simulator.market_data.db_consumer.persist_market_snapshot"),
        patch("order_book_simulator.market_data.db_consumer.persist_trades"),
    ):
        await consumer._flush_batch()

        assert len(consumer.batch) == 0


@pytest.mark.asyncio
async def test_flush_batch_updates_last_flush_time(consumer: MarketDataDBConsumer):
    """Flushing should update last_flush timestamp."""
    consumer.batch = [create_market_data()]
    consumer.last_flush = 0  # Set to epoch.

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock()

    with (
        patch(
            "order_book_simulator.market_data.db_consumer.AsyncSessionLocal",
            return_value=mock_session,
        ),
        patch("order_book_simulator.market_data.db_consumer.persist_market_snapshot"),
        patch("order_book_simulator.market_data.db_consumer.persist_trades"),
    ):
        before = time.time()
        await consumer._flush_batch()
        after = time.time()

        assert consumer.last_flush >= before
        assert consumer.last_flush <= after


@pytest.mark.asyncio
async def test_flush_batch_handles_errors_gracefully(consumer: MarketDataDBConsumer):
    """Flushing should handle errors without raising."""
    consumer.batch = [create_market_data()]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin.return_value.__aenter__ = AsyncMock()
    mock_session.begin.return_value.__aexit__ = AsyncMock()

    with (
        patch(
            "order_book_simulator.market_data.db_consumer.AsyncSessionLocal",
            return_value=mock_session,
        ),
        patch(
            "order_book_simulator.market_data.db_consumer.persist_market_snapshot",
            side_effect=Exception("DB error"),
        ),
    ):
        # Should not raise.
        await consumer._flush_batch()
        # Batch should still be cleared.
        assert len(consumer.batch) == 0


def test_batch_size_threshold(consumer: MarketDataDBConsumer):
    """Consumer should identify when batch size threshold is reached."""
    consumer.batch = [create_market_data() for _ in range(2)]
    consumer.last_flush = time.time()

    # Below threshold.
    should_flush = len(consumer.batch) >= consumer.batch_size
    assert not should_flush

    # At threshold.
    consumer.batch.append(create_market_data())
    should_flush = len(consumer.batch) >= consumer.batch_size
    assert should_flush


def test_batch_timeout_threshold(consumer: MarketDataDBConsumer):
    """Consumer should identify when batch timeout is reached."""
    consumer.batch = [create_market_data()]
    consumer.last_flush = time.time()

    # Just flushed, no timeout.
    should_flush = time.time() - consumer.last_flush >= consumer.batch_timeout
    assert not should_flush

    # Simulate timeout.
    consumer.last_flush = time.time() - consumer.batch_timeout - 0.01
    should_flush = time.time() - consumer.last_flush >= consumer.batch_timeout
    assert should_flush
