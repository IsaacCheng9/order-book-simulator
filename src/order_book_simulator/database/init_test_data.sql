-- Insert test stocks
INSERT INTO stock (
        id,
        ticker,
        min_order_size,
        max_order_size,
        price_precision
    )
VALUES (
        -- Fixed UUID for testing
        '00000000-0000-0000-0000-000000000001',
        -- Stock ticker
        'AAPL',
        -- Minimum order size (1 share)
        1,
        -- Maximum order size (100k shares)
        100000,
        -- Price precision (cents)
        2
    );