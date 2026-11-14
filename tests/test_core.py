import numpy as np
import pandas as pd
import pytest

from etf_backtest.core import (
    TICKERS,
    build_targets,
    drawdown_for_window,
    load_development_targets,
    load_prices,
    run_backtest,
    summarize_window,
)


def prices_on(dates):
    return pd.DataFrame(100.0, index=pd.DatetimeIndex(dates, name="Date"), columns=TICKERS)


def targets_on(dates, asset="DBC"):
    result = pd.DataFrame(
        0.0, index=pd.DatetimeIndex(dates, name="Date"), columns=[*TICKERS, "Cash"]
    )
    result[asset] = 1.0
    return result


@pytest.fixture(scope="module")
def full_prices():
    dates = pd.bdate_range("2006-12-01", "2025-12-31", name="Date")
    dates = dates[~((dates.month == 1) & (dates.day == 1))]
    return prices_on(dates)


def development_targets(prices, strategy="E"):
    dates = prices.loc["2008":"2015"].groupby(
        prices.loc["2008":"2015"].index.to_period("M")
    ).head(1).index
    frame = pd.DataFrame(1 / 6, index=dates, columns=TICKERS)
    frame["Cash"] = 0.0
    if strategy == "M":
        frame["VNQ"] = 0.0
        frame["Cash"] = 1 / 6
    return frame


def write_targets(path, frame):
    frame.rename_axis("Execution Date").to_csv(path)


def test_load_prices_pivots_sorted_adjusted_prices_in_fixed_order(tmp_path):
    path = tmp_path / "prices.csv"
    frame = prices_on(["2008-01-03", "2008-01-02"])
    frame.loc["2008-01-02", "SPY"] = 99.0
    long = frame.rename_axis(columns="Ticker").stack().rename("Adj Close").reset_index()
    long["Close"] = 999.0
    long.iloc[::-1].to_csv(path, index=False)

    result = load_prices(path)

    assert list(result.columns) == list(TICKERS)
    assert result.index.tolist() == [pd.Timestamp("2008-01-02"), pd.Timestamp("2008-01-03")]
    assert result.loc["2008-01-02", "SPY"] == 99.0


@pytest.mark.parametrize("problem", ["duplicate", "missing", "zero", "infinite", "unknown"])
def test_load_prices_rejects_invalid_coverage_and_values(tmp_path, problem):
    frame = prices_on(["2008-01-02", "2008-01-03"])
    long = frame.rename_axis(columns="Ticker").stack().rename("Adj Close").reset_index()
    if problem == "duplicate":
        long = pd.concat([long, long.iloc[[0]]])
    elif problem == "missing":
        long = long.iloc[1:]
    elif problem == "zero":
        long.loc[0, "Adj Close"] = 0.0
    elif problem == "infinite":
        long.loc[0, "Adj Close"] = np.inf
    else:
        long.loc[0, "Ticker"] = "QQQ"
    path = tmp_path / "prices.csv"
    long.to_csv(path, index=False)

    with pytest.raises(ValueError):
        load_prices(path)


def test_development_archive_preserves_96_execution_dates_and_weights(tmp_path, full_prices):
    expected = development_targets(full_prices, "M")
    path = tmp_path / "m.csv"
    write_targets(path, expected)

    result = load_development_targets(path, full_prices, "M")

    pd.testing.assert_frame_equal(result, expected, check_exact=False)
    assert len(result) == 96


@pytest.mark.parametrize("problem", ["order", "duplicate", "missing_month", "wrong_day", "strategy", "sum", "negative"])
def test_development_archive_rejects_malformed_inputs(tmp_path, full_prices, problem):
    frame = development_targets(full_prices)
    if problem == "order":
        frame = frame[["EFA", "DBC", "GLD", "IEF", "SPY", "VNQ", "Cash"]]
    elif problem == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]])
    elif problem == "missing_month":
        frame = frame.iloc[1:]
    elif problem == "wrong_day":
        index = frame.index.to_list()
        index[0] = pd.Timestamp("2008-01-03")
        frame.index = pd.DatetimeIndex(index)
    elif problem == "strategy":
        frame.loc[frame.index[0], ["DBC", "Cash"]] = [0.0, 1 / 6]
    elif problem == "sum":
        frame.iloc[0, -1] = 0.1
    else:
        frame.iloc[0, 0] = -0.1
        frame.iloc[0, -1] = 1 / 6 + 0.1
    path = tmp_path / "weights.csv"
    write_targets(path, frame)

    with pytest.raises(ValueError):
        load_development_targets(path, full_prices, "E")


