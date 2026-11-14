import csv
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from etf_backtest.cli import main
from etf_backtest.core import TICKERS as STUDY_TICKERS, load_development_targets


TICKERS = ("AAA", "BBB", "CCC")


@pytest.fixture
def price_rows():
    warmup = pd.date_range("2022-12-01", "2023-12-31", freq="BME")
    run = pd.to_datetime([
        "2024-01-03", "2024-01-04", "2024-01-31",
        "2024-02-01", "2024-02-02", "2024-02-29",
    ])
    prices = pd.DataFrame(100.0, index=warmup.append(run), columns=TICKERS)
    prices.loc["2023-01-31"] = [300, 100, 230]
    prices.loc["2023-12-29"] = [120, 90, 100]
    prices.loc["2024-01-03"] = [200, 200, 200]
    prices.loc[["2024-01-04", "2024-01-31"]] = [220, 180, 230]
    prices.loc["2024-02-01"] = [440, 180, 230]
    prices.loc[["2024-02-02", "2024-02-29"]] = [440, 225, 230]
    return prices.rename_axis(index="Date", columns="Ticker").stack().rename("Adj Close").reset_index()


def compare_args(prices, output, *, tickers=TICKERS, start="2024-01", end="2024-01", kind="synthetic"):
    return [
        "compare", "--prices", str(prices), "--tickers", *tickers,
        "--start", start, "--end", end, "--input-kind", kind,
        "--output-dir", str(output),
    ]


def test_price_only_command_uses_prior_month_signal_and_requested_basket(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    unrelated = pd.DataFrame([["invalid", "EXTRA", -100], ["invalid", "EXTRA", -100]],
                             columns=price_rows.columns)
    pd.concat([unrelated, price_rows.iloc[::-1]]).to_csv(prices, index=False)
    tickers = ("CCC", "AAA", "BBB")

    source = Path(__file__).resolve().parents[1] / "src"
    process = subprocess.run(
        [sys.executable, "-B", "-m", "etf_backtest", *compare_args(prices, output, tickers=tickers)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(source)},
    )

    assert process.returncode == 0, process.stderr
    assert {path.name for path in output.iterdir()} == {
        "performance.csv", "fee_sensitivity.csv", "drawdowns.csv",
        "drawdown_events.csv", "run_manifest.json",
    }
    performance = pd.read_csv(output / "performance.csv").set_index("Strategy")
    assert performance["Period"].to_dict() == {"E": "full_sample", "M": "full_sample"}
    assert performance.at["E", "Total Return"] == pytest.approx(1.05 / 1.001 - 1)
    assert performance.at["M", "Total Return"] == pytest.approx((1 + 0.1 / 3) / (1 + 0.001 / 3) - 1)
    assert performance.at["E", "CAGR"] == pytest.approx((1.05 / 1.001) ** (365.25 / 28) - 1)
    assert performance.at["E", "Annualized Volatility"] == pytest.approx(0.05 * (252 / 2) ** 0.5)

    sensitivity = pd.read_csv(output / "fee_sensitivity.csv").set_index("Cost (bp)")
    assert sensitivity.index.to_list() == [0, 5, 10, 20]
    assert sensitivity["Period"].eq("full_sample").all()
    for cost, row in sensitivity.iterrows():
        expected_e = (1.05 / (1 + cost / 10_000)) ** (365.25 / 28) - 1
        expected_m = ((1 + 0.1 / 3) / (1 + cost / 30_000)) ** (365.25 / 28) - 1
        assert [row["E"], row["M"], row["M - E"]] == pytest.approx(
            [expected_e, expected_m, expected_m - expected_e]
        )
    assert sensitivity.loc[10, ["E", "M"]].to_list() == pytest.approx(
        performance.loc[["E", "M"], "CAGR"].to_list()
    )

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["mode"] == "compare"
    assert manifest["fee_sensitivity_metric"] == "CAGR"
    assert manifest["protocol"] == "price-only-em-v1"
    assert manifest["period"] == "full_sample"
    assert manifest["input_kind"] == "synthetic"
    assert manifest["inputs"] == {"prices": {"name": "prices.csv", "sha256": sha256(prices.read_bytes()).hexdigest()}}
    assert manifest["tickers"] == list(tickers)
    assert (manifest["requested_start_month"], manifest["requested_end_month"]) == ("2024-01", "2024-01")
    assert (manifest["first_close"], manifest["last_close"]) == ("2024-01-03", "2024-01-31")
    assert manifest["daily_rows"] == 3
    assert manifest["execution_dates"] == {"E": 1, "M": 1}


def test_compare_applies_the_execution_day_move_before_switching_sleeves(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output, end="2024-02", kind="historical")) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["input_kind"] == "historical"
    assert manifest["last_close"] == "2024-02-29"
    assert manifest["execution_dates"] == {"E": 2, "M": 2}
    sensitivity = pd.read_csv(output / "fee_sensitivity.csv").set_index("Cost (bp)")
    # M earns the AAA move on February 1 before allocating one third to BBB.
    # January close is 620, February 1 close is 840, and February 2 close is 910.
    assert sensitivity.at[0, "M"] == pytest.approx((910 / 600) ** (365.25 / 57) - 1)


