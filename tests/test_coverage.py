from datetime import datetime

import exchange_calendars
import pandas as pd
import pytest

from etf_backtest.compare import prepare_comparison
from etf_backtest import market_data


TICKERS = ("SPY", "GLD")


def test_short_history_preserves_all_tickers_and_proposes_first_valid_month(tmp_path):
    sessions = exchange_calendars.get_calendar("XNYS", start="2014-12-01", end="2016-12-31").sessions
    selected = [("SPY", sessions), ("GLD", sessions[sessions >= "2015-06-15"])]
    path = tmp_path / "prices.csv"
    pd.concat([pd.DataFrame({"Date": dates, "Ticker": ticker, "Adj Close": 100})
               for ticker, dates in selected]).to_csv(path, index=False)

    with pytest.raises(ValueError, match=r"GLD: missing.*2014-12-31.*2015-06-12.*Earliest valid start.*2016-07"):
        prepare_comparison(path, list(TICKERS), "2016-01", "2016-12", verify_us_calendar=True)

    prices, targets = prepare_comparison(path, list(TICKERS), "2016-07", "2016-12", verify_us_calendar=True)
    assert prices.index[0] == pd.Timestamp("2016-07-01")
    assert prices.index[-1] == pd.Timestamp("2016-12-30")
    assert list(targets["M"]) == ["SPY", "GLD", "Cash"]
    assert targets["M"]["Cash"].eq(1.0).all()


def test_checks_for_a_date_shared_missing_by_all_tickers_and_preserves_real_closures(tmp_path):
    sessions = exchange_calendars.get_calendar("XNYS", start="2017-11-01", end="2018-12-31").sessions
    path = tmp_path / "prices.csv"
    rows = pd.concat([pd.DataFrame({"Date": sessions, "Ticker": ticker, "Adj Close": 100})
                      for ticker in TICKERS])
    # George H.W. Bush's national day of mourning was a full market closure.
    assert pd.Timestamp("2018-12-05") not in sessions
    rows.to_csv(path, index=False)
    prices, _ = prepare_comparison(path, list(TICKERS), "2018-12", "2018-12", verify_us_calendar=True)
    assert pd.Timestamp("2018-12-05") not in prices.index
    assert pd.Timestamp("2018-12-06") in prices.index

    rows[rows["Date"] != pd.Timestamp("2018-12-06")].to_csv(path, index=False)
    with pytest.raises(ValueError, match=r"SPY: missing.*2018-12-06.*GLD: missing.*2018-12-06.*No valid later start"):
        prepare_comparison(path, list(TICKERS), "2018-12", "2018-12", verify_us_calendar=True)


def test_incomplete_final_month_fails_instead_of_shortening(tmp_path):
    sessions = exchange_calendars.get_calendar("XNYS", start="2014-12-01", end="2016-03-31").sessions
    path = tmp_path / "prices.csv"
    dates = sessions[sessions <= "2016-03-29"]
    pd.DataFrame({"Date": dates, "Ticker": "SPY", "Adj Close": 100}).to_csv(path, index=False)

    with pytest.raises(ValueError, match=r"SPY: missing.*2016-03-30.*2016-03-31.*No valid later start.*2016-03"):
        prepare_comparison(path, ["SPY"], "2016-01", "2016-03", verify_us_calendar=True)


def test_yahoo_rejects_usd_security_listed_outside_us(tmp_path, monkeypatch):
    from test_market_data import chart, encode, respond

    payload = chart()
    payload["chart"]["result"][0]["meta"].update(exchangeName="TOR", fullExchangeName="Toronto")
    respond(monkeypatch, {"SPY": encode(payload)})
    with pytest.raises(ValueError, match=r"SPY.*U.S. listing.*TOR"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", tmp_path / "download")
    assert not (tmp_path / "download").exists()


def test_yahoo_short_history_reports_verified_candidate_without_saving(tmp_path, monkeypatch):
    from test_market_data import chart, encode, respond

    sessions = exchange_calendars.get_calendar("XNYS", start="2014-12-01", end="2016-12-31").sessions
    all_days = tuple(d.date().isoformat() for d in sessions)
    short_days = tuple(d for d in all_days if d >= "2015-06-15")
    payload = chart("GLD", days=short_days)
    payload["chart"]["result"][0]["meta"]["firstTradeDate"] = int(
        datetime.fromisoformat("2015-06-15T13:30:00+00:00").timestamp()
    )
    respond(monkeypatch, {"SPY": encode(chart(days=all_days)), "GLD": encode(payload)})
    with pytest.raises(ValueError, match=r"GLD: missing.*2014-12-31.*2015-06-12.*Earliest valid start.*2016-07"):
        market_data.download_data(list(TICKERS), "2016-01", "2016-12", tmp_path / "download")
    assert not (tmp_path / "download").exists()


def test_first_month_ignores_data_before_required_month_end(tmp_path, monkeypatch):
    from test_market_data import chart, encode, respond

    payload = chart()
    result = payload["chart"]["result"][0]
    result["meta"]["exchangeName"] = "NMS"
    result["timestamp"].insert(0, int(datetime.fromisoformat("2024-12-30T14:30:00+00:00").timestamp()))
    result["indicators"]["adjclose"][0]["adjclose"].insert(0, None)
    respond(monkeypatch, {"SPY": encode(payload)})
    output = market_data.download_data(["SPY"], "2026-01", "2026-01", tmp_path / "download")
    assert (output / "prices.csv").read_text().splitlines()[1].startswith("2024-12-31,")