def test_all_positive_m_development_archive_is_valid(tmp_path, full_prices):
    frame = development_targets(full_prices)
    path = tmp_path / "m.csv"
    write_targets(path, frame)
    assert len(load_development_targets(path, full_prices, "M")) == 96


def test_targets_use_strict_positive_prior_month_signal_and_next_trading_day(full_prices):
    prices = full_prices.copy()
    prices.loc["2015-12-31", ["DBC", "EFA"]] = [101.0, 99.0]
    dev_e = development_targets(prices)
    dev_m = development_targets(prices, "M")

    targets = build_targets(prices, dev_e, dev_m)

    for strategy in ("E", "M"):
        assert len(targets[strategy]) == 216
        assert [len(targets[strategy].loc[str(a):str(b)]) for a, b in [(2008, 2015), (2016, 2020), (2021, 2025)]] == [96, 60, 60]
        pd.testing.assert_frame_equal(targets[strategy].loc["2008":"2015"], {"E": dev_e, "M": dev_m}[strategy])
    first = targets["M"].loc["2016-01-04"]
    assert first["DBC"] == pytest.approx(1 / 6)
    assert first[["EFA", "GLD", "IEF", "SPY", "VNQ"]].eq(0).all()
    assert first["Cash"] == pytest.approx(5 / 6)
    prices.loc["2016-01-04":, "EFA"] = 200.0
    revised = build_targets(prices, dev_e, dev_m)
    pd.testing.assert_series_equal(revised["M"].loc["2016-01-04"], first)


def test_targets_reject_missing_signal_lookback_month(full_prices):
    prices = full_prices.loc["2015-01-01":]
    with pytest.raises(ValueError):
        build_targets(prices, development_targets(full_prices), development_targets(full_prices, "M"))


def test_rebalance_at_close_applies_old_holdings_return_before_switch():
    prices = prices_on(["2015-12-31", "2016-01-04", "2016-01-05"])
    prices["DBC"] = [100.0, 200.0, 200.0]
    prices["EFA"] = [100.0, 100.0, 200.0]
    targets = targets_on(prices.index[:2])
    targets.loc[prices.index[1], ["DBC", "EFA"]] = [0.0, 1.0]

    result = run_backtest(prices, targets, 600.0, 0.0)

    assert result["Close Value"].to_list() == pytest.approx([600.0, 1200.0, 2400.0])
    assert result["Daily Return"].to_list() == pytest.approx([0.0, 1.0, 1.0])
    assert result["Cost"].eq(0).all()


def test_fees_charge_entry_and_sale_without_charging_cash():
    prices = prices_on(["2008-01-02", "2008-01-03", "2008-01-04"])
    targets = targets_on(prices.index)
    targets.loc[prices.index[1]:, ["DBC", "Cash"]] = [0.0, 1.0]

    result = run_backtest(prices, targets, 600.0, 0.001)

    assert result["Close Value"].to_list() == pytest.approx([599.4005994006, 598.8011988012, 598.8011988012])
    assert result["Cost"].to_list() == pytest.approx([0.5994005994, 0.5994005994, 0.0])
    assert 600 * (1 + result["Daily Return"]).prod() == pytest.approx(result["Close Value"].iloc[-1])