@pytest.mark.parametrize("later_price,recovery", [(100.03, ""), (100.2, "2024-01-04")])
def test_drawdown_events_use_initial_capital_as_peak(price_rows, tmp_path, later_price, recovery):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.loc[price_rows["Date"].eq("2024-01-03"), "Adj Close"] = 100
    price_rows.loc[price_rows["Date"].between("2024-01-04", "2024-01-31"), "Adj Close"] = later_price
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 0

    drawdowns = pd.read_csv(output / "drawdowns.csv")
    assert drawdowns.columns.to_list() == ["Date", "E", "M"]
    assert drawdowns["Date"].to_list() == ["2024-01-03", "2024-01-04", "2024-01-31"]
    assert drawdowns.loc[0, ["E", "M"]].to_list() == pytest.approx(
        [1 / 1.001 - 1, 1 / (1 + 0.001 / 3) - 1]
    )
    events = pd.read_csv(output / "drawdown_events.csv", keep_default_na=False).set_index("Strategy")
    for strategy in ["E", "M"]:
        assert events.at[strategy, "Peak"] == "2024-01-03"
        assert events.at[strategy, "Peak Is Initial Capital"]
        assert events.at[strategy, "Trough"] == "2024-01-03"
        assert events.at[strategy, "Recovery"] == recovery
        assert events.at[strategy, "Max Drawdown"] == pytest.approx(drawdowns[strategy].min())


@pytest.mark.parametrize(
    "problem,error",
    [
        ("missing warmup", "2023-06"),
        ("missing run month", "2024-03"),
        ("missing selected close", "coverage"),
        ("missing symbol", "DDD"),
        ("duplicate selected close", "duplicate"),
        ("invalid selected date", "date"),
        ("zero price", "positive"),
        ("infinite price", "finite"),
        ("duplicate ticker", "distinct"),
        ("invalid month", "YYYY-MM"),
        ("reversed months", "start"),
        ("too few closes", "at least three"),
    ],
)
def test_compare_rejects_invalid_inputs_without_creating_output(price_rows, tmp_path, capsys, problem, error):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    args = {}
    if problem == "missing warmup":
        price_rows = price_rows[price_rows["Date"].dt.to_period("M") != "2023-06"]
    elif problem == "missing run month":
        args["end"] = "2024-03"
    elif problem == "missing selected close":
        price_rows = price_rows[~(price_rows["Date"].eq("2024-01-04") & price_rows["Ticker"].eq("AAA"))]
    elif problem == "missing symbol":
        args["tickers"] = ("AAA", "DDD")
    elif problem == "duplicate selected close":
        price_rows = pd.concat([price_rows, price_rows.iloc[[0]]])
    elif problem == "invalid selected date":
        price_rows["Date"] = price_rows["Date"].astype(str)
        price_rows.loc[0, "Date"] = ""
    elif problem == "zero price":
        price_rows.loc[0, "Adj Close"] = 0
    elif problem == "infinite price":
        price_rows.loc[0, "Adj Close"] = float("inf")
    elif problem == "duplicate ticker":
        args["tickers"] = ("AAA", "AAA")
    elif problem == "invalid month":
        args["start"] = "2024-1"
    elif problem == "reversed months":
        args["start"] = "2024-02"
    elif problem == "too few closes":
        price_rows = price_rows[~price_rows["Date"].eq("2024-01-04")]
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output, **args)) == 1

    assert error.lower() in capsys.readouterr().err.lower()
    assert not output.exists()


