-- Insert test instruments
INSERT INTO instrument (
    id,
    symbol,
    base_currency,
    quote_currency,
    min_order_size,
    max_order_size,
    price_precision
) VALUES (
    '00000000-0000-0000-0000-000000000001',  -- Fixed UUID for testing
    'BTC-USD',                                -- Trading pair
    'BTC',                                    -- Base currency
    'USD',                                    -- Quote currency
    0.0001,                                   -- Minimum order size
    100.0,                                    -- Maximum order size
    2                                         -- Price precision (decimal places)
);
