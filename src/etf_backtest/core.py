"""Run ETF backtests from adjusted closes and monthly execution targets.

Prices are adjusted closes. Target rows execute at that day's close after the
previous holdings earn that session's return. Cash earns zero interest.
"""

import csv
from threading import Lock

import numpy as np
import pandas as pd
from pandas.io.common import get_handle

TICKERS = ("DBC", "EFA", "GLD", "IEF", "SPY", "VNQ")
PERIODS = {"development": (2008, 2015), "validation": (2016, 2020), "holdout": (2021, 2025)}
FULL_SAMPLE = "full_sample"
LOOKBACK_MONTHS = 12
INITIAL_CAPITAL = 600.0
WEIGHT_COLUMNS = (*TICKERS, "Cash")
_CSV_FIELD_LIMIT_LOCK = Lock()


def _validate_dates(index, label):
    if not isinstance(index, pd.DatetimeIndex) or index.empty:
        raise ValueError(f"{label} needs a nonempty DatetimeIndex")
    _validate_calendar_dates(index, label)
    if not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError(f"{label} dates must be unique and sorted")


def _validate_calendar_dates(dates, label):
    if dates.hasnans or dates.tz is not None or not dates.equals(dates.normalize()):
        raise ValueError(f"{label} needs timezone-free calendar dates")


def _validate_prices(prices, tickers=TICKERS):
    _validate_dates(prices.index, "Prices")
    if list(prices.columns) != list(tickers):
        raise ValueError(f"Price columns must follow {tickers}")
    values = prices.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Adj Close coverage must be complete, finite, and positive")


def _validate_weights(weights, tickers=TICKERS):
    _validate_dates(weights.index, "Targets")
    weight_columns = (*tickers, "Cash")
    if list(weights.columns) != list(weight_columns):
        raise ValueError(f"Target columns must follow {weight_columns}")
    values = weights.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
        raise ValueError("Target weights must be finite and between zero and one")
    if not np.allclose(values.sum(axis=1), 1.0, atol=1e-12, rtol=0):
        raise ValueError("Target weights must sum to one")


def _first_days(prices, start, end):
    dates = prices.loc[str(start):str(end)].index
    months = dates.to_period("M")
    expected = pd.period_range(f"{start}-01", f"{end}-12", freq="M")
    if not months.unique().equals(expected):
        raise ValueError(f"Prices need every month in {start}–{end}")
    return dates[~months.duplicated()].rename("Date")


def _validate_development(weights, prices, strategy):
    _validate_weights(weights)
    if strategy not in ("E", "M"):
        raise ValueError("Strategy must be E or M")
    if not weights.index.equals(_first_days(prices, 2008, 2015)):
        raise ValueError("Development targets need the first trading day of all 96 months in 2008–2015")
    etf_weights = weights.loc[:, list(TICKERS)].to_numpy(dtype=float)
    if strategy == "E":
        if not np.allclose(etf_weights, 1 / 6, atol=1e-12, rtol=0):
            raise ValueError("E requires six equal ETF sleeves")
        if not np.allclose(weights["Cash"], 0.0, atol=1e-12, rtol=0):
            raise ValueError("E requires zero cash")
    else:
        allowed = np.isclose(etf_weights, 0.0, atol=1e-12, rtol=0) | np.isclose(
            etf_weights, 1 / 6, atol=1e-12, rtol=0
        )
        if not allowed.all():
            raise ValueError("M ETF sleeves must each be zero or one sixth")


def _validate_csv_records(stream, path, required_columns):
    last_line = ""

    def checked_lines():
        nonlocal last_line
        for line_number, last_line in enumerate(stream, 1):
            if "\x00" in last_line:
                raise ValueError(f"{path} has a NUL byte at line {line_number}")
            yield last_line

    reader = csv.reader(checked_lines(), strict=True)
    # Keep quoted empty records. Skip only physically blank lines outside records.
    records = (fields for fields in reader if last_line.strip())
    try:
        header = next(records, [])
        missing = [column for column in required_columns if column not in header]
        if missing:
            raise ValueError(f"{path} is missing required CSV columns: {', '.join(missing)}")
        duplicates = [column for column in required_columns if header.count(column) > 1]
        if duplicates:
            raise ValueError(f"{path} has duplicate required CSV columns: {', '.join(duplicates)}")
        for fields in records:
            if len(fields) != len(header):
                raise ValueError(
                    f"{path} has {len(fields)} CSV fields in the record ending at line "
                    f"{reader.line_num}. The header has {len(header)} fields."
                )
    except csv.Error as error:
        raise ValueError(f"{path} has malformed CSV near line {reader.line_num}: {error}") from error