@pytest.mark.parametrize("column", ["Date", "Ticker", "Adj Close"])
def test_compare_rejects_ambiguous_required_csv_headers(price_rows, tmp_path, capsys, column):
    prices = tmp_path / "ambiguous.csv"
    output = tmp_path / "results"
    price_rows.insert(len(price_rows.columns), column, "conflicting", allow_duplicates=True)
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 1

    error = capsys.readouterr().err
    assert "duplicate" in error.lower()
    assert prices.name in error and column in error
    assert not output.exists()


@pytest.mark.parametrize("mismatch", ["leading extra field", "trailing extra field", "missing field"])
def test_compare_rejects_csv_records_that_do_not_match_header(price_rows, tmp_path, capsys, mismatch):
    prices = tmp_path / "misaligned.csv"
    output = tmp_path / "results"
    header, *rows = price_rows.to_csv(index=False).splitlines()
    if mismatch == "leading extra field":
        rows = [f"extra,{row}" for row in rows]
    elif mismatch == "trailing extra field":
        rows = [f"{row},extra" for row in rows]
    else:
        header += ",Optional"
        rows[1:] = [f"{row},extra" for row in rows[1:]]
    prices.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")

    assert main(compare_args(prices, output)) == 1

    error = capsys.readouterr().err
    assert prices.name in error and "line 2" in error and "fields" in error
    assert not output.exists()


def test_compare_accepts_quoted_optional_fields_bom_and_blank_lines(price_rows, tmp_path):
    prices = tmp_path / "quoted.csv"
    output = tmp_path / "results"
    price_rows["Source notes"] = "Snapshot, export\nwith a second line"
    contents = price_rows.to_csv(index=False).replace("\n", "\n\n  \n", 1)
    prices.write_text("\n" + contents, encoding="utf-8-sig")

    assert main(compare_args(prices, output)) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["daily_rows"] == 3


@pytest.mark.parametrize("field", ["header", "price"])
def test_compare_rejects_null_bytes_before_csv_parsers_disagree(price_rows, tmp_path, capsys, field):
    prices = tmp_path / "null-byte.csv"
    output = tmp_path / "results"
    if field == "header":
        price_rows = price_rows.rename(columns={"Adj Close": "Adj Close\x00hidden"})
        price_rows["Adj Close"] = 500.0
        line = 1
    else:
        price_rows["Adj Close"] = price_rows["Adj Close"].astype(str)
        price_rows.loc[0, "Adj Close"] = "1\x0025"
        line = 2
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 1

    error = capsys.readouterr().err
    assert "NUL" in error and prices.name in error and f"line {line}" in error
    assert not output.exists()


def test_compare_accepts_a_long_optional_field_and_restores_csv_limit(price_rows, tmp_path):
    prices = tmp_path / "long-field.csv"
    output = tmp_path / "results"
    field_limit = csv.field_size_limit()
    price_rows["Source notes"] = ""
    price_rows.loc[0, "Source notes"] = "x" * (field_limit + 1)
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 0

    assert csv.field_size_limit() == field_limit


def test_compare_accepts_gzip_prices(price_rows, tmp_path):
    prices = tmp_path / "compressed.csv.gz"
    output = tmp_path / "results"
    with gzip.open(prices, "wt", encoding="utf-8") as stream:
        price_rows.to_csv(stream, index=False)

    assert main(compare_args(prices, output)) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["daily_rows"] == 3


