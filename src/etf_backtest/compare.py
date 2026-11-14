"""Construct monthly equal-weight and momentum targets from a local price CSV."""

import re

import pandas as pd

from .core import LOOKBACK_MONTHS, load_prices


def _parse_month(value, option):
    if re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) is None or value.startswith("0000"):
        raise ValueError(f"{option} must be a calendar month in YYYY-MM format")
    return pd.Period(value, freq="M")


def prepare_comparison(prices_path, tickers, requested_start, requested_end, *, verify_us_calendar=False):
    """Return requested closes and one execution target per strategy per month."""
    start = _parse_month(requested_start, "--start")
    end = _parse_month(requested_end, "--end")
    if start > end:
        raise ValueError("--start must be no later than --end")

    warmup_start = start - LOOKBACK_MONTHS - 1
    if verify_us_calendar:
        from .coverage import expected_sessions, validate_session_coverage

        sessions = expected_sessions(warmup_start, end)
        prices = load_prices(
            prices_path, tickers=tickers, month_range=(warmup_start, end),
            first_date=sessions[0], allow_incomplete=True,
        )
        observed = {ticker: prices[ticker].dropna().index for ticker in tickers}
        validate_session_coverage(observed, sessions, start, end)
    else:
        prices = load_prices(prices_path, tickers=tickers, month_range=(warmup_start, end))
    months = prices.index.to_period("M")
    expected = pd.period_range(warmup_start, end, freq="M")
    missing = expected.difference(months)
    if len(missing):
        raise ValueError(f"Prices are missing required warmup or run months: {', '.join(map(str, missing))}")

    run_prices = prices.loc[months >= start]
    run_months = run_prices.index.to_period("M")
    execution_days = run_prices.index[~run_months.duplicated()]
    signal_months = run_months.unique() - 1
    monthly = prices.groupby(months).last()

    # The denominator is strictly positive, so positive 12-month return
    # means the two adjusted closes compare strictly greater.
    positive = monthly.loc[signal_months].to_numpy() > monthly.loc[
        signal_months - LOOKBACK_MONTHS
    ].to_numpy()
    sleeve = 1 / len(tickers)
    equal = pd.DataFrame(sleeve, index=execution_days, columns=tickers)
    equal["Cash"] = 0.0
    momentum = pd.DataFrame(positive * sleeve, index=execution_days, columns=tickers)
    momentum["Cash"] = (~positive).sum(axis=1) / len(tickers)
    return run_prices, {"E": equal, "M": momentum}
