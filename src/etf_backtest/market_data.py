"""Download auditable public ETF adjusted-close data from Yahoo Finance."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .compare import _parse_month
from .core import LOOKBACK_MONTHS
from .coverage import CALENDAR_NAME, CALENDAR_VERSION, expected_sessions, validate_session_coverage


# Chart metadata codes for U.S. equity exchanges that can list ETFs.
_US_EXCHANGES = frozenset({"ASE", "BTS", "NAS", "NGM", "NCM", "NMS", "NYQ", "PCX"})


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_response(raw, ticker, first_date, end_exclusive):
    """Validate provider data without substituting the unadjusted close field."""
    try:
        payload = json.loads(raw)
        chart = payload["chart"]
        if not isinstance(chart, dict):
            raise TypeError("chart must be an object")
        if chart.get("error"):
            raise ValueError(f"{ticker}: provider error: {chart['error']}")
        results = chart["result"]
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError(f"{ticker}: provider must return exactly one chart result")
        result = results[0]
        meta = result["meta"]
        if not isinstance(meta, dict):
            raise TypeError("meta must be an object")
        timestamps = result["timestamp"]
    except (KeyError, TypeError, IndexError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{ticker}: malformed provider chart response: {error}") from error

    if meta.get("symbol") != ticker:
        raise ValueError(f"{ticker}: provider symbol mismatch: {meta.get('symbol')!r}")
    if meta.get("instrumentType") != "ETF":
        raise ValueError(f"{ticker}: provider instrumentType must be ETF")
    if meta.get("currency") != "USD":
        raise ValueError(f"{ticker}: currency must be USD; received {meta.get('currency')!r}")
    exchange = meta.get("exchangeName")
    if not isinstance(exchange, str) or exchange not in _US_EXCHANGES:
        raise ValueError(f"{ticker}: U.S. listing could not be verified from exchangeName {exchange!r}")
    try:
        exchange_timezone = meta["exchangeTimezoneName"]
        local_timezone = ZoneInfo(exchange_timezone)
        first_trade_timestamp = meta["firstTradeDate"]
        if isinstance(first_trade_timestamp, bool) or not isinstance(first_trade_timestamp, int) or first_trade_timestamp <= 0:
            raise ValueError("firstTradeDate must be a positive timestamp")
        first_trade_date = datetime.fromtimestamp(first_trade_timestamp, local_timezone).date().isoformat()
    except (KeyError, TypeError, ValueError, OverflowError, OSError, ZoneInfoNotFoundError) as error:
        raise ValueError(f"{ticker}: invalid exchange timezone or firstTradeDate: {error}") from error
    try:
        adjusted = result["indicators"]["adjclose"]
        if not isinstance(adjusted, list) or len(adjusted) != 1:
            raise ValueError("expected exactly one adjusted close array")
        values = adjusted[0]["adjclose"]
    except (KeyError, TypeError, IndexError, ValueError) as error:
        raise ValueError(f"{ticker}: actual adjusted close field is missing or malformed") from error
    if not isinstance(timestamps, list) or not isinstance(values, list) or not timestamps or len(timestamps) != len(values):
        raise ValueError(f"{ticker}: timestamps and adjusted close values must be nonempty arrays of equal length")

    rows = {}
    seen = set()
    for timestamp, value in zip(timestamps, values, strict=True):
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError(f"{ticker}: invalid daily timestamp {timestamp!r}")
        try:
            day = datetime.fromtimestamp(timestamp, local_timezone).date().isoformat()
        except (ValueError, OverflowError, OSError) as error:
            raise ValueError(f"{ticker}: invalid daily timestamp {timestamp!r}") from error
        if not first_date <= day < end_exclusive:
            continue
        if day in seen:
            raise ValueError(f"{ticker}: Duplicate exchange-local trading date {day}")
        seen.add(day)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{ticker}: adjusted close must be finite and strictly positive on {day}; received {value!r}")
        rows[day] = float(value)
    rows = dict(sorted(rows.items()))
    metadata = {
        "currency": meta["currency"], "exchange": exchange, "exchange_timezone": exchange_timezone,
        "instrument_type": meta["instrumentType"], "provider_symbol": meta["symbol"],
        "first_trade_timestamp": first_trade_timestamp, "first_trade_date": first_trade_date,
        "coverage": {"first_date": min(rows, default=None), "last_date": max(rows, default=None), "observations": len(rows)},
    }
    return rows, metadata


def _fetch(ticker, query, first_date, end_exclusive):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?{query}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; etf-backtest/0.1)", "Accept": "application/json"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
            retrieved_at = _utc_now()
            break
        except HTTPError as error:
            if attempt == 0 and (error.code == 429 or 500 <= error.code <= 599):
                time.sleep(1)
                continue
            raise ValueError(f"{ticker}: Yahoo Finance HTTP {error.code} after {attempt + 1} attempt(s): {error.reason}") from error
        except (URLError, OSError, TimeoutError) as error:
            raise ValueError(f"{ticker}: Yahoo Finance download failed: {error}") from error
    rows, metadata = _parse_response(raw, ticker, first_date, end_exclusive)
    metadata.update({"ticker": ticker, "path": f"raw/{ticker}.json", "sha256": hashlib.sha256(raw).hexdigest(),
                     "request_url": url, "retrieved_at": retrieved_at})
    if rows:
        print(f"{ticker}: {len(rows)} daily adjusted closes ({min(rows)} to {max(rows)})", flush=True)
    else:
        print(f"{ticker}: no daily adjusted closes in this window", flush=True)
    return raw, rows, metadata


def download_data(tickers, requested_start, requested_end, output_dir):
    """Write a fresh, complete download directory, or leave no output on failure."""
    tickers = [ticker.upper() for ticker in tickers]
    if not tickers or any(re.fullmatch(r"[A-Z][A-Z0-9.-]{0,19}", ticker) is None for ticker in tickers):
        raise ValueError("--tickers must contain ETF symbols using letters, digits, dots or hyphens")
    if len(tickers) != len(set(tickers)):
        raise ValueError("--tickers must not contain duplicate symbols")
    start = _parse_month(requested_start, "--start")
    end = _parse_month(requested_end, "--end")
    if start > end:
        raise ValueError("--start must be no later than --end")
    output = Path(output_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output directory already exists: {output}")
    warmup = start - LOOKBACK_MONTHS - 1
    sessions = expected_sessions(warmup, end)
    first_date = sessions[0].date().isoformat()
    end_exclusive = (end + 1).start_time.date().isoformat()
    query = urlencode({
        "period1": int(datetime.fromisoformat(first_date).replace(tzinfo=timezone.utc).timestamp()),
        "period2": int(datetime.fromisoformat(end_exclusive).replace(tzinfo=timezone.utc).timestamp()),
        "interval": "1d", "includeAdjustedClose": "true",
    })
    downloads = {}
    with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as executor:
        futures = {executor.submit(_fetch, ticker, query, first_date, end_exclusive): ticker for ticker in tickers}
        for future in as_completed(futures):
            downloads[futures[future]] = future.result()
    validate_session_coverage(
        {ticker: downloads[ticker][1].keys() for ticker in tickers}, sessions, start, end,
        first_trade_dates={ticker: downloads[ticker][2]["first_trade_date"] for ticker in tickers},
    )

    provenance = {
        "schema_version": 1, "input_kind": "historical",
        "source": {"provider": "Yahoo Finance", "endpoint": "chart", "interval": "1d",
                   "price_field": "indicators.adjclose[0].adjclose"},
        "retrieved_at": _utc_now(),
        "requested": {"tickers": tickers, "start": str(start), "end": str(end), "warmup_start": str(warmup)},
        "normalized": {"path": "prices.csv", "columns": ["Date", "Ticker", "Adj Close"]},
        "trading_calendar": {"name": CALENDAR_NAME, "version": CALENDAR_VERSION},
        "raw_files": [downloads[ticker][2] for ticker in sorted(tickers)],
        "transformations": [
            "Read actual provider adjusted close values; never substitute ordinary close.",
            "Convert provider timestamps to dates in each exchange's named timezone.",
            f"Retain exchange-local dates from {first_date} inclusive to {end_exclusive} exclusive.",
            "Require positive finite prices on every expected XNYS trading session; no forward fill or null-row deletion.",
            "Sort normalized long-form CSV by Date, then Ticker; preserve raw response bytes unchanged.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        stage = Path(temporary) / "complete"
        (stage / "raw").mkdir(parents=True)
        for ticker in tickers:
            (stage / "raw" / f"{ticker}.json").write_bytes(downloads[ticker][0])
        prices_path = stage / "prices.csv"
        with prices_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(provenance["normalized"]["columns"])
            for session in sessions:
                day = session.date().isoformat()
                for ticker in sorted(tickers):
                    writer.writerow([day, ticker, repr(downloads[ticker][1][day])])
        provenance["normalized"]["sha256"] = hashlib.sha256(prices_path.read_bytes()).hexdigest()
        (stage / "provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            output.mkdir()
        except FileExistsError as error:
            raise ValueError(f"Output directory already exists: {output}") from error
        try:
            for item in stage.iterdir():
                os.rename(item, output / item.name)
        except OSError:
            shutil.rmtree(output)
            raise
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download public USD ETF adjusted closes with raw responses and provenance.")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--start", required=True, help="First run month, YYYY-MM; download includes 13 warmup months")
    parser.add_argument("--end", required=True, help="Last run month, YYYY-MM")
    parser.add_argument("--output-dir", required=True, help="Fresh output directory; existing paths are never overwritten")
    args = parser.parse_args(argv)
    try:
        output = download_data(args.tickers, args.start, args.end, args.output_dir)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Download failed: {error}\n")
    print(f"Saved prices, raw responses and provenance to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