def test_switch_between_etfs_charges_both_sell_and_buy_after_capital_grows():
    prices = prices_on(["2015-12-31", "2016-01-04", "2016-01-05"])
    prices["DBC"] = [100.0, 200.0, 200.0]
    prices["EFA"] = [100.0, 100.0, 200.0]
    targets = targets_on(prices.index[:2])
    targets.loc[prices.index[1], ["DBC", "EFA"]] = [0.0, 1.0]

    result = run_backtest(prices, targets, 600.0, 0.001)

    assert result["Close Value"].to_list() == pytest.approx([
        599.4005994006, 1196.405992, 2392.811984,
    ])
    assert result["Cost"].to_list() == pytest.approx([
        0.5994005994, 2.3952071904, 0.0,
    ])


def test_partial_cash_target_charges_only_the_invested_sleeve():
    prices = prices_on(["2008-01-02", "2008-01-03"])
    targets = targets_on(prices.index[:1])
    targets.loc[:, ["DBC", "Cash"]] = [0.5, 0.5]

    result = run_backtest(prices, targets, 600.0, 0.001)

    assert result["Cost"].to_list() == pytest.approx([0.29985007496, 0.0])
    assert result["Close Value"].to_list() == pytest.approx([599.7001499250, 599.7001499250])


@pytest.mark.parametrize("problem", ["first_target", "off_calendar", "bad_sum", "negative", "nan", "price_order", "capital", "rate"])
def test_backtest_rejects_invalid_inputs(problem):
    prices = prices_on(["2008-01-02", "2008-01-03"])
    targets = targets_on(prices.index)
    capital, rate = 600.0, 0.001
    if problem == "first_target":
        targets = targets.iloc[1:]
    elif problem == "off_calendar":
        targets.index = pd.DatetimeIndex(["2008-01-02", "2008-01-05"])
    elif problem == "bad_sum":
        targets.iloc[0, 0] = 0.8
    elif problem == "negative":
        targets.iloc[0, [0, -1]] = [-0.1, 1.1]
    elif problem == "nan":
        prices.iloc[0, 0] = np.nan
    elif problem == "price_order":
        prices = prices.iloc[::-1]
    elif problem == "capital":
        capital = 0.0
    else:
        rate = np.inf
    with pytest.raises(ValueError):
        run_backtest(prices, targets, capital, rate)


def test_development_metrics_include_entry_fee_against_initial_capital():
    result = pd.DataFrame({
        "Close Value": [594.0, 660.0, 600.0],
        "Cost": [6.0, 0.0, 0.0],
        "Daily Return": [-0.01, 1 / 9, -1 / 11],
    }, index=pd.DatetimeIndex(["2008-01-02", "2008-01-03", "2015-12-31"]))

    summary = summarize_window(result, "development")
    drawdown = drawdown_for_window(result, "development")

    assert summary["Total Return"] == pytest.approx(0.0)
    assert summary["CAGR"] == pytest.approx(0.0)
    assert summary["Annualized Volatility"] == pytest.approx(np.std([1 / 9, -1 / 11], ddof=1) * np.sqrt(252))
    assert drawdown.to_list() == pytest.approx([-0.01, 0.0, -1 / 11])
    assert summary["Max Drawdown"] == pytest.approx(-1 / 11)


def test_validation_metrics_use_prior_year_anchor_and_first_session_return():
    result = pd.DataFrame({
        "Close Value": [500.0, 450.0, 550.0],
        "Cost": [0.0, 0.0, 0.0],
        "Daily Return": [0.0, -0.1, 2 / 9],
    }, index=pd.DatetimeIndex(["2015-12-31", "2016-01-04", "2020-12-31"]))

    summary = summarize_window(result, "validation")
    drawdown = drawdown_for_window(result, "validation")

    assert summary["Total Return"] == pytest.approx(0.1)
    assert summary["CAGR"] == pytest.approx(1.1 ** (365.25 / 1827) - 1)
    assert summary["Annualized Volatility"] == pytest.approx(np.std([-0.1, 2 / 9], ddof=1) * np.sqrt(252))
    assert drawdown.to_list() == pytest.approx([0.0, -0.1, 0.0])
    assert summary["Max Drawdown"] == pytest.approx(-0.1)
    with pytest.raises(ValueError):
        summarize_window(result.iloc[1:], "validation")
