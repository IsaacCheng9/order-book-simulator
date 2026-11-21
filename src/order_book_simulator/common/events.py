"""Canonical versioned event contracts and payload validators."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any
from uuid import UUID

from order_book_simulator.common.models import OrderSide, OrderStatus, OrderType

EVENT_SCHEMA_VERSION = 1
DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000000")
# Inclusive upper bound matching PostgreSQL NUMERIC(20, 8) without rounding to 10^12.
MAX_NUMERIC_20_8_VALUE = Decimal("999999999999.99999999")
NUMERIC_20_8_QUANT = Decimal("0.00000001")
MAX_NUMERIC_20_8_SCALE = 8
MAX_NUMERIC_20_8_EXPONENT = 11
MAX_TICKER_LENGTH = 20
MAX_TIME_IN_FORCE_LENGTH = 20
MAX_CLIENT_ORDER_ID_LENGTH = 100


class EventValidationError(ValueError):
    """Raised when a Kafka event fails schema or semantic validation."""


def validate_schema_version(payload: dict[str, Any]) -> int:
    """Validate optional schema_version, defaulting legacy payloads to v1.

    Args:
        payload: Decoded Kafka event dictionary.

    Returns:
        The validated integer schema version.

    Raises:
        EventValidationError: If schema_version is non-integer or unsupported.
    """
    raw_version = payload.get("schema_version")
    if raw_version is None:
        return EVENT_SCHEMA_VERSION
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise EventValidationError("schema_version must be an integer.")
    if raw_version != EVENT_SCHEMA_VERSION:
        raise EventValidationError(
            f"Unsupported schema_version: {raw_version!r} "
            f"(expected {EVENT_SCHEMA_VERSION})."
        )
    return raw_version


def _require_non_empty_str(
    mapping: dict[str, Any], field: str, *, max_length: int | None = None
) -> str:
    """Return a required non-empty string field within an optional length cap."""
    if field not in mapping:
        raise EventValidationError(f"Missing required field: {field}.")
    value = mapping[field]
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{field} must be a non-empty string.")
    if "\x00" in value:
        raise EventValidationError(f"{field} must not contain NUL bytes.")
    if max_length is not None and len(value) > max_length:
        raise EventValidationError(f"{field} exceeds maximum length of {max_length}.")
    return value


def _validate_optional_str(
    mapping: dict[str, Any], field: str, *, max_length: int
) -> str | None:
    """Validate an optional string field against a maximum length."""
    value = mapping.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventValidationError(f"{field} must be a string or null.")
    if "\x00" in value:
        raise EventValidationError(f"{field} must not contain NUL bytes.")
    if len(value) > max_length:
        raise EventValidationError(f"{field} exceeds maximum length of {max_length}.")
    return value


def _parse_uuid(value: Any, field: str) -> UUID:
    """Parse a required UUID string or UUID instance."""
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{field} must be a UUID string.")
    try:
        return UUID(value)
    except ValueError as exc:
        raise EventValidationError(f"{field} must be a valid UUID.") from exc


def validate_numeric_20_8(
    value: Any,
    field: str,
    *,
    allow_zero: bool = False,
) -> Decimal:
    """Parse and validate a Decimal fitting PostgreSQL NUMERIC(20, 8)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise EventValidationError(f"{field} must be a numeric value.")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise EventValidationError(f"{field} must be a valid decimal.") from exc
    if not parsed.is_finite():
        raise EventValidationError(f"{field} must be finite.")
    if parsed.is_signed():
        raise EventValidationError(f"{field} must not be negative.")
    exp = parsed.as_tuple().exponent
    if isinstance(exp, int) and (
        exp < -MAX_NUMERIC_20_8_SCALE or exp > MAX_NUMERIC_20_8_EXPONENT
    ):
        raise EventValidationError(f"{field} exponent out of range for NUMERIC(20, 8).")
    if parsed == Decimal(0):
        if not allow_zero:
            raise EventValidationError(f"{field} must be positive.")
        return Decimal(0)
    if parsed > MAX_NUMERIC_20_8_VALUE:
        raise EventValidationError(f"{field} exceeds NUMERIC(20, 8) limit.")
    return parsed


