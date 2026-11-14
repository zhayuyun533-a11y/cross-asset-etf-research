import json
from pathlib import Path

import pandas as pd
import pytest

from etf_backtest import cli, demo


METRICS = ("Total Return", "CAGR", "Annualized Volatility", "Max Drawdown")


def assert_readable_comparison(stdout, output):
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    performance = pd.read_csv(output / "performance.csv")

    assert f"{manifest['input_kind']} input" in stdout
    assert f"main fee {manifest['main_cost_bp']} bp" in stdout
    assert f"Actual closes: {manifest['first_close']} to {manifest['last_close']}" in stdout
    assert f"Reports: {output.resolve()}" in stdout
    assert all(metric in stdout for metric in METRICS)

    table_rows = []
    for line in stdout.splitlines():
        cells = line.split()
        if cells and cells[0] in {"E", "M"}:
            table_rows.append(cells)
    expected_rows = [
        [row["Strategy"], *(f"{row[metric]:.2%}" for metric in METRICS)]
        for _, row in performance.iterrows()
    ]
    assert table_rows == expected_rows
    assert "CSV metrics are decimal fractions." in stdout
    assert len(stdout.splitlines()) <= 9
    assert {path.name for path in output.iterdir()} == {
        "performance.csv", "fee_sensitivity.csv", "drawdowns.csv",
        "drawdown_events.csv", "run_manifest.json",
    }
    assert performance[list(METRICS)].map(lambda value: isinstance(value, float)).all().all()


@pytest.mark.parametrize("input_kind", ["synthetic", "historical"])
def test_compare_prints_percentages_actual_dates_and_absolute_reports(tmp_path, monkeypatch, capsys, input_kind):
    monkeypatch.chdir(tmp_path)
    prices = Path("prices.csv")
    with prices.open("w", newline="", encoding="utf-8") as stream:
        demo._write_prices(stream)
    output = Path("reports")

    status = cli.main([
        "compare", "--prices", str(prices), "--tickers", *demo.TICKERS,
        "--start", "2023-01", "--end", "2023-12",
        "--input-kind", input_kind, "--output-dir", str(output),
    ])

    assert status == 0
    assert_readable_comparison(capsys.readouterr().out, output)


def test_demo_prints_the_comparison_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert demo.main(["demo"]) == 0

    stdout = capsys.readouterr().out
    assert_readable_comparison(stdout, Path("demo/results"))
    assert "Saved fictional weekday prices" in stdout


def test_compare_help_explains_input_coverage_and_output_rules(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["compare", "--help"])

    assert stopped.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    for expected in (
        "Date, Ticker, Adj Close", "inclusive", "2024-01", "2022-12",
        "warmup", "must not already exist", "fictional", "observed market prices",
    ):
        assert expected in help_text


def test_demo_help_lists_the_created_inputs_and_reports(capsys):
    with pytest.raises(SystemExit) as stopped:
        demo.main(["--help"])

    assert stopped.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    for expected in (
        "must not already exist", "prices.csv", "results/", "performance.csv",
        "fee_sensitivity.csv", "drawdowns.csv", "drawdown_events.csv", "run_manifest.json",
    ):
        assert expected in help_text