def _read_csv(path, required_columns, **options):
    # Check the source records before pandas can rename duplicate columns or
    # interpret an extra field as the row index. Use pandas' compression inference.
    with get_handle(path, "r", encoding="utf-8-sig", compression="infer") as handles:
        stream = handles.handle
        try:
            _validate_csv_records(stream, path, required_columns)
        except ValueError as error:
            cause = error.__cause__
            if not isinstance(cause, csv.Error) or not str(cause).startswith("field larger than field limit"):
                raise
            # The standard CSV parser exposes this limit process-wide. Raise it
            # only when a long field needs a retry, and restore it afterward.
            with _CSV_FIELD_LIMIT_LOCK:
                previous_limit = csv.field_size_limit()
                csv.field_size_limit(max(previous_limit, 2**31 - 1))
                try:
                    if stream.seekable():
                        stream.seek(0)
                        _validate_csv_records(stream, path, required_columns)
                    else:
                        with get_handle(path, "r", encoding="utf-8-sig", compression="infer") as retry:
                            _validate_csv_records(retry.handle, path, required_columns)
                finally:
                    csv.field_size_limit(previous_limit)
        if stream.seekable():
            stream.seek(0)
            return pd.read_csv(stream, **options)
    return pd.read_csv(path, **options)


def _price_location(date, ticker):
    return f"Date {date.date()}, Ticker {ticker!r}"


def _float_or_nan(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def load_prices(path, *, tickers=None, month_range=None, first_date=None, allow_incomplete=False):
    """Read long Date/Ticker/Adj Close CSV data.

    By default require the exact six-ETF universe. When a basket is specified,
    keep only its tickers in the given order. Each observed date in the
    optional inclusive month range must have one positive close per ticker.
    Calendar validation can request a partial matrix to diagnose missing
    closes itself. first_date can exclude unneeded days from the first month.
    """
    fixed_universe = tickers is None
    if fixed_universe:
        tickers = TICKERS
        options = {}
    else:
        if (
            not tickers or len(set(tickers)) != len(tickers)
            or any(not ticker.strip() for ticker in tickers)
        ):
            raise ValueError("Provide one or more distinct, nonempty tickers")
        if "Cash" in tickers:
            raise ValueError("Cash is reserved for the uninvested sleeve")
        options = {"dtype": {"Ticker": str}, "keep_default_na": False}

    label = f"Prices CSV {path}"
    rows = _read_csv(path, ("Date", "Ticker", "Adj Close"), **options)
    if not fixed_universe:
        rows = rows.loc[rows["Ticker"].isin(tickers)].copy()
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(rows["Date"], errors="raise"))
    except ValueError as error:
        raise ValueError(f"{label} has an invalid Date: {str(error).splitlines()[0]}") from error
    _validate_calendar_dates(dates, label)
    rows["Date"] = dates
    if month_range is not None:
        start, end = month_range
        months = dates.to_period("M")
        rows = rows.loc[(months >= start) & (months <= end)].copy()
    if first_date is not None:
        rows = rows.loc[rows["Date"] >= first_date].copy()

    observed = set(rows["Ticker"])
    missing_tickers = [ticker for ticker in tickers if ticker not in observed]
    if missing_tickers and not allow_incomplete:
        raise ValueError(f"{label} is missing required tickers: {', '.join(missing_tickers)}")
    if fixed_universe and observed != set(tickers):
        unexpected = sorted(map(str, observed - set(tickers)))
        raise ValueError(f"{label} contains unexpected tickers for the fixed study: {', '.join(unexpected)}")

    duplicates = rows.duplicated(["Date", "Ticker"])
    if duplicates.any():
        row = rows.loc[duplicates].iloc[0]
        location = _price_location(row["Date"], row["Ticker"])
        raise ValueError(f"{label} contains a duplicate price for {location}")

    raw = rows["Adj Close"]
    if fixed_universe:
        try:
            closes = raw.astype(float)
        except (TypeError, ValueError):
            # Locate the first unconvertible value while retaining the replay's
            # existing float conversion for valid inputs.
            closes = raw.map(_float_or_nan)
    else:
        closes = pd.to_numeric(raw, errors="coerce")
    invalid = ~np.isfinite(closes) | closes.le(0)
    if invalid.any():
        row = rows.loc[invalid].iloc[0]
        location = _price_location(row["Date"], row["Ticker"])
        value = row["Adj Close"]
        if pd.isna(value) or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{label} has incomplete Adj Close coverage: missing price for {location}")
        raise ValueError(
            f"{label} has an invalid Adj Close for {location}: {value!r}. "
            "Provide a finite, positive price."
        )

    rows["Adj Close"] = closes
    prices = rows.pivot(index="Date", columns="Ticker", values="Adj Close")
    if allow_incomplete:
        return prices.reindex(columns=tickers).sort_index().astype(float)
    prices = prices.loc[:, list(tickers)].sort_index().astype(float)
    missing_prices = prices.isna().to_numpy()
    if missing_prices.any():
        date_index, ticker_index = np.argwhere(missing_prices)[0]
        location = _price_location(prices.index[date_index], prices.columns[ticker_index])
        raise ValueError(f"{label} has incomplete Adj Close coverage: missing price for {location}")
    _validate_prices(prices, tickers)
    return prices


