"""Generate fictional weekday prices and run an offline three-ETF comparison."""

import argparse
import csv
import math
import sys
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

from . import cli


TICKERS = ("DEMOETF1", "DEMOETF2", "DEMOETF3")
START_MONTH = "2023-01"
END_MONTH = "2024-12"
PRICE_START = date(2021, 12, 1)  # Reference month for the first momentum signal.
PRICE_END = date(2024, 12, 31)


def _write_prices(stream):
    writer = csv.writer(stream)
    writer.writerow(["Date", "Ticker", "Adj Close"])
    day = PRICE_START
    one_day = timedelta(days=1)
    while day <= PRICE_END:
        if day.weekday() < 5:
            years = (day - PRICE_START).days / 365.25
            for index, ticker in enumerate(TICKERS):
                price = (70 + 8 * index) * math.exp(
                    (0.02 + 0.006 * index) * years
                    + 0.30 * math.sin(2.5 * years + 1.8 * index)
                    + 0.10 * math.cos(5.0 * years - 0.55 * index)
                )
                writer.writerow([day.isoformat(), ticker, f"{price:.8f}"])
        day += one_day


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Creates prices.csv and a results/ directory containing performance.csv, "
            "fee_sensitivity.csv, drawdowns.csv, drawdown_events.csv, and run_manifest.json. "
            "Prints the E/M comparison at the main 10 bp fee."
        ),
    )
    parser.add_argument(
        "directory", type=Path, help="New directory for the demo; must not already exist."
    )
    directory = parser.parse_args(argv).directory
    prices = directory / "prices.csv"

    try:
        with ExitStack() as cleanup:
            directory.mkdir(parents=True, exist_ok=False)
            cleanup.callback(directory.rmdir)
            with prices.open("x", newline="", encoding="utf-8") as stream:
                cleanup.callback(prices.unlink)
                _write_prices(stream)
            status = cli.main([
                "compare",
                f"--prices={prices}",
                "--tickers", *TICKERS,
                "--start", START_MONTH,
                "--end", END_MONTH,
                "--input-kind", "synthetic",
                f"--output-dir={directory / 'results'}",
            ])
            if status == 0:
                cleanup.pop_all()
    except OSError as error:
        print(f"etf-backtest-demo: {error}", file=sys.stderr)
        return 1

    if status == 0:
        print(f"Saved fictional weekday prices to {prices}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
