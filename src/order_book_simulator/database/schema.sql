-- Create extensions for enhanced functionality
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
-- Enum for order types
CREATE TYPE order_type AS ENUM ('MARKET', 'LIMIT', 'STOP');
CREATE TYPE order_side AS ENUM ('BUY', 'SELL');
CREATE TYPE order_status AS ENUM (
    'PENDING',
    'PARTIALLY_FILLED',
    'FILLED',
    'CANCELLED'
);
-- Stock table
CREATE TABLE stock (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- e.g., "AAPL", "MSFT"
    ticker VARCHAR(20) NOT NULL UNIQUE,
    company_name VARCHAR(255) NOT NULL,
    -- Minimum shares per order
    min_order_size NUMERIC(20, 8) NOT NULL,
    -- Maximum shares per order
    max_order_size NUMERIC(20, 8) NOT NULL,
    -- Price decimal places
    price_precision INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
-- Order table
CREATE TABLE order_ (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    stock_id UUID NOT NULL,
    type order_type NOT NULL,
    side order_side NOT NULL,
    status order_status NOT NULL DEFAULT 'PENDING',
    price NUMERIC(20, 8),
    quantity NUMERIC(20, 8) NOT NULL,
    filled_quantity NUMERIC(20, 8) DEFAULT 0,
    total_fee NUMERIC(20, 8) DEFAULT 0,
    time_in_force VARCHAR(20),
    client_order_id VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_id) REFERENCES stock(id)
);
-- Trade table (execution records)
CREATE TABLE trade (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    buyer_order_id UUID NOT NULL,
    seller_order_id UUID NOT NULL,
    stock_id UUID NOT NULL,
    price NUMERIC(20, 8) NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    total_amount NUMERIC(20, 8) NOT NULL,
    buyer_fee NUMERIC(20, 8) NOT NULL,
    seller_fee NUMERIC(20, 8) NOT NULL,
    trade_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (buyer_order_id) REFERENCES order_(id),
    FOREIGN KEY (seller_order_id) REFERENCES order_(id),
    FOREIGN KEY (stock_id) REFERENCES stock(id)
);
-- Market Data Snapshot table
CREATE TABLE market_snapshot (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stock_id UUID NOT NULL,
    bids JSONB NOT NULL,
    asks JSONB NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_id) REFERENCES stock(id)
);
-- Indexes for performance optimization
CREATE INDEX idx_orders_user_stock ON order_(user_id, stock_id);
CREATE INDEX idx_trades_stock ON trade(stock_id);
CREATE INDEX idx_market_snapshot_stock_time ON market_snapshot(stock_id, timestamp);
CREATE INDEX idx_orders_status ON order_(status);
CREATE INDEX idx_trades_time ON trade(trade_time);
CREATE INDEX idx_orders_created_at ON order_(created_at);
CREATE INDEX idx_orders_type_side ON order_(type, side);
CREATE INDEX idx_orders_price ON order_(price)
WHERE type = 'LIMIT';
-- Function to automatically update the 'updated_at' timestamp
CREATE OR REPLACE FUNCTION update_modified_column() RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = CURRENT_TIMESTAMP;
RETURN NEW;
END;
$$ language 'plpgsql';
-- Triggers to automatically update 'updated_at' for stocks and orders
CREATE TRIGGER update_stocks_modtime BEFORE
UPDATE ON stock FOR EACH ROW EXECUTE FUNCTION update_modified_column();
CREATE TRIGGER update_orders_modtime BEFORE
UPDATE ON order_ FOR EACH ROW EXECUTE FUNCTION update_modified_column();
-- Each combination of user_id and client_order_id must be unique.
CREATE UNIQUE INDEX idx_unique_client_order_per_user ON order_(user_id, client_order_id)
WHERE client_order_id IS NOT NULL;