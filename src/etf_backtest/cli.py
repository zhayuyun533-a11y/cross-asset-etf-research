"""Run the historical replay or price-only comparison from local CSVs."""

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from . import __version__
from .compare import _parse_month, prepare_comparison
from .core import (
    FULL_SAMPLE,
    INITIAL_CAPITAL,
    LOOKBACK_MONTHS,
    PERIODS,
    TICKERS,
    build_targets,
    drawdown_for_window,
    load_development_targets,
    load_prices,
    run_backtest,
    summarize_window,
)

COSTS_BP = (0, 5, 10, 20)
MAIN_COST_BP = 10


def _fee_bp(value):
    try:
        rate = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Fee must be a number in basis points") from error
    if not math.isfinite(rate) or not 0 <= rate < 10_000:
        raise ValueError("Fee must be finite and at least 0 but less than 10000 basis points")
    return rate


def _fee_argument(value):
    try:
        return _fee_bp(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def input_record(path):
    return {"name": path.name, "sha256": sha256(path.read_bytes()).hexdigest()}


def recovery_events(main_paths, period="holdout"):
    rows = []
    for strategy, path in main_paths.items():
        drawdown = drawdown_for_window(path, period)
        nav = path.loc[drawdown.index, "Close Value"]
        trough = drawdown.idxmin()
        max_drawdown = float(drawdown.at[trough])
        if period == FULL_SAMPLE and max_drawdown == 0:
            rows.append({
                "Strategy": strategy,
                "Peak": "",
                "Trough": "",
                "Recovery": "",
                "Max Drawdown": max_drawdown,
                "Peak Is Initial Capital": False,
                "Recovery Status": "no_drawdown",
                "Trough to Recovery (days)": None,
                "Peak to Recovery (days)": None,
            })
            continue

        peak = nav.loc[:trough].idxmax()
        peak_value = nav.at[peak]
        initial_peak = period == FULL_SAMPLE and peak_value < INITIAL_CAPITAL
        if initial_peak:
            peak = nav.index[0]
            peak_value = INITIAL_CAPITAL
        elif period == FULL_SAMPLE:
            # A decline after a repeated high begins at the last such high.
            before_trough = nav.loc[:trough]
            peak = before_trough.index[before_trough.eq(peak_value)][-1]
        recovered = nav.loc[trough:]
        recovered = recovered[recovered >= peak_value]
        recovery = recovered.index.min()
        row = {
            "Strategy": strategy,
            "Peak": peak.date().isoformat(),
            "Trough": trough.date().isoformat(),
            "Recovery": recovery.date().isoformat() if pd.notna(recovery) else "",
            "Max Drawdown": max_drawdown,
        }
        if period == FULL_SAMPLE:
            has_recovered = pd.notna(recovery)
            row.update({
                "Peak Is Initial Capital": bool(initial_peak),
                "Recovery Status": "recovered" if has_recovered else "unrecovered",
                "Trough to Recovery (days)": (recovery - trough).days if has_recovered else None,
                "Peak to Recovery (days)": (recovery - peak).days if has_recovered else None,
            })
        rows.append(row)
    events = pd.DataFrame(rows)
    if period == FULL_SAMPLE:
        duration_columns = ["Trough to Recovery (days)", "Peak to Recovery (days)"]
        events[duration_columns] = events[duration_columns].astype("Int64")
    return events


def _calculate_scenarios(run_prices, targets, periods, tickers=TICKERS, *, requested_fee=None):
    performance_rows = []
    sensitivity_rows = []
    reported_paths = {MAIN_COST_BP: {}}
    if requested_fee is not None:
        reported_paths.setdefault(requested_fee, {})
    rates = sorted(set(COSTS_BP).union(reported_paths))
    for bp in rates:
        scenario = {}
        for strategy, weights in targets.items():
            result = run_backtest(run_prices, weights, INITIAL_CAPITAL, bp / 10_000, tickers=tickers)
            scenario[strategy] = {
                period: summarize_window(result, period) for period in periods
            }
            if bp in reported_paths:
                reported_paths[bp][strategy] = result
            if bp == MAIN_COST_BP:
                for period, metrics in scenario[strategy].items():
                    performance_rows.append({
                        "Period": period, "Strategy": strategy, **metrics.to_dict()
                    })
        for period in periods:
            e_cagr = scenario["E"][period]["CAGR"]
            m_cagr = scenario["M"][period]["CAGR"]
            sensitivity_rows.append({
                "Period": period, "Cost (bp)": bp,
                "E": e_cagr, "M": m_cagr, "M - E": m_cagr - e_cagr,
            })

    return pd.DataFrame(performance_rows), pd.DataFrame(sensitivity_rows), reported_paths


def _run_metadata(protocol, input_kind, fingerprints, run_prices, targets, tickers):
    return {
        "protocol": protocol,
        "software_version": __version__,
        "input_kind": input_kind,
        "inputs": fingerprints,
        "python": platform.python_version(),
        "packages": {"pandas": pd.__version__, "numpy": np.__version__},
        "tickers": list(tickers),
        "initial_capital": INITIAL_CAPITAL,
        "main_cost_bp": MAIN_COST_BP,
        "costs_bp": list(COSTS_BP),
        "cash_interest": 0,
        "first_close": run_prices.index[0].date().isoformat(),
        "last_close": run_prices.index[-1].date().isoformat(),
        "daily_rows": len(run_prices),
        "execution_dates": {strategy: len(weights) for strategy, weights in targets.items()},
    }


def _drawdowns(main_paths, period):
    return pd.DataFrame({
        strategy: drawdown_for_window(path, period) for strategy, path in main_paths.items()
    }).rename_axis("Date")


def _check_stable_inputs(paths, fingerprints):
    if fingerprints != {key: input_record(path) for key, path in paths.items()}:
        raise ValueError("An input file changed while it was being read. Use stable snapshots.")


def calculate(prices_path, dev_e_path, dev_m_path, input_kind):
    paths = {"prices": prices_path, "dev_e": dev_e_path, "dev_m": dev_m_path}
    fingerprints = {key: input_record(path) for key, path in paths.items()}
    prices = load_prices(prices_path)
    development_e = load_development_targets(dev_e_path, prices, "E")
    development_m = load_development_targets(dev_m_path, prices, "M")
    targets = build_targets(prices, development_e, development_m)
    run_prices = prices.loc["2008":"2025"]
    performance, sensitivity, paths_by_fee = _calculate_scenarios(run_prices, targets, PERIODS)
    main_paths = paths_by_fee[MAIN_COST_BP]
    _check_stable_inputs(paths, fingerprints)
    manifest = _run_metadata(
        "six-etf-em-2008-2025-v1", input_kind, fingerprints, run_prices, targets, TICKERS
    )
    manifest["periods"] = {name: list(years) for name, years in PERIODS.items()}
    manifest["metric_units"] = "decimal fractions"
    tables = {
        "performance.csv": performance,
        "fee_sensitivity.csv": sensitivity,
        "holdout_drawdowns.csv": _drawdowns(main_paths, "holdout"),
        "holdout_drawdown_events.csv": recovery_events(main_paths),
    }
    return tables, manifest


def _read_price_provenance(path, price_hash, tickers, input_kind):
    fingerprint = input_record(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    try:
        expected_hash = record["normalized"]["sha256"]
        declared_kind = record["input_kind"]
        declared_tickers = record["requested"]["tickers"]
        provider = record["source"]["provider"]
        retrieved_at = record["retrieved_at"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Price provenance {path} is missing required source fields") from error
    if expected_hash != price_hash:
        raise ValueError(f"Price provenance {path} does not match the supplied price file hash")
    if declared_kind != input_kind:
        raise ValueError(f"Price provenance {path} conflicts with --input-kind")
    if (
        not isinstance(declared_tickers, list)
        or not all(isinstance(ticker, str) for ticker in declared_tickers)
        or not set(tickers).issubset(declared_tickers)
    ):
        raise ValueError(f"Price provenance {path} does not cover all selected tickers")
    if not isinstance(provider, str) or not provider or not isinstance(retrieved_at, str) or not retrieved_at:
        raise ValueError(f"Price provenance {path} needs its provider and retrieval time")
    return {"file": fingerprint, "record": record}


def calculate_comparison(
    prices_path, tickers, start, end, input_kind, provenance_path=None, *,
    requested_fee=None, verify_us_calendar=False,
):
    if requested_fee is not None:
        requested_fee = _fee_bp(requested_fee)
    if verify_us_calendar and input_kind != "historical":
        raise ValueError("--verify-us-calendar applies only to historical input")
    paths = {"prices": prices_path}
    fingerprints = {"prices": input_record(prices_path)}
    provenance = None
    if provenance_path is not None:
        provenance = _read_price_provenance(
            provenance_path, fingerprints["prices"]["sha256"], tickers, input_kind
        )
    if verify_us_calendar:
        from .coverage import CALENDAR_NAME, CALENDAR_VERSION, expected_sessions

        run_prices, targets = prepare_comparison(
            prices_path, tickers, start, end, verify_us_calendar=True,
        )
    else:
        run_prices, targets = prepare_comparison(prices_path, tickers, start, end)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        performance, sensitivity, paths_by_fee = _calculate_scenarios(
            run_prices, targets, (FULL_SAMPLE,), tickers, requested_fee=requested_fee,
        )
    main_paths = paths_by_fee[MAIN_COST_BP]
    _check_stable_inputs(paths, fingerprints)
    if provenance is not None and input_record(provenance_path) != provenance["file"]:
        raise ValueError("Price provenance changed during the run. Use a stable snapshot.")
    manifest = {
        "mode": "compare",
        **_run_metadata(
            "price-only-em-v1", input_kind, fingerprints, run_prices, targets, tickers
        ),
        "period": FULL_SAMPLE,
        "requested_start_month": start,
        "requested_end_month": end,
        "lookback_months": LOOKBACK_MONTHS,
        "momentum_threshold": 0,
        "sleeve_weight": 1 / len(tickers),
        "strategy_rules": {
            "E": "All selected ETFs receive one equal sleeve.",
            "M": "Only strictly positive momentum receives one equal sleeve. Other sleeves stay in cash.",
        },
        "signal": "Prior month's last observed adjusted close versus the adjusted close 12 months earlier.",
        "execution": "Next observed close, after existing holdings earn the execution session's return.",
        "transaction_cost": "Fee on buys plus sells of ETF positions, sized using post-fee NAV.",
        "volatility": "Daily sample standard deviation excluding the fee-only first return, scaled by sqrt(252).",
        "fee_sensitivity_metric": "CAGR",
        "fee_sensitivity_difference": "M CAGR minus E CAGR, in decimal fractions",
        "metric_units": "decimal fractions",
    }
    if provenance is not None:
        manifest["price_provenance"] = provenance
    if input_kind == "historical":
        manifest["currency"] = "USD"
    if verify_us_calendar:
        warmup_month = _parse_month(start, "--start") - LOOKBACK_MONTHS - 1
        sessions = expected_sessions(warmup_month, _parse_month(end, "--end"))
        manifest["trading_calendar"] = CALENDAR_NAME
        manifest["packages"]["exchange_calendars"] = CALENDAR_VERSION
        manifest["required_price_first_close"] = sessions[0].date().isoformat()
        manifest["required_price_last_close"] = sessions[-1].date().isoformat()
    tables = {
        "performance.csv": performance,
        "fee_sensitivity.csv": sensitivity,
        "drawdowns.csv": _drawdowns(main_paths, FULL_SAMPLE),
        "drawdown_events.csv": recovery_events(main_paths, FULL_SAMPLE),
    }
    if requested_fee is not None:
        manifest["requested_cost_bp"] = requested_fee
        manifest["costs_bp"] = sensitivity["Cost (bp)"].tolist()
        summary = sensitivity.set_index("Cost (bp)")
        comparisons = []
        for bp, paths_for_rate in paths_by_fee.items():
            events = recovery_events(paths_for_rate, FULL_SAMPLE)
            events.insert(0, "Cost (bp)", bp)
            events.insert(2, "CAGR", events["Strategy"].map(summary.loc[bp, ["E", "M"]]))
            comparisons.append(events)
        tables["fee_comparison.csv"] = pd.concat(comparisons, ignore_index=True)
    return tables, manifest


def write_outputs(output_dir, tables, manifest):
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".etf-run-", dir=output_dir.parent) as work:
        staged = Path(work)
        for name, table in tables.items():
            table.to_csv(staged / name, index=table.index.name == "Date")
        (staged / "run_manifest.json").write_text(
            json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        output_dir.mkdir()
        written = []
        try:
            for source in staged.iterdir():
                destination = output_dir / source.name
                source.replace(destination)
                written.append(destination)
        except OSError:
            for destination in written:
                destination.unlink(missing_ok=True)
            output_dir.rmdir()
            raise


def print_comparison_summary(output_dir, tables, manifest):
    metrics = ["Total Return", "CAGR", "Annualized Volatility", "Max Drawdown"]
    performance = tables["performance.csv"]
    summary = performance.loc[
        performance["Period"].eq(manifest["period"]), ["Strategy", *metrics]
    ]
    print(
        f"E/M comparison ({manifest['input_kind']} input; "
        f"main fee {manifest['main_cost_bp']} bp)"
    )
    if "currency" in manifest:
        print(f"Currency: {manifest['currency']}")
    print(f"Actual closes: {manifest['first_close']} to {manifest['last_close']}")
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.2%}"))
    if "requested_cost_bp" in manifest:
        print(f"Requested fee: {manifest['requested_cost_bp']:g} bp per buy or sell (fee_comparison.csv)")
    print(f"Reports: {output_dir.resolve()}")
    print("CSV metrics are decimal fractions.")


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    compare = arguments[:1] == ["compare"]
    if compare:
        parser = argparse.ArgumentParser(
            prog="etf-backtest compare",
            description="Compare equal weight and 12-month momentum from local adjusted closes.",
            epilog=(
                "Supply every warmup and run month for the selected ETFs. "
                "For --start 2024-01, warmup begins in 2022-12: the first signal "
                "compares December 2023 with December 2022."
            ),
        )
    else:
        parser = argparse.ArgumentParser(
            description="Replay the fixed six ETF E/M study from local CSVs.",
            epilog=(
                "For a price-only comparison, see python -m etf_backtest compare --help. "
                "For a synthetic demonstration, see python -m etf_backtest.demo --help."
            ),
        )
    parser.add_argument(
        "--prices", type=Path, required=True,
        help="Local CSV with columns Date, Ticker, Adj Close; dates use YYYY-MM-DD.",
    )
    if compare:
        parser.add_argument("--tickers", nargs="+", required=True, help="Distinct ticker symbols in the CSV.")
        parser.add_argument("--start", required=True, metavar="YYYY-MM", help="First month, inclusive.")
        parser.add_argument("--end", required=True, metavar="YYYY-MM", help="Last month, inclusive.")
        parser.add_argument(
            "--provenance", type=Path,
            help="Optional download provenance JSON matching the prices file hash.",
        )
        parser.add_argument(
            "--fee-bp", type=_fee_argument, metavar="BASIS_POINTS",
            help="Additional per-side trading fee to compare with 10 bp, e.g. 25 for 0.25%%.",
        )
        parser.add_argument(
            "--verify-us-calendar", action="store_true",
            help="Check historical coverage against the US equity session calendar.",
        )
    else:
        parser.add_argument("--dev-e", type=Path, required=True)
        parser.add_argument("--dev-m", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="New report directory; must not already exist.",
    )
    parser.add_argument(
        "--input-kind", choices=("synthetic", "historical"), required=True,
        help=(
            "Label the supplied data: synthetic means fictional/generated prices; "
            "historical means observed market prices."
        ),
    )
    args = parser.parse_args(arguments[1:] if compare else arguments)
    try:
        if args.output_dir.exists():
            raise FileExistsError(f"Output directory already exists: {args.output_dir}")
        if compare:
            tables, manifest = calculate_comparison(
                args.prices, tuple(args.tickers), args.start, args.end, args.input_kind,
                args.provenance, requested_fee=args.fee_bp, verify_us_calendar=args.verify_us_calendar,
            )
        else:
            tables, manifest = calculate(args.prices, args.dev_e, args.dev_m, args.input_kind)
        write_outputs(args.output_dir, tables, manifest)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"etf-backtest: {error}", file=sys.stderr)
        return 1
    if compare:
        print_comparison_summary(args.output_dir, tables, manifest)
    else:
        print(f"Saved {args.input_kind} results to {args.output_dir}")
        print("Metrics are decimal fractions. Main fee is 10 bp.")
    return 0