def test_compare_rejects_quoted_empty_csv_record(price_rows, tmp_path, capsys):
    prices = tmp_path / "quoted-empty.csv"
    output = tmp_path / "results"
    contents = price_rows.to_csv(index=False).replace("\n", '\n""\n', 1)
    prices.write_text(contents, encoding="utf-8")

    assert main(compare_args(prices, output)) == 1

    error = capsys.readouterr().err
    assert prices.name in error and "line 2" in error and "fields" in error
    assert not output.exists()


def test_development_archive_rejects_extra_unnamed_field(tmp_path):
    archive = tmp_path / "weights-e.csv"
    dates = pd.date_range("2008-01-01", "2015-12-01", freq="MS")
    prices = pd.DataFrame(100.0, index=dates, columns=STUDY_TICKERS)
    weights = pd.DataFrame(1 / 6, index=dates, columns=STUDY_TICKERS)
    weights["Cash"] = 0.0
    header, *rows = weights.to_csv(index_label="Execution Date").splitlines()
    archive.write_text("\n".join([header, *(f"extra,{row}" for row in rows)]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"weights-e\.csv.*line 2.*fields"):
        load_development_targets(archive, prices, "E")


@pytest.mark.parametrize(
    "problem,description",
    [
        ("absent", "missing"),
        ("blank", "missing"),
        ("invalid", "invalid"),
        ("duplicate", "duplicate"),
    ],
)
def test_compare_names_the_problem_price_file_date_and_ticker(
    price_rows, tmp_path, capsys, problem, description
):
    prices = tmp_path / "price-problem.csv"
    output = tmp_path / "results"
    selected = price_rows["Date"].eq("2024-01-04") & price_rows["Ticker"].eq("BBB")
    if problem == "absent":
        price_rows = price_rows.loc[~selected]
    elif problem == "duplicate":
        price_rows = pd.concat([price_rows, price_rows.loc[selected]])
    else:
        price_rows["Adj Close"] = price_rows["Adj Close"].astype(str)
        price_rows.loc[selected, "Adj Close"] = "" if problem == "blank" else "invalid value"
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 1

    error = capsys.readouterr().err
    assert description in error.lower()
    assert prices.name in error and "2024-01-04" in error and "BBB" in error
    assert not output.exists()


def test_compare_preserves_existing_output(price_rows, tmp_path, capsys):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.to_csv(prices, index=False)
    output.mkdir()
    existing = output / "performance.csv"
    existing.write_text("Previous report\n")

    assert main(compare_args(prices, output)) == 1

    assert "exists" in capsys.readouterr().err
    assert list(output.iterdir()) == [existing]
    assert existing.read_text() == "Previous report\n"


def test_price_coverage_is_required_only_during_warmup_and_run(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    outside = pd.DataFrame([
        [pd.Timestamp("2022-11-30"), "AAA", -1],
        [pd.Timestamp("2022-11-30"), "AAA", -1],
        [pd.Timestamp("2024-03-01"), "BBB", 0],
    ], columns=price_rows.columns)
    pd.concat([price_rows, outside]).to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["daily_rows"] == 3
    assert manifest["last_close"] == "2024-01-31"


def test_compare_accepts_a_literal_na_ticker(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.loc[price_rows["Ticker"].eq("AAA"), "Ticker"] = "NA"
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output, tickers=("NA",))) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["tickers"] == ["NA"]


def test_compare_reports_an_error_if_annualized_metrics_overflow(price_rows, tmp_path, capsys):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    january = price_rows["Date"].dt.to_period("M") == "2024-01"
    price_rows = price_rows.copy()
    price_rows.loc[price_rows["Date"].eq("2024-01-31"), "Date"] = pd.Timestamp("2024-01-05")
    price_rows.loc[january, "Adj Close"] = 100
    price_rows.loc[price_rows["Date"].eq("2024-01-05"), "Adj Close"] = 100_000
    price_rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output)) == 1

    assert "finite" in capsys.readouterr().err
    assert not output.exists()


