# Order Book Simulator

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Test](https://github.com/IsaacCheng9/order-book-simulator/actions/workflows/test.yml/badge.svg)](https://github.com/IsaacCheng9/order-book-simulator/actions/workflows/test.yml)

An equity order matching engine and market data system built in Python, designed
to simulate real-world stock exchange trading mechanics. Processes limit orders
with price-time priority, executes trades, and provides market data updates in
real-time.

## Components

The system consists of three main components:

- Gateway Service: Handles incoming orders and API interactions
- Matching Engine: Processes orders and executes trades
- Market Data Service: Manages market data dissemination

## Usage

### Installing Dependencies

Run the following command from the [project root](./) directory:

```bash
uv sync --all-extras --dev
```

### Running the Simulator Locally

Use Docker Compose to build and run the services locally:

```bash
docker compose -f docker/docker-compose.yml up --build
```

From there, you can interact with the FastAPI service at:
[http://localhost:8000/docs](http://localhost:8000/docs)
