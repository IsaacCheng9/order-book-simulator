import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import orjson
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from order_book_simulator.market_data import db_consumer as db_consumer_module
from order_book_simulator.market_data.db_consumer import MarketDataDBConsumer

SCHEMA_PATH = (
    Path(__file__).parents[2]
    / "src"
    / "order_book_simulator"
    / "database"
    / "schema.sql"
)
TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture
async def postgres_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated PostgreSQL schema using the production DDL."""
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if database_url is None:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")

    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    engine = None

    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}", public')
        await connection.execute(SCHEMA_PATH.read_text())

        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": f'"{schema}", public'}},
        )
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(
            db_consumer_module,
            "AsyncSessionLocal",
            session_factory,
        )
        yield session_factory
    finally:
        if engine is not None:
            await engine.dispose()
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


@pytest.mark.asyncio
async def test_persist_batch_stores_jsonb_snapshot_and_trade(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Persist a complete market-data batch against the production schema."""
    stock_id = uuid4()
    buyer_order_id = uuid4()
    seller_order_id = uuid4()
    snapshot = orjson.loads(
        orjson.dumps(
            {
                "stock_id": str(stock_id),
                "ticker": "TEST",
                "bids": [{"price": "100.00", "quantity": "10", "order_count": 1}],
                "asks": [{"price": "101.00", "quantity": "5", "order_count": 1}],
                "trades": [
                    {
                        "price": "100.50",
                        "quantity": "5",
                        "buyer_order_id": str(buyer_order_id),
                        "seller_order_id": str(seller_order_id),
                        "timestamp": "2026-07-26T12:00:00+00:00",
                    }
                ],
            }
        )
    )

    async with postgres_session_factory() as session, session.begin():
        await session.execute(
            text("""
                    INSERT INTO stock (
                        id, ticker, company_name, min_order_size,
                        max_order_size, price_precision
                    )
                    VALUES (
                        :id, :ticker, :company_name, :min_order_size,
                        :max_order_size, :price_precision
                    )
                """),
            {
                "id": stock_id,
                "ticker": "TEST",
                "company_name": "Test Company",
                "min_order_size": 1,
                "max_order_size": 1000,
                "price_precision": 2,
            },
        )

    consumer = MarketDataDBConsumer()
    await consumer._persist_batch([snapshot])

    async with postgres_session_factory() as session:
        snapshot_result = await session.execute(
            text("""
                    SELECT bids, asks
                    FROM market_snapshot
                    WHERE stock_id = :stock_id
                """),
            {"stock_id": stock_id},
        )
        snapshot_row = snapshot_result.mappings().one()
        trade_count = (
            await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM trade
                    WHERE stock_id = :stock_id
                """),
                {"stock_id": stock_id},
            )
        ).scalar_one()

    assert snapshot_row["bids"] == snapshot["bids"]
    assert snapshot_row["asks"] == snapshot["asks"]
    assert trade_count == 1
