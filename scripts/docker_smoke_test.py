"""End-to-end Docker Compose smoke and crash-recovery verification script.

Exercises the advertised Compose stack against real PostgreSQL, Redis, Kafka,
Gateway (REST + WebSocket), Matching Engine, Market Data consumer, and
optionally Streamlit:

1. Verifies gateway health, Kafka health, seeded stocks, and Streamlit health.
2. Submits resting, crossing, duplicate-client-order-ID, and partial-fill
   orders through the public REST API while verifying live WebSocket bootstrap
   and delta delivery.
3. Confirms truthful order_ rows, trade rows, and live VWAP/spread endpoints.
4. Disables restart policy, hard-kills matching with SIGKILL (no SIGTERM),
   wipes Redis with FLUSHALL, re-enables restart policy, restarts matching,
   verifies exact Kafka replay of books and sequences, checks WebSocket/Pub/Sub
   continuity on a post-restart cancellation, and verifies zero duplicate
   trades in PostgreSQL.
"""

import asyncio
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiohttp
import asyncpg
import httpx
import orjson

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000").rstrip("/")
WS_URL = os.getenv("WS_URL", "ws://localhost:8000").rstrip("/")
STREAMLIT_URL = os.getenv("STREAMLIT_URL", "http://localhost:8501").rstrip("/")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://orderbook_user:orderbook_password@localhost:5432/orderbook_db",
).replace("postgresql+asyncpg://", "postgresql://", 1)
CHECK_STREAMLIT = os.getenv("CHECK_STREAMLIT", "0") == "1"
RUN_CRASH_RECOVERY = os.getenv("RUN_CRASH_RECOVERY", "1") == "1"
REPO_ROOT = Path(os.getenv("COMPOSE_PROJECT_DIR", str(Path(__file__).parents[1])))
TICKER = "AAPL"


def _run_cmd(*args: str) -> str:
    """Run a command in REPO_ROOT and return stripped stdout."""
    result = subprocess.run(
        list(args),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_compose(*args: str) -> str:
    """Run a docker compose command in REPO_ROOT and return stdout."""
    return _run_cmd("docker", "compose", *args)


def _normalise_levels(
    levels: list[dict[str, Any]],
) -> list[tuple[Decimal, Decimal, int]]:
    """Normalise order book price levels for scale-independent comparison."""
    return [
        (
            Decimal(str(lvl["price"])),
            Decimal(str(lvl["quantity"])),
            int(lvl["order_count"]),
        )
        for lvl in levels
    ]


def _normalise_book(
    book: dict[str, Any],
) -> tuple[list[tuple[Decimal, Decimal, int]], list[tuple[Decimal, Decimal, int]]]:
    """Normalise bids and asks from a gateway order book snapshot."""
    return _normalise_levels(book["bids"]), _normalise_levels(book["asks"])


async def _wait_for(
    description: str,
    predicate: Any,
    *,
    timeout_seconds: float = 60.0,
    interval_seconds: float = 0.5,
) -> Any:
    """Poll an async predicate until it returns a truthy value or times out."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = await predicate()
            if result:
                return result
        except (
            httpx.HTTPError,
            aiohttp.ClientError,
            asyncpg.PostgresError,
            OSError,
            KeyError,
            ValueError,
        ) as exc:
            last_error = exc
        await asyncio.sleep(interval_seconds)
    detail = f" (last error: {last_error})" if last_error is not None else ""
    raise TimeoutError(f"Timed out waiting for {description}{detail}")


async def _ws_receive_json(ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    """Read one JSON text frame from a WebSocket, failing clearly on close/error."""
    msg = await ws.receive()
    if msg.type == aiohttp.WSMsgType.TEXT:
        decoded = orjson.loads(msg.data)
        if not isinstance(decoded, dict):
            raise RuntimeError(f"Expected JSON object on WebSocket, got {decoded!r}")
        return decoded
    if msg.type in (
        aiohttp.WSMsgType.CLOSE,
        aiohttp.WSMsgType.CLOSING,
        aiohttp.WSMsgType.CLOSED,
    ):
        raise RuntimeError(
            f"WebSocket closed unexpectedly: code={ws.close_code}, data={msg.data!r}"
        )
    if msg.type == aiohttp.WSMsgType.ERROR:
        raise RuntimeError(f"WebSocket error: {ws.exception()!r}")
    raise RuntimeError(f"Unexpected WebSocket frame type: {msg.type!r}")


async def _submit_order(
    client: httpx.AsyncClient,
    *,
    user_id: UUID,
    side: str,
    order_type: str,
    price: str | None,
    quantity: str,
    time_in_force: str | None = "GTC",
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Submit an order via the public gateway POST endpoint."""
    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "ticker": TICKER,
        "type": order_type,
        "side": side,
        "price": price,
        "quantity": quantity,
        "time_in_force": time_in_force,
        "client_order_id": client_order_id,
    }
    response = await client.post(f"{GATEWAY_URL}/v1/order-book", json=payload)
    response.raise_for_status()
    return response.json()