def test_all_cash_momentum_stays_exactly_flat_for_large_basket(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    tickers = tuple(f"ETF{i}" for i in range(49))
    rows = pd.MultiIndex.from_product(
        [price_rows["Date"].unique(), tickers], names=["Date", "Ticker"]
    ).to_frame(index=False)
    rows["Adj Close"] = 100
    rows.to_csv(prices, index=False)

    assert main(compare_args(prices, output, tickers=tickers, end="2024-02")) == 0

    performance = pd.read_csv(output / "performance.csv").set_index("Strategy")
    assert performance.at["M", "Total Return"] == 0.0
    assert performance.at["M", "CAGR"] == 0.0
    assert performance.at["M", "Annualized Volatility"] == 0.0
    assert performance.at["M", "Max Drawdown"] == 0.0

def test_compare_records_matching_price_provenance(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    provenance = tmp_path / "provenance.json"
    price_rows.to_csv(prices, index=False)
    record = {
        "schema_version": 1,
        "input_kind": "historical",
        "source": {"provider": "Yahoo Finance", "price_field": "indicators.adjclose[0].adjclose"},
        "retrieved_at": "2026-11-12T12:00:00+00:00",
        "requested": {"tickers": list(TICKERS)},
        "normalized": {"path": "prices.csv", "sha256": sha256(prices.read_bytes()).hexdigest()},
    }
    provenance.write_text(json.dumps(record), encoding="utf-8")

    assert main([*compare_args(prices, output, kind="historical"), "--provenance", str(provenance)]) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["price_provenance"]["record"] == record
    assert manifest["price_provenance"]["file"]["sha256"] == sha256(provenance.read_bytes()).hexdigest()


@pytest.mark.parametrize("problem", ["wrong hash", "different input kind", "missing ticker"])
def test_compare_rejects_inconsistent_provenance(price_rows, tmp_path, capsys, problem):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    provenance = tmp_path / "provenance.json"
    price_rows.to_csv(prices, index=False)
    record = {
        "input_kind": "historical",
        "source": {"provider": "Yahoo Finance"},
        "retrieved_at": "2026-11-12T12:00:00+00:00",
        "requested": {"tickers": list(TICKERS)},
        "normalized": {"sha256": sha256(prices.read_bytes()).hexdigest()},
    }
    if problem == "wrong hash":
        record["normalized"]["sha256"] = "0" * 64
    elif problem == "different input kind":
        record["input_kind"] = "synthetic"
    else:
        record["requested"]["tickers"] = ["AAA", "BBB"]
    provenance.write_text(json.dumps(record), encoding="utf-8")

    assert main([*compare_args(prices, output, kind="historical"), "--provenance", str(provenance)]) == 1

    assert "provenance" in capsys.readouterr().err.lower()
    assert not output.exists()


def test_requested_fee_compares_both_strategies_against_same_10_bp_run(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.to_csv(prices, index=False)

    assert main([*compare_args(prices, output), "--fee-bp", "12.5"]) == 0

    comparison = pd.read_csv(output / "fee_comparison.csv").set_index(["Cost (bp)", "Strategy"])
    assert comparison.index.to_list() == [(10, "E"), (10, "M"), (12.5, "E"), (12.5, "M")]
    performance = pd.read_csv(output / "performance.csv").set_index("Strategy")
    assert comparison.loc[10, "CAGR"].to_list() == pytest.approx(performance["CAGR"])
    assert comparison.loc[10, "Max Drawdown"].to_list() == pytest.approx(performance["Max Drawdown"])
    for strategy, initial_sleeve, gross in [("E", 1, 1.05), ("M", 1 / 3, 1 + 0.1 / 3)]:
        row = comparison.loc[(12.5, strategy)]
        assert row["CAGR"] == pytest.approx((gross / (1 + 0.00125 * initial_sleeve)) ** (365.25 / 28) - 1)
        assert row["Max Drawdown"] == pytest.approx(1 / (1 + 0.00125 * initial_sleeve) - 1)
        assert row["Peak"] == row["Trough"] == "2024-01-03"
        assert row["Recovery"] == "2024-01-04"
        assert row["Recovery Status"] == "recovered"
        assert row["Trough to Recovery (days)"] == row["Peak to Recovery (days)"] == 1

    sensitivity = pd.read_csv(output / "fee_sensitivity.csv")
    assert sensitivity["Cost (bp)"].to_list() == [0, 5, 10, 12.5, 20]
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["main_cost_bp"] == 10
    assert manifest["requested_cost_bp"] == 12.5
    assert manifest["costs_bp"] == [0, 5, 10, 12.5, 20]
    assert manifest["inputs"]["prices"]["sha256"] == sha256(prices.read_bytes()).hexdigest()


@pytest.mark.parametrize("bad_fee", ["-1", "inf", "nan", "10000", "none"])
def test_invalid_fee_is_rejected_without_publishing_results(price_rows, tmp_path, bad_fee, capsys):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.to_csv(prices, index=False)

    with pytest.raises(SystemExit) as exc:
        main([*compare_args(prices, output), f"--fee-bp={bad_fee}"])
    assert exc.value.code == 2

    assert "fee" in capsys.readouterr().err.lower()
    assert not output.exists()


def test_fee_comparison_distinguishes_unrecovered_from_no_drawdown(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.loc[:, "Adj Close"] = 100
    price_rows.loc[(price_rows["Date"].eq("2024-01-31")) & (price_rows["Ticker"].eq("AAA")), "Adj Close"] = 50
    price_rows.to_csv(prices, index=False)

    assert main([*compare_args(prices, output), "--fee-bp", "0"]) == 0

    events = pd.read_csv(output / "drawdown_events.csv", keep_default_na=False).set_index("Strategy")
    assert events.at["E", "Recovery Status"] == "unrecovered"
    assert events.at["M", "Recovery Status"] == "no_drawdown"
    for column in ["Recovery", "Trough to Recovery (days)", "Peak to Recovery (days)"]:
        assert events.at["E", column] == events.at["M", column] == ""
    assert events.at["M", "Peak"] == events.at["M", "Trough"] == ""


def test_high_valid_fee_uses_the_specified_fee_on_exact_initial_purchase(price_rows, tmp_path):
    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    price_rows.to_csv(prices, index=False)

    assert main([*compare_args(prices, output), "--fee-bp", "2500"]) == 0

    comparison = pd.read_csv(output / "fee_comparison.csv").set_index(["Cost (bp)", "Strategy"])
    equal = comparison.loc[(2500, "E")]
    assert equal["CAGR"] == pytest.approx((1.05 / 1.25) ** (365.25 / 28) - 1)
    assert equal["Max Drawdown"] == pytest.approx(1 / 1.25 - 1)


def test_strict_historical_manifest_distinguishes_required_prices_from_rebalance_window(tmp_path):
    import exchange_calendars

    prices = tmp_path / "prices.csv"
    output = tmp_path / "results"
    sessions = exchange_calendars.get_calendar("XNYS", start="2014-12-01", end="2016-01-31").sessions
    pd.DataFrame({"Date": sessions, "Ticker": "SPY", "Adj Close": 100}).to_csv(prices, index=False)

    assert main([
        *compare_args(prices, output, tickers=("SPY",), start="2016-01", end="2016-01", kind="historical"),
        "--verify-us-calendar",
    ]) == 0

    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["currency"] == "USD"
    assert manifest["trading_calendar"] == "XNYS"
    assert manifest["packages"]["exchange_calendars"] == exchange_calendars.__version__
    assert manifest["required_price_first_close"] == "2014-12-31"
    assert manifest["required_price_last_close"] == "2016-01-29"
    assert manifest["first_close"] == "2016-01-04"
    assert manifest["last_close"] == "2016-01-29"
