-- Create extensions for enhanced functionality
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enum for order types
CREATE TYPE order_type AS ENUM ('MARKET', 'LIMIT', 'STOP');
CREATE TYPE order_side AS ENUM ('BUY', 'SELL');
CREATE TYPE order_status AS ENUM ('PENDING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED');

-- Instrument/Trading Pair table
CREATE TABLE instrument (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL UNIQUE,
    base_currency VARCHAR(10) NOT NULL,
    quote_currency VARCHAR(10) NOT NULL,
    min_order_size NUMERIC(20, 8) NOT NULL,
    max_order_size NUMERIC(20, 8) NOT NULL,
    price_precision INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Order table
CREATE TABLE order_ (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    instrument_id UUID NOT NULL,
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
    
    FOREIGN KEY (instrument_id) REFERENCES instrument(id)
);

-- Trade table (execution records)
CREATE TABLE trade (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    buyer_order_id UUID NOT NULL,
    seller_order_id UUID NOT NULL,
    instrument_id UUID NOT NULL,
    price NUMERIC(20, 8) NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    total_amount NUMERIC(20, 8) NOT NULL,
    buyer_fee NUMERIC(20, 8) NOT NULL,
    seller_fee NUMERIC(20, 8) NOT NULL,
    trade_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (buyer_order_id) REFERENCES order_(id),
    FOREIGN KEY (seller_order_id) REFERENCES order_(id),
    FOREIGN KEY (instrument_id) REFERENCES instrument(id)
);

-- Market Data Snapshot table
CREATE TABLE market_data_snapshot (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    instrument_id UUID NOT NULL,
    open_price NUMERIC(20, 8) NOT NULL,
    high_price NUMERIC(20, 8) NOT NULL,
    low_price NUMERIC(20, 8) NOT NULL,
    close_price NUMERIC(20, 8) NOT NULL,
    volume NUMERIC(20, 8) NOT NULL,
    snapshot_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (instrument_id) REFERENCES instrument(id)
);

-- Indexes for performance optimization
CREATE INDEX idx_orders_user_instrument ON order_(user_id, instrument_id);
CREATE INDEX idx_trades_instrument ON trade(instrument_id);
CREATE INDEX idx_market_data_instrument ON market_data_snapshot(instrument_id);
CREATE INDEX idx_orders_status ON order_(status);
CREATE INDEX idx_trades_time ON trade(trade_time);

-- Function to automatically update the 'updated_at' timestamp
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers to automatically update 'updated_at' for instruments and orders
CREATE TRIGGER update_instruments_modtime
    BEFORE UPDATE ON instrument
    FOR EACH ROW
    EXECUTE FUNCTION update_modified_column();

CREATE TRIGGER update_orders_modtime
    BEFORE UPDATE ON order_
    FOR EACH ROW
    EXECUTE FUNCTION update_modified_column();