async def _fetch_orders(
    conn: asyncpg.Connection, order_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    """Fetch order_ rows by ID from PostgreSQL."""
    rows = await conn.fetch(
        """
        SELECT id, user_id, type, side, status, price, quantity,
               filled_quantity, time_in_force, client_order_id
        FROM order_
        WHERE id = ANY($1::uuid[])
        """,
        order_ids,
    )
    return {row["id"]: dict(row) for row in rows}


async def _fetch_trade_ids_for_orders(
    conn: asyncpg.Connection, order_ids: list[UUID]
) -> list[UUID]:
    """Fetch all trade IDs involving any of the given order IDs."""
    rows = await conn.fetch(
        """
        SELECT id
        FROM trade
        WHERE buyer_order_id = ANY($1::uuid[])
           OR seller_order_id = ANY($1::uuid[])
        ORDER BY trade_time, id
        """,
        order_ids,
    )
    return [row["id"] for row in rows]


async def _is_gateway_healthy(client: httpx.AsyncClient) -> bool:
    """Return True when /v1/health reports healthy Kafka and Redis fan-out."""
    resp = await client.get(f"{GATEWAY_URL}/v1/health")
    if resp.status_code != 200:
        return False
    body = resp.json()
    components = body.get("components", {})
    return (
        body.get("status") == "healthy"
        and components.get("kafka_producer") == "healthy"
        and components.get("redis_fanout") == "healthy"
    )


async def run_smoke_test() -> None:
    """Execute the full Docker Compose smoke and crash-recovery suite."""
    user_id = uuid4()
    duplicate_cid = f"smoke-cid-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Verify public health and seeded stocks.
        await _wait_for(
            "gateway /v1/health == 200",
            lambda: _is_gateway_healthy(client),
            timeout_seconds=90,
        )
        kafka_resp = await client.get(f"{GATEWAY_URL}/v1/health/kafka")
        kafka_resp.raise_for_status()
        assert kafka_resp.json()["kafka_producer"] == "healthy"

        stocks_resp = await client.get(f"{GATEWAY_URL}/v1/market-data/stocks")
        stocks_resp.raise_for_status()
        tickers = {s["ticker"] for s in stocks_resp.json()["stocks"]}
        assert TICKER in tickers, f"Expected seeded ticker {TICKER} in stocks"

        if CHECK_STREAMLIT:

            async def streamlit_healthy() -> bool:
                resp = await client.get(f"{STREAMLIT_URL}/_stcore/health")
                return resp.status_code == 200

            await _wait_for(
                "streamlit /_stcore/health == 200",
                streamlit_healthy,
                timeout_seconds=60,
            )

        # 2. Submit initial resting sell order and wait for Redis book creation.
        # Safe ahead of matching readiness because OrderConsumer uses earliest.
        sell_1 = await _submit_order(
            client,
            user_id=user_id,
            side="SELL",
            order_type="LIMIT",
            price="100.00",
            quantity="10",
            time_in_force="GTC",
            client_order_id=duplicate_cid,
        )
        sell_1_id = UUID(sell_1["id"])

        async def initial_book_ready() -> dict[str, Any] | None:
            resp = await client.get(f"{GATEWAY_URL}/v1/order-book/{TICKER}")
            if resp.status_code != 200:
                return None
            body = resp.json()
            if body["book"]["asks"]:
                return body
            return None

        await _wait_for("initial AAPL order book in Redis", initial_book_ready)

        # 3. Verify WebSocket atomic snapshot bootstrap and live delta push.
        async with (
            aiohttp.ClientSession() as ws_session,
            ws_session.ws_connect(f"{WS_URL}/v1/ws/{TICKER}") as ws,
        ):
            async with asyncio.timeout(15.0):
                snapshot_msg = await _ws_receive_json(ws)
            assert snapshot_msg["type"] == "snapshot"
            assert snapshot_msg["sequence_number"] >= 1
            ws_seq = snapshot_msg["sequence_number"]

            # Submit sell #2 with the SAME user_id and client_order_id
            # (verifies D3 non-unique client_order_id policy).
            sell_2 = await _submit_order(
                client,
                user_id=user_id,
                side="SELL",
                order_type="LIMIT",
                price="100.00",
                quantity="10",
                time_in_force="GTC",
                client_order_id=duplicate_cid,
            )
            sell_2_id = UUID(sell_2["id"])

            # Expect live delta reflecting sell #2 aggregating ask 100.00 to 20.
            matched_sell_2_delta = False
            async with asyncio.timeout(15.0):
                while not matched_sell_2_delta:
                    msg = await _ws_receive_json(ws)
                    if msg.get("type") != "deltas":
                        continue
                    for delta in msg.get("data") or []:
                        if (
                            delta.get("sequence_number", 0) > ws_seq
                            and delta.get("side") == "SELL"
                            and Decimal(str(delta["price"])) == Decimal(100)
                            and Decimal(str(delta["quantity"])) == Decimal(20)
                            and int(delta["order_count"]) == 2
                        ):
                            matched_sell_2_delta = True
                            break
            assert matched_sell_2_delta

        # 4. Submit sell #3 (to cancel after restart), crossing buy, and bid.
        sell_3 = await _submit_order(
            client,
            user_id=user_id,
            side="SELL",
            order_type="LIMIT",
            price="101.00",
            quantity="5",
            time_in_force="DAY",
            client_order_id="smoke-sell-3",
        )
        sell_3_id = UUID(sell_3["id"])

        buy_1 = await _submit_order(
            client,
            user_id=user_id,
            side="BUY",
            order_type="LIMIT",
            price="100.00",
            quantity="14",
            time_in_force="IOC",
            client_order_id="smoke-buy-1",
        )
        buy_1_id = UUID(buy_1["id"])

        buy_2 = await _submit_order(
            client,
            user_id=user_id,
            side="BUY",
            order_type="LIMIT",
            price="99.50",
            quantity="8",
            time_in_force="GTC",
            client_order_id="smoke-buy-2",
        )
        buy_2_id = UUID(buy_2["id"])

        all_order_ids = [sell_1_id, sell_2_id, sell_3_id, buy_1_id, buy_2_id]
        expected_pre_crash = (
            [(Decimal("99.50"), Decimal(8), 1)],
            [(Decimal(100), Decimal(6), 1), (Decimal(101), Decimal(5), 1)],
        )

        # Wait for the expected order book state in Redis.
        async def book_matched() -> dict[str, Any] | None:
            resp = await client.get(f"{GATEWAY_URL}/v1/order-book/{TICKER}")
            if resp.status_code != 200:
                return None
            body = resp.json()
            if _normalise_book(body["book"]) == expected_pre_crash:
                return body
            return None

        pre_crash_book = await _wait_for(
            "expected pre-crash order book snapshot", book_matched
        )
        pre_crash_seq = pre_crash_book["current_delta_sequence_number"]

        # Verify PostgreSQL order_ ledger and trade persistence (scoped conn).
        conn = await asyncpg.connect(DATABASE_URL)
        try:

            async def pg_persisted() -> (
                tuple[dict[UUID, dict[str, Any]], list[UUID]] | None
            ):
                orders = await _fetch_orders(conn, all_order_ids)
                trade_ids = await _fetch_trade_ids_for_orders(conn, all_order_ids)
                if (
                    len(orders) == len(all_order_ids)
                    and len(trade_ids) == 2
                    and orders[sell_1_id]["status"] == "FILLED"
                    and orders[sell_2_id]["status"] == "PARTIALLY_FILLED"
                    and orders[buy_1_id]["status"] == "FILLED"
                ):
                    return orders, trade_ids
                return None

            orders_before, trade_ids_before = await _wait_for(
                "PostgreSQL order_ and trade persistence", pg_persisted
            )
        finally:
            await conn.close()

        assert orders_before[sell_1_id]["user_id"] == user_id
        assert orders_before[sell_1_id]["client_order_id"] == duplicate_cid
        assert Decimal(orders_before[sell_1_id]["filled_quantity"]) == Decimal(10)
        assert orders_before[sell_2_id]["client_order_id"] == duplicate_cid
        assert Decimal(orders_before[sell_2_id]["filled_quantity"]) == Decimal(4)
        assert orders_before[sell_3_id]["status"] == "PENDING"
        assert orders_before[buy_2_id]["status"] == "PENDING"

        # Poll live analytics and VWAP endpoint until async ingestion converges.
        async def market_data_converged() -> bool:
            resp = await client.get(f"{GATEWAY_URL}/v1/market-data/{TICKER}")
            if resp.status_code != 200:
                return False
            body = resp.json()
            vwap = body.get("vwap_1min")
            spread = body.get("spread")
            return (
                vwap is not None
                and spread is not None
                and Decimal(vwap) == Decimal(100)
                and Decimal(spread) == Decimal("0.50")
            )

        await _wait_for(
            "live /v1/market-data/AAPL VWAP and spread", market_data_converged
        )

        # 5. R6 crash recovery: disable auto-restart, SIGKILL matching (no
        # SIGTERM), wipe Redis, restore restart policy, restart matching.
        if RUN_CRASH_RECOVERY:
            matching_cid = _run_compose("ps", "-q", "matching")
            assert matching_cid, "Expected running matching container"
            _run_cmd("docker", "update", "--restart=no", matching_cid)
            try:
                _run_cmd("docker", "kill", "-s", "SIGKILL", matching_cid)
                _run_cmd("docker", "wait", matching_cid)
                _run_compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")

                # Confirm Redis was genuinely wiped.
                wiped_resp = await client.get(f"{GATEWAY_URL}/v1/order-book/{TICKER}")
                assert wiped_resp.status_code == 404
            finally:
                _run_cmd("docker", "update", "--restart=unless-stopped", matching_cid)

            # Restart matching; it must replay Kafka orders and restore Redis.
            _run_compose("up", "-d", "matching")

            async def book_recovered() -> dict[str, Any] | None:
                resp = await client.get(f"{GATEWAY_URL}/v1/order-book/{TICKER}")
                if resp.status_code != 200:
                    return None
                body = resp.json()
                if (
                    _normalise_book(body["book"]) == expected_pre_crash
                    and body["current_delta_sequence_number"] == pre_crash_seq
                ):
                    return body
                return None

            await _wait_for(
                "post-crash Kafka replay to restore Redis book and sequence",
                book_recovered,
                timeout_seconds=90,
            )

            # Confirm gateway Redis Pub/Sub fan-out stayed healthy after FLUSHALL.
            await _wait_for(
                "gateway /v1/health healthy after FLUSHALL",
                lambda: _is_gateway_healthy(client),
                timeout_seconds=30,
            )

            # Open WebSocket after recovery, cancel pre-crash sell #3, and verify
            # live delta delivery plus post-restart sequence continuity.
            async with (
                aiohttp.ClientSession() as ws_session,
                ws_session.ws_connect(f"{WS_URL}/v1/ws/{TICKER}") as ws,
            ):
                async with asyncio.timeout(15.0):
                    recovered_ws_snap = await _ws_receive_json(ws)
                assert recovered_ws_snap["type"] == "snapshot"
                assert recovered_ws_snap["sequence_number"] == pre_crash_seq

                cancel_resp = await client.delete(
                    f"{GATEWAY_URL}/v1/order-book/orders/{sell_3_id}",
                    params={"ticker": TICKER},
                )
                cancel_resp.raise_for_status()

                cancel_delta_seen = False
                async with asyncio.timeout(35.0):
                    while not cancel_delta_seen:
                        msg = await _ws_receive_json(ws)
                        if msg.get("type") != "deltas":
                            continue
                        for delta in msg.get("data") or []:
                            if (
                                delta.get("sequence_number") == pre_crash_seq + 1
                                and delta.get("delta_type") == "LEVEL_REMOVE"
                                and Decimal(str(delta["price"])) == Decimal(101)
                            ):
                                cancel_delta_seen = True
                                break
                assert cancel_delta_seen

            expected_post_cancel = (
                [(Decimal("99.50"), Decimal(8), 1)],
                [(Decimal(100), Decimal(6), 1)],
            )

            async def cancel_applied() -> bool:
                resp = await client.get(f"{GATEWAY_URL}/v1/order-book/{TICKER}")
                if resp.status_code != 200:
                    return False
                body = resp.json()
                return (
                    _normalise_book(body["book"]) == expected_post_cancel
                    and body["current_delta_sequence_number"] == pre_crash_seq + 1
                )

            await _wait_for(
                "post-restart cancellation of pre-crash resting order",
                cancel_applied,
            )

            # Confirm live market-data endpoint resumed after FLUSHALL.
            async def post_restart_market_data() -> bool:
                resp = await client.get(f"{GATEWAY_URL}/v1/market-data/{TICKER}")
                if resp.status_code != 200:
                    return False
                body = resp.json()
                spread = body.get("spread")
                return spread is not None and Decimal(spread) == Decimal("0.50")

            await _wait_for(
                "post-restart /v1/market-data/AAPL spread", post_restart_market_data
            )

            # Verify Postgres CANCELLED status and zero duplicate trades (fresh conn).
            conn_after = await asyncpg.connect(DATABASE_URL)
            try:

                async def cancel_persisted() -> bool:
                    orders_after = await _fetch_orders(conn_after, [sell_3_id])
                    return (
                        sell_3_id in orders_after
                        and orders_after[sell_3_id]["status"] == "CANCELLED"
                    )

                await _wait_for(
                    "PostgreSQL CANCELLED status for sell #3", cancel_persisted
                )

                trade_ids_after = await _fetch_trade_ids_for_orders(
                    conn_after, all_order_ids
                )
                assert trade_ids_after == trade_ids_before
            finally:
                await conn_after.close()

    print("Docker Compose smoke and R6 crash-recovery verification passed.")


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