def validate_notional_20_8(price: Decimal, quantity: Decimal, field: str) -> None:
    """Ensure price * quantity rounds within PostgreSQL NUMERIC(20, 8)."""
    with localcontext() as ctx:
        ctx.prec = 40
        notional = (price * quantity).quantize(
            NUMERIC_20_8_QUANT, rounding=ROUND_HALF_UP
        )
    if notional > MAX_NUMERIC_20_8_VALUE:
        raise EventValidationError(f"{field} exceeds NUMERIC(20, 8) limit.")


_parse_decimal = validate_numeric_20_8


def _parse_iso_timestamp(value: Any, field: str) -> datetime:
    """Parse a required timezone-aware ISO-8601 timestamp string."""
    if not isinstance(value, str) or not value:
        raise EventValidationError(f"{field} must be an ISO-8601 timestamp string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EventValidationError(
            f"{field} must be a valid ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        raise EventValidationError(f"{field} must include a timezone.")
    return parsed


def _validate_book_levels(levels: Any, side_name: str) -> None:
    """Validate a list of order book price-level dicts."""
    if not isinstance(levels, list):
        raise EventValidationError(f"{side_name} must be a list.")
    for idx, level in enumerate(levels):
        if not isinstance(level, dict):
            raise EventValidationError(f"{side_name}[{idx}] must be an object.")
        _parse_decimal(level.get("price"), f"{side_name}[{idx}].price")
        _parse_decimal(
            level.get("quantity"), f"{side_name}[{idx}].quantity", allow_zero=True
        )
        order_count = level.get("order_count")
        if order_count is not None and (
            isinstance(order_count, bool)
            or not isinstance(order_count, int)
            or order_count < 0
        ):
            raise EventValidationError(
                f"{side_name}[{idx}].order_count must be a non-negative integer."
            )


def _validate_trades(trades: Any, event_stock_id: UUID) -> None:
    """Validate the optional trades list on a market-data event."""
    if trades is None:
        return
    if not isinstance(trades, list):
        raise EventValidationError("trades must be a list.")
    for idx, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise EventValidationError(f"trades[{idx}] must be an object.")
        if trade.get("id") is not None:
            _parse_uuid(trade["id"], f"trades[{idx}].id")
        if trade.get("stock_id") is not None:
            trade_stock_id = _parse_uuid(trade["stock_id"], f"trades[{idx}].stock_id")
            if trade_stock_id != event_stock_id:
                raise EventValidationError(
                    f"trades[{idx}].stock_id does not match event stock_id."
                )
        _parse_uuid(trade.get("buyer_order_id"), f"trades[{idx}].buyer_order_id")
        _parse_uuid(trade.get("seller_order_id"), f"trades[{idx}].seller_order_id")
        price = _parse_decimal(trade.get("price"), f"trades[{idx}].price")
        quantity = _parse_decimal(trade.get("quantity"), f"trades[{idx}].quantity")
        validate_notional_20_8(price, quantity, f"trades[{idx}] notional")
        if trade.get("buyer_fee") is not None:
            _parse_decimal(
                trade["buyer_fee"], f"trades[{idx}].buyer_fee", allow_zero=True
            )
        if trade.get("seller_fee") is not None:
            _parse_decimal(
                trade["seller_fee"], f"trades[{idx}].seller_fee", allow_zero=True
            )
        _parse_iso_timestamp(trade.get("timestamp"), f"trades[{idx}].timestamp")


def _validate_order_updates(order_updates: Any, event_stock_id: UUID) -> None:
    """Validate canonical order-state transitions on a market-data event."""
    if order_updates is None:
        return
    if not isinstance(order_updates, list):
        raise EventValidationError("order_updates must be a list.")
    for idx, order in enumerate(order_updates):
        if not isinstance(order, dict):
            raise EventValidationError(f"order_updates[{idx}] must be an object.")
        _parse_uuid(order.get("id"), f"order_updates[{idx}].id")
        _parse_uuid(order.get("user_id"), f"order_updates[{idx}].user_id")
        order_stock_id = _parse_uuid(
            order.get("stock_id"), f"order_updates[{idx}].stock_id"
        )
        if order_stock_id != event_stock_id:
            raise EventValidationError(
                f"order_updates[{idx}].stock_id does not match event stock_id."
            )
        try:
            order_type = OrderType(_require_non_empty_str(order, "type"))
        except ValueError as exc:
            raise EventValidationError(
                f"order_updates[{idx}].type is not a valid OrderType."
            ) from exc
        try:
            OrderSide(_require_non_empty_str(order, "side"))
        except ValueError as exc:
            raise EventValidationError(
                f"order_updates[{idx}].side is not a valid OrderSide."
            ) from exc
        try:
            order_status = OrderStatus(_require_non_empty_str(order, "status"))
        except ValueError as exc:
            raise EventValidationError(
                f"order_updates[{idx}].status is not a valid OrderStatus."
            ) from exc

        quantity = _parse_decimal(
            order.get("quantity"), f"order_updates[{idx}].quantity"
        )
        filled_quantity = _parse_decimal(
            order.get("filled_quantity"),
            f"order_updates[{idx}].filled_quantity",
            allow_zero=True,
        )
        if filled_quantity > quantity:
            raise EventValidationError(
                f"order_updates[{idx}].filled_quantity exceeds quantity."
            )
        if order_status == OrderStatus.PENDING and filled_quantity > Decimal(0):
            raise EventValidationError(
                f"order_updates[{idx}] PENDING order cannot have positive "
                "filled_quantity."
            )
        if order_status == OrderStatus.PARTIALLY_FILLED and (
            filled_quantity <= Decimal(0) or filled_quantity >= quantity
        ):
            raise EventValidationError(
                f"order_updates[{idx}] PARTIALLY_FILLED order must have "
                "0 < filled_quantity < quantity."
            )
        if order_status == OrderStatus.FILLED and filled_quantity != quantity:
            raise EventValidationError(
                f"order_updates[{idx}] FILLED order must have filled_quantity "
                "equal to quantity."
            )
        if order_status == OrderStatus.CANCELLED and filled_quantity >= quantity:
            raise EventValidationError(
                f"order_updates[{idx}] CANCELLED order must have "
                "filled_quantity strictly less than quantity."
            )
        if order.get("total_fee") is not None:
            _parse_decimal(
                order["total_fee"],
                f"order_updates[{idx}].total_fee",
                allow_zero=True,
            )

        raw_price = order.get("price")
        if order_type == OrderType.MARKET:
            if raw_price is not None:
                raise EventValidationError(
                    f"order_updates[{idx}].price must be null for MARKET orders."
                )
        elif order_type == OrderType.LIMIT or raw_price is not None:
            _parse_decimal(raw_price, f"order_updates[{idx}].price")

        _validate_optional_str(
            order, "time_in_force", max_length=MAX_TIME_IN_FORCE_LENGTH
        )
        _validate_optional_str(
            order, "client_order_id", max_length=MAX_CLIENT_ORDER_ID_LENGTH
        )
        _parse_iso_timestamp(
            order.get("created_at"), f"order_updates[{idx}].created_at"
        )
        _parse_iso_timestamp(
            order.get("updated_at"), f"order_updates[{idx}].updated_at"
        )


def validate_market_data_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a market-data Kafka event before batching for persistence.

    Args:
        payload: Decoded JSON object from the market-data Kafka topic.

    Returns:
        The validated event dictionary.

    Raises:
        EventValidationError: If any schema or semantic invariant is violated.
    """
    validate_schema_version(payload)
    stock_id = _parse_uuid(payload.get("stock_id"), "stock_id")
    _require_non_empty_str(payload, "ticker", max_length=MAX_TICKER_LENGTH)

    if payload.get("timestamp") is not None:
        _parse_iso_timestamp(payload["timestamp"], "timestamp")

    seq = payload.get("sequence_number")
    if seq is not None and (
        isinstance(seq, bool) or not isinstance(seq, int) or seq < 0
    ):
        raise EventValidationError("sequence_number must be a non-negative integer.")

    if "bids" not in payload or "asks" not in payload:
        raise EventValidationError("Market data event must include bids and asks.")
    _validate_book_levels(payload["bids"], "bids")
    _validate_book_levels(payload["asks"], "asks")
    _validate_trades(payload.get("trades"), stock_id)
    _validate_order_updates(payload.get("order_updates"), stock_id)
    return payload
