"""Create fictional E/M inputs using weekdays, with January 1 excluded.

The smooth price paths are mathematical examples, independent of market data.
The calendar serves the demonstration and does not model exchange holidays.
"""

import argparse
import csv
import math
from datetime import date, timedelta
from pathlib import Path


TICKERS = ("DBC", "EFA", "GLD", "IEF", "SPY", "VNQ")
START = date(2006, 12, 1)
END = date(2025, 12, 31)


def generate_demo(destination):
    sessions = []
    day = START
    while day <= END:
        if day.weekday() < 5 and (day.month, day.day) != (1, 1):
            sessions.append(day)
        day += timedelta(days=1)

    prices = {}
    month_ends = {}
    for day in sessions:
        years = (day - START).days / 365.25
        prices[day] = {
            ticker: (70 + 8 * index) * math.exp(
                (0.02 + 0.006 * index) * years
                + 0.45 * math.sin(1.7 * years + 1.1 * index)
                + 0.14 * math.cos(4.4 * years - 0.55 * index)
            )
            for index, ticker in enumerate(TICKERS)
        }
        month_ends[day.year, day.month] = day

    e_rows = []
    m_rows = []
    for previous, execution in zip(sessions, sessions[1:]):
        if not 2008 <= execution.year <= 2015:
            continue
        if (previous.year, previous.month) == (execution.year, execution.month):
            continue
        reference = month_ends[previous.year - 1, previous.month]
        selected = [
            prices[previous][ticker] / prices[reference][ticker] - 1 > 0
            for ticker in TICKERS
        ]
        e_rows.append([execution.isoformat(), *([1 / 6] * 6), 0.0])
        m_rows.append([
            execution.isoformat(),
            *(int(positive) / 6 for positive in selected),
            (6 - sum(selected)) / 6,
        ])

    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "prices.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Date", "Ticker", "Adj Close"])
        for day, values in prices.items():
            writer.writerows([day.isoformat(), ticker, values[ticker]] for ticker in TICKERS)
    for filename, rows in (("dev-e.csv", e_rows), ("dev-m.csv", m_rows)):
        with (destination / filename).open("x", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["Execution Date", *TICKERS, "Cash"])
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="New directory for the three demo CSVs")
    args = parser.parse_args()
    if args.directory.exists():
        parser.error(f"Output directory already exists: {args.directory}")
    generate_demo(args.directory)
    print(f"Synthetic inputs saved to {args.directory}")
    print("Calendar: Monday–Friday, excluding January 1. Prices are fictional.")


if __name__ == "__main__":
    main()
