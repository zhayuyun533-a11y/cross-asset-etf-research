import csv
import hashlib
import json
import math
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import pandas as pd

from etf_backtest.cli import recovery_events


REPO = Path(__file__).resolve().parents[1]
PERIODS = {"development", "validation", "holdout"}


def test_recovery_uses_the_actual_prior_december_anchor():
    dates = pd.to_datetime(["2020-12-30", "2021-01-04", "2025-12-31"])
    path = pd.DataFrame({
        "Close Value": [100.0, 90.0, 105.0],
        "Daily Return": [0.0, -0.1, 105 / 90 - 1],
    }, index=dates)

    events = recovery_events({"E": path}).iloc[0]

    assert events["Peak"] == "2020-12-30"
    assert events["Trough"] == "2021-01-04"
    assert events["Recovery"] == "2025-12-31"
    assert events["Max Drawdown"] == pytest.approx(-0.1)


@pytest.fixture(scope="module")
def demo_inputs(tmp_path_factory):
    input_dir = tmp_path_factory.mktemp("demo") / "inputs"
    result = subprocess.run(
        [sys.executable, str(REPO / "examples" / "generate_demo.py"), str(input_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return input_dir


def run_cli(input_dir, output_dir, *, prices=None):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "etf_backtest",
            "--prices",
            str(prices if prices is not None else input_dir / "prices.csv"),
            "--dev-e",
            str(input_dir / "dev-e.csv"),
            "--dev-m",
            str(input_dir / "dev-m.csv"),
            "--output-dir",
            str(output_dir),
            "--input-kind",
            "synthetic",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def test_demo_has_price_coverage_and_96_development_targets(demo_inputs):
    header, prices = read_csv(demo_inputs / "prices.csv")
    tickers = ["DBC", "EFA", "GLD", "IEF", "SPY", "VNQ"]
    assert header == ["Date", "Ticker", "Adj Close"]
    assert prices[0]["Date"] == "2006-12-01"
    assert prices[-1]["Date"] == "2025-12-31"
    assert {row["Ticker"] for row in prices} == set(tickers)
    prices_by_date = {}
    month_ends = {}
    for row in prices:
        value = float(row["Adj Close"])
        assert math.isfinite(value) and value > 0
        prices_by_date.setdefault(row["Date"], {})[row["Ticker"]] = value
        month_ends[row["Date"][:7]] = row["Date"]
    assert len(prices) == 6 * len(prices_by_date)
    assert all(set(values) == set(tickers) for values in prices_by_date.values())
    observed_dates = list(prices_by_date)
    assert observed_dates == sorted(observed_dates)

    e_header, e_rows = read_csv(demo_inputs / "dev-e.csv")
    m_header, m_rows = read_csv(demo_inputs / "dev-m.csv")
    assert e_header == m_header == ["Execution Date", *tickers, "Cash"]
    execution_dates = [row["Execution Date"] for row in e_rows]
    assert len(execution_dates) == len(set(execution_dates)) == 96
    assert execution_dates == [row["Execution Date"] for row in m_rows]
    assert execution_dates[0] == "2008-01-02"
    assert execution_dates[-1] == "2015-12-01"
    assert 0 < float(m_rows[0]["Cash"]) < 1
    for e_row, m_row in zip(e_rows, m_rows):
        execution = e_row["Execution Date"]
        signal = observed_dates[observed_dates.index(execution) - 1]
        assert signal[:7] != execution[:7]
        assert signal == month_ends[signal[:7]]
        reference_month = f"{int(signal[:4]) - 1}{signal[4:7]}"
        reference = month_ends[reference_month]
        accepted = 0
        for ticker in tickers:
            assert float(e_row[ticker]) == pytest.approx(1 / 6)
            positive = prices_by_date[signal][ticker] > prices_by_date[reference][ticker]
            assert float(m_row[ticker]) == pytest.approx(1 / 6 if positive else 0)
            accepted += positive
        assert float(e_row["Cash"]) == 0
        assert float(m_row["Cash"]) == pytest.approx((6 - accepted) / 6)


def test_demo_generation_is_deterministic_and_preserves_existing_files(demo_inputs, tmp_path):
    duplicate = tmp_path / "inputs"
    command = [sys.executable, str(REPO / "examples" / "generate_demo.py"), str(duplicate)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = {path.name: path.read_bytes() for path in demo_inputs.iterdir()}
    assert {path.name: path.read_bytes() for path in duplicate.iterdir()} == expected

    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert {path.name: path.read_bytes() for path in duplicate.iterdir()} == expected


def test_cli_writes_a_complete_report_for_synthetic_inputs(demo_inputs, tmp_path):
    output = tmp_path / "results"
    result = run_cli(demo_inputs, output)
    assert result.returncode == 0, result.stderr
    assert {
        "performance.csv",
        "fee_sensitivity.csv",
        "holdout_drawdowns.csv",
        "holdout_drawdown_events.csv",
        "run_manifest.json",
    } <= {path.name for path in output.iterdir() if path.is_file()}

    header, performance = read_csv(output / "performance.csv")
    assert header == [
        "Period", "Strategy", "Total Return", "CAGR", "Annualized Volatility", "Max Drawdown"
    ]
    assert len(performance) == 6
    assert {(row["Period"], row["Strategy"]) for row in performance} == {
        (period, strategy) for period in PERIODS for strategy in {"E", "M"}
    }
    for row in performance:
        assert all(math.isfinite(float(row[column])) for column in header[2:])
        assert float(row["Annualized Volatility"]) >= 0
        assert -1 <= float(row["Max Drawdown"]) <= 0

    header, sensitivity = read_csv(output / "fee_sensitivity.csv")
    assert header == ["Period", "Cost (bp)", "E", "M", "M - E"]
    assert len(sensitivity) == 12
    assert {(row["Period"], int(row["Cost (bp)"])) for row in sensitivity} == {
        (period, cost) for period in PERIODS for cost in {0, 5, 10, 20}
    }
    main_cagr = {(row["Period"], row["Strategy"]): float(row["CAGR"]) for row in performance}
    for row in sensitivity:
        assert all(math.isfinite(float(row[column])) for column in ("E", "M", "M - E"))
        assert float(row["M - E"]) == pytest.approx(float(row["M"]) - float(row["E"]))
        if row["Cost (bp)"] == "10":
            for strategy in ("E", "M"):
                assert float(row[strategy]) == pytest.approx(main_cagr[row["Period"], strategy])

    header, drawdowns = read_csv(output / "holdout_drawdowns.csv")
    assert header == ["Date", "E", "M"]
    drawdown_dates = [date.fromisoformat(row["Date"]) for row in drawdowns]
    assert drawdown_dates[0] == date(2020, 12, 31)
    assert drawdown_dates[-1] == date(2025, 12, 31)
    assert drawdown_dates == sorted(set(drawdown_dates))
    holdout_max_drawdown = {
        row["Strategy"]: float(row["Max Drawdown"])
        for row in performance if row["Period"] == "holdout"
    }
    for strategy in ("E", "M"):
        values = [float(row[strategy]) for row in drawdowns]
        assert values[0] == 0
        assert all(math.isfinite(value) and -1 <= value <= 0 for value in values)
        assert min(values) == pytest.approx(holdout_max_drawdown[strategy])

    header, events = read_csv(output / "holdout_drawdown_events.csv")
    assert header == ["Strategy", "Peak", "Trough", "Recovery", "Max Drawdown"]
    assert len(events) == 2
    assert {row["Strategy"] for row in events} == {"E", "M"}
    for row in events:
        peak = date.fromisoformat(row["Peak"])
        trough = date.fromisoformat(row["Trough"])
        assert drawdown_dates[0] <= peak <= trough <= drawdown_dates[-1]
        if row["Recovery"]:
            recovery = date.fromisoformat(row["Recovery"])
            assert trough <= recovery <= drawdown_dates[-1]
        assert float(row["Max Drawdown"]) == pytest.approx(holdout_max_drawdown[row["Strategy"]])

    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_kind"] == "synthetic"
    assert manifest["costs_bp"] == [0, 5, 10, 20]
    assert manifest["main_cost_bp"] == 10
    for key, filename in {"prices": "prices.csv", "dev_e": "dev-e.csv", "dev_m": "dev-m.csv"}.items():
        expected_hash = hashlib.sha256((demo_inputs / filename).read_bytes()).hexdigest()
        assert manifest["inputs"][key]["sha256"] == expected_hash


def test_cli_rejects_invalid_prices_without_creating_output(demo_inputs, tmp_path):
    prices = tmp_path / "invalid-prices.csv"
    prices.write_text("Date,Ticker,Close\n2008-01-02,DBC,100\n", encoding="utf-8")
    output = tmp_path / "results"
    result = run_cli(demo_inputs, output, prices=prices)
    assert result.returncode != 0
    assert "Adj Close" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("has_report", [False, True])
def test_cli_preserves_an_existing_output_directory(demo_inputs, tmp_path, has_report):
    output = tmp_path / "results"
    output.mkdir()
    prior_report = output / "performance.csv"
    if has_report:
        prior_report.write_text("Existing report\n", encoding="utf-8")

    result = run_cli(demo_inputs, output)

    assert result.returncode != 0
    assert "exists" in result.stderr.lower()
    assert list(output.iterdir()) == ([prior_report] if has_report else [])
    if has_report:
        assert prior_report.read_text(encoding="utf-8") == "Existing report\n"