def load_development_targets(path, prices, strategy):
    """Read the original 96-row execution archive without rebuilding its signals.

    The CSV header is Execution Date, six ETF tickers in fixed order, Cash.
    E has equal invested sleeves. M has zero-or-one-sixth invested sleeves.
    """
    _validate_prices(prices)
    required_columns = ["Execution Date", *WEIGHT_COLUMNS]
    rows = _read_csv(path, required_columns)
    if list(rows.columns) != required_columns:
        raise ValueError(f"Development archive {path} has an unexpected schema or column order")
    dates = pd.DatetimeIndex(pd.to_datetime(rows.pop("Execution Date"), errors="raise"), name="Date")
    rows.index = dates
    weights = rows.astype(float)
    _validate_development(weights, prices, strategy)
    return weights


def build_targets(prices, development_e, development_m):
    """Append 60 validation and 60 holdout targets to each development archive.

    Provide prices covering every month of 2008–2025 plus December 2014 for
    the first validation signal's 12-month lookback. Extra years are ignored.
    Signal dates use each month's final observed trading close. Execution is
    the first observed trading close strictly after that date.
    """
    _validate_prices(prices)
    _validate_development(development_e, prices, "E")
    _validate_development(development_m, prices, "M")
    pieces = {"E": [development_e.copy()], "M": [development_m.copy()]}
    trade_days = prices.index
    signal_days = trade_days.to_series().resample("ME").last()
    monthly = prices.resample("ME").last()
    momentum = monthly.pct_change(periods=LOOKBACK_MONTHS, fill_method=None)

    for period in ("validation", "holdout"):
        start, end = PERIODS[period]
        execution_days = _first_days(prices, start, end)
        months = pd.date_range(f"{start - 1}-12-31", f"{end}-11-30", freq="ME")
        signals = signal_days.reindex(months)
        signals_momentum = momentum.reindex(months)
        if signals.isna().any() or not np.isfinite(signals_momentum.to_numpy()).all():
            raise ValueError(f"Missing monthly price or 12-month lookback for {period}")
        positions = trade_days.searchsorted(signals, side="right")
        if (positions >= len(trade_days)).any():
            raise ValueError(f"Missing execution dates for {period}")
        mapped_days = trade_days[positions].rename("Date")
        if not mapped_days.equals(execution_days):
            raise ValueError(f"Signal and execution months do not align for {period}")

        equal = pd.DataFrame(1 / 6, index=mapped_days, columns=TICKERS)
        equal["Cash"] = 0.0
        positive = signals_momentum.gt(0)
        selected = positive.astype(float) / 6
        if period == "validation":
            selected["Cash"] = 1.0 - selected.sum(axis=1)
        else:
            selected["Cash"] = (~positive).sum(axis=1) / 6
        selected.index = mapped_days
        pieces["E"].append(equal)
        pieces["M"].append(selected)

    targets = {
        strategy: pd.concat(parts, verify_integrity=True).rename_axis("Date")
        for strategy, parts in pieces.items()
    }
    for weights in targets.values():
        _validate_weights(weights)
    return targets


def _rebalance_with_cost(current_values, target_weights, cost_rate):
    """Solve the notebook fee equation on buys plus sells of ETF positions."""
    capital = current_values.sum()
    fee = 0.0
    for _ in range(20):
        new_values = (capital - fee) * target_weights
        recalculated_fee = np.abs(new_values[:-1] - current_values[:-1]).sum() * cost_rate
        if abs(recalculated_fee - fee) < 1e-10:
            return new_values, fee
        fee = recalculated_fee

    # At high rates, direct iteration can be slow. The fee residual is
    # increasing because turnover changes by at most one unit per unit of fee.
    lower, upper = 0.0, capital
    for _ in range(64):
        fee = lower + (upper - lower) / 2
        new_values = (capital - fee) * target_weights
        recalculated_fee = np.abs(new_values[:-1] - current_values[:-1]).sum() * cost_rate
        if fee == lower or fee == upper or recalculated_fee == fee:
            return new_values, fee
        if recalculated_fee > fee:
            lower = fee
        else:
            upper = fee
    return new_values, fee


