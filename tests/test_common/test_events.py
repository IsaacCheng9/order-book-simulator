from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from order_book_simulator.common.events import (
    EVENT_SCHEMA_VERSION,
    EventValidationError,
    validate_market_data_event,
    validate_schema_version,
)


def _valid_event(stock_id: str | None = None) -> dict[str, Any]:
    """Build a valid canonical v1 market-data event."""
    resolved_stock_id = stock_id or str(uuid4())
    now = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC).isoformat()
    order_id = str(uuid4())
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "stock_id": resolved_stock_id,
        "ticker": "AAPL",
        "timestamp": now,
        "sequence_number": 1,
        "bids": [{"price": "100.00", "quantity": "10", "order_count": 1}],
        "asks": [],
        "trades": [],
        "order_updates": [
            {
                "id": order_id,
                "user_id": str(uuid4()),
                "stock_id": resolved_stock_id,
                "type": "LIMIT",
                "side": "BUY",
                "status": "PENDING",
                "price": "100.00",
                "quantity": "10",
                "filled_quantity": "0",
                "total_fee": "0",
                "time_in_force": "GTC",
                "client_order_id": "cid-1",
                "created_at": now,
                "updated_at": now,
            }
        ],
    }


def test_validate_schema_version_accepts_v1_and_omitted():
    """Accept schema_version=1 and legacy events omitting schema_version."""
    assert validate_schema_version({}) == 1
    assert validate_schema_version({"schema_version": 1}) == 1


@pytest.mark.parametrize("bad_version", [0, 2, "1", True, False, 1.0])
def test_validate_schema_version_rejects_invalid_values(bad_version: Any):
    """Reject unsupported or non-integer schema versions."""
    with pytest.raises(EventValidationError):
        validate_schema_version({"schema_version": bad_version})


def test_validate_market_data_event_accepts_valid_v1_and_market_order():
    """Accept valid limit and market order updates plus valid trades."""
    stock_id = str(uuid4())
    now = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC).isoformat()
    event = _valid_event(stock_id)
    buyer_id = str(uuid4())
    seller_id = event["order_updates"][0]["id"]
    event["order_updates"][0]["status"] = "FILLED"
    event["order_updates"][0]["filled_quantity"] = "10"
    event["order_updates"].append(
        {
            "id": buyer_id,
            "user_id": str(uuid4()),
            "stock_id": stock_id,
            "type": "MARKET",
            "side": "BUY",
            "status": "FILLED",
            "price": None,
            "quantity": "10",
            "filled_quantity": "10",
            "total_fee": "0",
            "time_in_force": None,
            "client_order_id": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    event["trades"] = [
        {
            "id": str(uuid4()),
            "stock_id": stock_id,
            "price": "100.00",
            "quantity": "10",
            "buyer_order_id": buyer_id,
            "seller_order_id": seller_id,
            "timestamp": now,
        }
    ]
    assert validate_market_data_event(event) is event


@pytest.mark.parametrize(
    "mutator",
    [
        lambda e: e.update({"schema_version": 99}),
        lambda e: e.update({"stock_id": "not-a-uuid"}),
        lambda e: e.update({"ticker": ""}),
        lambda e: e.update({"ticker": "X" * 21}),
        lambda e: e.update({"timestamp": "2026-07-26T12:00:00"}),
        lambda e: e.update({"sequence_number": True}),
        lambda e: e.pop("bids"),
        lambda e: e.update({"bids": "not-a-list"}),
        lambda e: e.update({"bids": [{"price": "NaN", "quantity": "1"}]}),
        lambda e: e.update({"bids": [{"price": "1E+30", "quantity": "1"}]}),
        lambda e: e.update({"bids": [{"price": "1000000000000", "quantity": "1"}]}),
        lambda e: e.update(
            {"bids": [{"price": "999999999999.999999995", "quantity": "1"}]}
        ),
        lambda e: e.update({"bids": [{"price": "0.000000001", "quantity": "1"}]}),
        lambda e: e.update({"bids": [{"price": "100", "quantity": "-0"}]}),
        lambda e: e.update({"bids": [{"price": "100", "quantity": "0e-20000"}]}),
        lambda e: e.update({"bids": [{"price": "100", "quantity": "0E+999999999999"}]}),
        lambda e: e.update({"ticker": "AA\x00PL"}),
        lambda e: e["order_updates"][0].update({"time_in_force": "GT\x00C"}),
        lambda e: e["order_updates"][0].update({"client_order_id": "cid\x001"}),
        lambda e: e["order_updates"][0].update(
            {"status": "CANCELLED", "filled_quantity": "10"}
        ),
        lambda e: e.update(
            {
                "trades": [
                    {
                        "id": str(uuid4()),
                        "price": "999999.99999999",
                        "quantity": "1000000.00000001",
                        "buyer_order_id": str(uuid4()),
                        "seller_order_id": str(uuid4()),
                        "timestamp": "2026-07-26T12:00:00+00:00",
                    }
                ]
            }
        ),
        lambda e: e.update(
            {
                "trades": [
                    {
                        "id": str(uuid4()),
                        "price": "1000000",
                        "quantity": "1000000",
                        "buyer_order_id": str(uuid4()),
                        "seller_order_id": str(uuid4()),
                        "timestamp": "2026-07-26T12:00:00+00:00",
                    }
                ]
            }
        ),
        lambda e: e.update(
            {
                "trades": [
                    {
                        "id": str(uuid4()),
                        "stock_id": str(uuid4()),
                        "price": "100",
                        "quantity": "1",
                        "buyer_order_id": str(uuid4()),
                        "seller_order_id": str(uuid4()),
                        "timestamp": "2026-07-26T12:00:00+00:00",
                    }
                ]
            }
        ),
        lambda e: e["order_updates"][0].update({"status": "BOGUS"}),
        lambda e: e["order_updates"][0].update({"filled_quantity": "11"}),
        lambda e: e["order_updates"][0].update(
            {"status": "PENDING", "filled_quantity": "1"}
        ),
        lambda e: e["order_updates"][0].update(
            {"status": "PARTIALLY_FILLED", "filled_quantity": "0"}
        ),
        lambda e: e["order_updates"][0].update(
            {"status": "PARTIALLY_FILLED", "filled_quantity": "10"}
        ),
        lambda e: e["order_updates"][0].update(
            {"status": "FILLED", "filled_quantity": "5"}
        ),
        lambda e: e["order_updates"][0].update({"type": "MARKET", "price": "100"}),
        lambda e: e["order_updates"][0].update({"type": "LIMIT", "price": None}),
        lambda e: e["order_updates"][0].update({"time_in_force": "T" * 21}),
        lambda e: e["order_updates"][0].update({"client_order_id": "C" * 101}),
        lambda e: e["order_updates"][0].update({"stock_id": str(uuid4())}),
        lambda e: e["order_updates"][0].update({"created_at": "2026-07-26T12:00:00"}),
    ],
)
def test_validate_market_data_event_rejects_invalid_payloads(mutator):
    """Reject malformed, overflowing, or semantically inconsistent payloads."""
    event = _valid_event()
    mutator(event)
    with pytest.raises(EventValidationError):
        validate_market_data_event(event)
