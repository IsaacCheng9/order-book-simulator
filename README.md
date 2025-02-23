# Order Book Simulator

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Test](https://github.com/IsaacCheng9/order-book-simulator/actions/workflows/test.yml/badge.svg)](https://github.com/IsaacCheng9/order-book-simulator/actions/workflows/test.yml)

An equity order matching engine and market data system built in Python, designed
to simulate real-world US stock exchange trading mechanics. Processes limit
orders with price-time priority, executes trades, and provides market data
updates in real-time.

## Components

The system consists of three main components:

- Gateway Service: Handles incoming orders and API interactions
- Matching Engine: Processes orders and executes trades
- Market Data Service: Manages market data dissemination

## Installing Dependencies

Run the following command from the [project root](./) directory:

```bash
uv sync --all-extras --dev
```

## Usage

### Running the Order Book Services

The services handle order processing, matching, and market data dissemination.
We expose the services via a FastAPI gateway service which enables the user to
interact with the services via REST API calls.

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
