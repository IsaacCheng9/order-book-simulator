# Order Book Simulator

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Test](https://github.com/IsaacCheng9/order-book-simulator/actions/workflows/test.yml/badge.svg)](https://github.com/IsaacCheng9/order-book-simulator/actions/workflows/test.yml)

An equity order matching engine and market data system built in Python, designed
to simulate real-world US stock exchange trading mechanics. Processes limit
orders with price-time priority, executes trades, and provides market data
updates in real-time.

## Components

### Order Book Services

The order book services handle order processing, matching, and market data
dissemination. We expose the services via a FastAPI gateway service which
enables the user to interact with the services via REST API calls.

The system consists of four main services:

- **Gateway Service**: REST API service that handles incoming orders and market
  data requests
- **Matching Engine**: Processes orders and executes trades using price-time
  priority
- **Market Data Service**: Manages market data dissemination and analytics
- **Database**: Stores order and trade history

### Market Simulator

The market simulator is a tool that allows you to simulate market activity to
demonstrate how the order book services work. We use the simulator to generate
orders for the order book services to process.

You can find the `MarketSimulator` class in
[src/order_book_simulator/simulator/market_simulator.py](./src/order_book_simulator/simulator/market_simulator.py).
We've also provided an example script in
[examples/market_simulator_usage.py](./examples/market_simulator_usage.py) that
shows how to use the `MarketSimulator` class to simulate market activity.

## Technology Stack

- **FastAPI**: REST API framework for the gateway service
- **Polars**: Data processing and analysis
- **SQLAlchemy**: ORM for the database
- **Pydantic**: Data modelling and validation
- **Kafka**: Message broker for order flow and market data
- **PostgreSQL**: Persistent storage for orders and trades
- **Redis**: Caching for real-time market data
- **Docker**: Containerisation and deployment

## Installing Dependencies

Run the following command from the [project root](./) directory:

```bash
uv sync --all-extras --dev
```

## Usage

### Running the Order Book Services

Use Docker Compose to build and run the services locally:

```bash
docker compose up --build
```

From there, you can interact with the FastAPI service at:
[http://localhost:8000/docs](http://localhost:8000/docs)

### Resetting the Order Book Services

To reset the order book services, you can stop the services and remove the
containers, images, and volumes:

```bash
docker compose down -v
```

### Running the Market Simulator Example

To run the market simulator, you can use the following command:

```bash
uv run python examples/market_simulator_usage.py
```