def run_backtest(prices, execution_weights, initial_capital, cost_rate, *, tickers=TICKERS):
    """Return daily closing values, costs, and returns for one continuous path.

    Slice prices and target rows to the desired simulation before calling.
    The first price date needs a target. Every target date must be a supplied
    trading date. Costs are decimal rates applied to ETF buys plus sells.
    """
    _validate_prices(prices, tickers)
    _validate_weights(execution_weights, tickers)
    initial_capital, cost_rate = float(initial_capital), float(cost_rate)
    if not np.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("Initial capital must be finite and positive")
    if not np.isfinite(cost_rate) or not 0 <= cost_rate < 1:
        raise ValueError("Cost rate must be finite and between zero inclusive and one exclusive")
    if prices.index[0] not in execution_weights.index:
        raise ValueError("The first price date needs an execution target")
    if not execution_weights.index.isin(prices.index).all():
        raise ValueError("Execution targets must fall on supplied trading dates")

    price_values = prices.to_numpy(dtype=float)
    ratios = price_values[1:] / price_values[:-1]
    target_positions = prices.index.get_indexer(execution_weights.index)
    targets_by_position = dict(zip(target_positions, execution_weights.to_numpy(dtype=float)))
    current_values = np.zeros(len(tickers) + 1, dtype=float)
    current_values[-1] = initial_capital
    nav = np.empty(len(prices), dtype=float)
    costs = np.zeros(len(prices), dtype=float)

    for position in range(len(prices)):
        if position:
            current_values[:-1] *= ratios[position - 1]
        if position in targets_by_position:
            current_values, costs[position] = _rebalance_with_cost(
                current_values, targets_by_position[position], cost_rate
            )
        nav[position] = current_values.sum()

    daily_returns = np.empty(len(prices), dtype=float)
    daily_returns[0] = nav[0] / initial_capital - 1
    daily_returns[1:] = nav[1:] / nav[:-1] - 1
    return pd.DataFrame({
        "Close Value": nav, "Cost": costs, "Daily Return": daily_returns,
    }, index=prices.index)


def _window_data(result, period, initial_capital):
    if period not in PERIODS and period != FULL_SAMPLE:
        raise ValueError(f"Unknown period {period!r}")
    _validate_dates(result.index, "Backtest result")
    if not {"Close Value", "Daily Return"}.issubset(result.columns):
        raise ValueError("Backtest result requires Close Value and Daily Return")
    initial_capital = float(initial_capital)
    if not np.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("Initial capital must be finite and positive")
    values = result[["Close Value", "Daily Return"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (result["Close Value"] <= 0).any():
        raise ValueError("Backtest values must be finite with positive closing values")
    if period == FULL_SAMPLE:
        if len(result) < 3:
            raise ValueError("The full sample needs at least three observed closes")
        return result["Close Value"], result["Daily Return"].iloc[1:], initial_capital

    start, end = PERIODS[period]
    window = result.loc[str(start):str(end)]
    if len(window) < 2 or window.index[0].year != start or window.index[-1].year != end:
        raise ValueError(f"Result needs the complete {period} window")
    if period == "development":
        nav = window["Close Value"]
        returns = window["Daily Return"].iloc[1:]
        base = initial_capital
    else:
        prior = result.loc[result.index < window.index[0]]
        if prior.empty or prior.index[-1].year != start - 1 or prior.index[-1].month != 12:
            raise ValueError(f"{period} requires the previous December closing anchor")
        nav = result.loc[prior.index[-1]:window.index[-1], "Close Value"]
        returns = window["Daily Return"]
        base = float(nav.iloc[0])
        growth = float(nav.iloc[-1]) / base
        if abs((1 + returns).prod() - growth) >= 1e-10:
            raise ValueError("Daily returns do not reproduce the anchored window growth")
    return nav, returns, base


def summarize_window(result, period, initial_capital=INITIAL_CAPITAL):
    """Calculate CAGR, volatility, and drawdown for a reporting period.

    Full-sample and development returns include the initial fee. Their
    volatility excludes the fee-only first return. The validation and holdout
    windows use every daily return and the prior December's closing NAV.
    """
    nav, returns, base = _window_data(result, period, initial_capital)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    growth = nav.iloc[-1] / base
    drawdown = drawdown_for_window(result, period, initial_capital)
    metrics = pd.Series({
        "Total Return": growth - 1,
        "CAGR": growth ** (1 / years) - 1,
        "Annualized Volatility": returns.std(ddof=1) * 252 ** 0.5,
        "Max Drawdown": drawdown.min(),
    })
    if period == FULL_SAMPLE and not np.isfinite(metrics.to_numpy()).all():
        raise ValueError("The full sample needs finite performance metrics")
    return metrics


def drawdown_for_window(result, period, initial_capital=INITIAL_CAPITAL):
    """Return drawdowns, including the prior December anchor for later windows."""
    nav, _, base = _window_data(result, period, initial_capital)
    peak = nav.cummax()
    if period in ("development", FULL_SAMPLE):
        peak = peak.clip(lower=base)
    return (nav / peak - 1).rename("Drawdown")
