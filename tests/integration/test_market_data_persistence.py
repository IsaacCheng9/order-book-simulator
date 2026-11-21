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
    trade_id = uuid4()
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
                        "id": str(trade_id),
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
        trade_ids = (
            (
                await session.execute(
                    text("""
                    SELECT id
                    FROM trade
                    WHERE stock_id = :stock_id
                """),
                    {"stock_id": stock_id},
                )
            )
            .scalars()
            .all()
        )

    assert snapshot_row["bids"] == snapshot["bids"]
    assert snapshot_row["asks"] == snapshot["asks"]
    assert trade_ids == [trade_id]


@pytest.mark.asyncio
async def test_persist_batch_redelivery_does_not_duplicate_trades(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Skip duplicate trade rows when a persisted batch is redelivered."""
    stock_id = uuid4()
    trade_id = uuid4()
    buyer_order_id = uuid4()
    seller_order_id = uuid4()
    snapshot = orjson.loads(
        orjson.dumps(
            {
                "stock_id": str(stock_id),
                "ticker": "TEST",
                "bids": [],
                "asks": [],
                "trades": [
                    {
                        "id": str(trade_id),
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
    await consumer._persist_batch([snapshot])

    async with postgres_session_factory() as session:
        trade_ids = (
            (
                await session.execute(
                    text("""
                    SELECT id
                    FROM trade
                    WHERE stock_id = :stock_id
                """),
                    {"stock_id": stock_id},
                )
            )
            .scalars()
            .all()
        )

    assert trade_ids == [trade_id]


@pytest.mark.asyncio
async def test_persist_canonical_order_lifecycle_duplicate_cid_and_redelivery(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Persist real order metadata, state transitions, duplicate CIDs, and replays."""
    stock_id = uuid4()
    user_id = uuid4()
    sell_1_id = uuid4()
    sell_2_id = uuid4()
    buy_limit_id = uuid4()
    buy_market_id = uuid4()
    trade_1_id = uuid4()
    trade_2_id = uuid4()
    t0 = "2026-07-26T12:00:00+00:00"
    t1 = "2026-07-26T12:00:01+00:00"
    t2 = "2026-07-26T12:00:02+00:00"
    t3 = "2026-07-26T12:00:03+00:00"

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
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "min_order_size": 1,
                "max_order_size": 1000,
                "price_precision": 2,
            },
        )

    # Event 1: resting sell #1 (PENDING) with client_order_id="dup-cid".
    ev1 = {
        "schema_version": 1,
        "stock_id": str(stock_id),
        "ticker": "AAPL",
        "timestamp": t0,
        "bids": [],
        "asks": [{"price": "100.00", "quantity": "10", "order_count": 1}],
        "trades": [],
        "order_updates": [
            {
                "id": str(sell_1_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "PENDING",
                "price": "100.00",
                "quantity": "10",
                "filled_quantity": "0",
                "total_fee": "0",
                "time_in_force": "GTC",
                "client_order_id": "dup-cid",
                "created_at": t0,
                "updated_at": t0,
            }
        ],
    }
    # Event 2: resting sell #2 by SAME user with SAME client_order_id (allowed per D3).
    ev2 = {
        "schema_version": 1,
        "stock_id": str(stock_id),
        "ticker": "AAPL",
        "timestamp": t1,
        "bids": [],
        "asks": [
            {"price": "100.00", "quantity": "10", "order_count": 1},
            {"price": "101.00", "quantity": "5", "order_count": 1},
        ],
        "trades": [],
        "order_updates": [
            {
                "id": str(sell_2_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "PENDING",
                "price": "101.00",
                "quantity": "5",
                "filled_quantity": "0",
                "total_fee": "0",
                "time_in_force": "DAY",
                "client_order_id": "dup-cid",
                "created_at": t1,
                "updated_at": t1,
            }
        ],
    }
    # Event 3: crossing limit buy partially fills sell #1 (4 of 10).
    ev3 = {
        "schema_version": 1,
        "stock_id": str(stock_id),
        "ticker": "AAPL",
        "timestamp": t2,
        "bids": [],
        "asks": [
            {"price": "100.00", "quantity": "6", "order_count": 1},
            {"price": "101.00", "quantity": "5", "order_count": 1},
        ],
        "trades": [
            {
                "id": str(trade_1_id),
                "stock_id": str(stock_id),
                "price": "100.00",
                "quantity": "4",
                "buyer_order_id": str(buy_limit_id),
                "seller_order_id": str(sell_1_id),
                "timestamp": t2,
            }
        ],
        "order_updates": [
            {
                "id": str(sell_1_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "PARTIALLY_FILLED",
                "price": "100.00",
                "quantity": "10",
                "filled_quantity": "4",
                "total_fee": "0",
                "time_in_force": "GTC",
                "client_order_id": "dup-cid",
                "created_at": t0,
                "updated_at": t2,
            },
            {
                "id": str(buy_limit_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "BUY",
                "status": "FILLED",
                "price": "100.00",
                "quantity": "4",
                "filled_quantity": "4",
                "total_fee": "0",
                "time_in_force": "IOC",
                "client_order_id": "buy-1",
                "created_at": t2,
                "updated_at": t2,
            },
        ],
    }
    # Event 4: market buy completes sell #1, and sell #2 is cancelled.
    ev4 = {
        "schema_version": 1,
        "stock_id": str(stock_id),
        "ticker": "AAPL",
        "timestamp": t3,
        "bids": [],
        "asks": [],
        "trades": [
            {
                "id": str(trade_2_id),
                "stock_id": str(stock_id),
                "price": "100.00",
                "quantity": "6",
                "buyer_order_id": str(buy_market_id),
                "seller_order_id": str(sell_1_id),
                "timestamp": t3,
            }
        ],
        "order_updates": [
            {
                "id": str(sell_1_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "FILLED",
                "price": "100.00",
                "quantity": "10",
                "filled_quantity": "10",
                "total_fee": "0",
                "time_in_force": "GTC",
                "client_order_id": "dup-cid",
                "created_at": t0,
                "updated_at": t3,
            },
            {
                "id": str(buy_market_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "MARKET",
                "side": "BUY",
                "status": "FILLED",
                "price": None,
                "quantity": "6",
                "filled_quantity": "6",
                "total_fee": "0",
                "time_in_force": None,
                "client_order_id": None,
                "created_at": t3,
                "updated_at": t3,
            },
            {
                "id": str(sell_2_id),
                "user_id": str(user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "CANCELLED",
                "price": "101.00",
                "quantity": "5",
                "filled_quantity": "0",
                "total_fee": "0",
                "time_in_force": "DAY",
                "client_order_id": "dup-cid",
                "created_at": t1,
                "updated_at": t3,
            },
        ],
    }

    consumer = MarketDataDBConsumer()
    await consumer._persist_batch([ev1, ev2, ev3, ev4])
    # Redeliver earlier events to prove monotonic status/fill progression.
    await consumer._persist_batch([ev1, ev3, ev4])

    async with postgres_session_factory() as session:
        rows = (
            (
                await session.execute(
                    text("""
                        SELECT id, user_id, type, side, status, price, quantity,
                               filled_quantity, time_in_force, client_order_id,
                               created_at, updated_at
                        FROM order_
                        WHERE stock_id = :stock_id
                    """),
                    {"stock_id": stock_id},
                )
            )
            .mappings()
            .all()
        )
        trade_ids = (
            (
                await session.execute(
                    text("SELECT id FROM trade WHERE stock_id = :stock_id"),
                    {"stock_id": stock_id},
                )
            )
            .scalars()
            .all()
        )

    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {sell_1_id, sell_2_id, buy_limit_id, buy_market_id}
    assert by_id[sell_1_id]["status"] == "FILLED"
    assert by_id[sell_1_id]["filled_quantity"] == 10
    assert by_id[sell_1_id]["user_id"] == user_id
    assert by_id[sell_1_id]["time_in_force"] == "GTC"
    assert by_id[sell_1_id]["client_order_id"] == "dup-cid"
    assert by_id[sell_1_id]["updated_at"].isoformat() == t3
    assert by_id[sell_2_id]["status"] == "CANCELLED"
    assert by_id[sell_2_id]["filled_quantity"] == 0
    assert by_id[sell_2_id]["client_order_id"] == "dup-cid"
    assert by_id[sell_2_id]["updated_at"].isoformat() == t3
    assert by_id[buy_market_id]["type"] == "MARKET"
    assert by_id[buy_market_id]["price"] is None
    assert by_id[buy_market_id]["status"] == "FILLED"
    assert set(trade_ids) == {trade_1_id, trade_2_id}
    assert len(trade_ids) == 2


@pytest.mark.asyncio
async def test_persist_order_repairs_legacy_stub_and_prefers_filled_over_cancelled(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Repair fallback stub metadata on canonical upsert and preserve FILLED."""
    stock_id = uuid4()
    real_user_id = uuid4()
    buyer_id = uuid4()
    seller_id = uuid4()
    trade_id = uuid4()
    t0 = "2026-07-26T12:00:00+00:00"
    t1 = "2026-07-26T12:00:01+00:00"

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
                "ticker": "MSFT",
                "company_name": "Microsoft",
                "min_order_size": 1,
                "max_order_size": 1000,
                "price_precision": 2,
            },
        )

    consumer = MarketDataDBConsumer()
    # First persist a legacy trade event without order_updates (inserts stub).
    legacy_event = {
        "stock_id": str(stock_id),
        "ticker": "MSFT",
        "bids": [],
        "asks": [],
        "trades": [
            {
                "id": str(trade_id),
                "price": "200.00",
                "quantity": "4",
                "buyer_order_id": str(buyer_id),
                "seller_order_id": str(seller_id),
                "timestamp": t0,
            }
        ],
    }
    await consumer._persist_batch([legacy_event])

    # Next arrive canonical updates: CANCELLED followed by FILLED (proves repair
    # of stub metadata and FILLED > CANCELLED precedence).
    canonical_cancelled = {
        "schema_version": 1,
        "stock_id": str(stock_id),
        "ticker": "MSFT",
        "timestamp": t0,
        "bids": [],
        "asks": [],
        "trades": [],
        "order_updates": [
            {
                "id": str(seller_id),
                "user_id": str(real_user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "CANCELLED",
                "price": "200.00",
                "quantity": "10",
                "filled_quantity": "4",
                "total_fee": "0",
                "time_in_force": "GTC",
                "client_order_id": "repaired-cid",
                "created_at": t0,
                "updated_at": t0,
            }
        ],
    }
    canonical_filled = {
        "schema_version": 1,
        "stock_id": str(stock_id),
        "ticker": "MSFT",
        "timestamp": t1,
        "bids": [],
        "asks": [],
        "trades": [],
        "order_updates": [
            {
                "id": str(seller_id),
                "user_id": str(real_user_id),
                "stock_id": str(stock_id),
                "type": "LIMIT",
                "side": "SELL",
                "status": "FILLED",
                "price": "200.00",
                "quantity": "10",
                "filled_quantity": "10",
                "total_fee": "0",
                "time_in_force": "GTC",
                "client_order_id": "repaired-cid",
                "created_at": t0,
                "updated_at": t1,
            }
        ],
    }
    await consumer._persist_batch([canonical_cancelled, canonical_filled])

    async with postgres_session_factory() as session:
        row = (
            (
                await session.execute(
                    text("""
                    SELECT user_id, quantity, filled_quantity, status,
                           time_in_force, client_order_id, updated_at
                    FROM order_
                    WHERE id = :id
                """),
                    {"id": seller_id},
                )
            )
            .mappings()
            .one()
        )

    assert row["user_id"] == real_user_id
    assert row["quantity"] == 10
    assert row["filled_quantity"] == 10
    assert row["status"] == "FILLED"
    assert row["time_in_force"] == "GTC"
    assert row["client_order_id"] == "repaired-cid"
    assert row["updated_at"].isoformat() == t1
