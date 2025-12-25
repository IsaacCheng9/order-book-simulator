"""Runs all unit benchmarks."""

import asyncio

from matching_engine_benchmark import main as run_matching_engine_benchmark
from order_book_benchmark import main as run_order_book_benchmark


def main() -> None:
    """Runs all unit benchmarks in sequence."""
    print("=" * 80)
    print("Unit Benchmarks")
    print("=" * 80)
    print()

    print("=" * 80)
    print("1. Order Book Benchmark (Pure Data Structures)")
    print("=" * 80)
    print()
    run_order_book_benchmark()
    print()

    print("=" * 80)
    print("2. Matching Engine Benchmark (Mocked I/O)")
    print("=" * 80)
    print()
    asyncio.run(run_matching_engine_benchmark())
    print()

    print("=" * 80)
    print("Unit benchmarks complete")
    print("=" * 80)


if __name__ == "__main__":
    main()
