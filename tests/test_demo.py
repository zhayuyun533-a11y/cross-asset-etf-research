import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"


def run_demo(cwd, directory):
    return subprocess.run(
        [sys.executable, "-B", "-m", "etf_backtest.demo", directory],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SOURCE)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_demo_writes_a_deterministic_synthetic_comparison_from_another_directory(tmp_path):
    result = run_demo(tmp_path, "demo")
    assert result.returncode == 0, result.stderr

    demo_dir = tmp_path / "demo"
    assert {path.name for path in demo_dir.iterdir()} == {"prices.csv", "results"}
    price_file = demo_dir / "prices.csv"
    results = demo_dir / "results"
    assert {path.name for path in results.iterdir()} == {
        "performance.csv",
        "fee_sensitivity.csv",
        "drawdowns.csv",
        "drawdown_events.csv",
        "run_manifest.json",
    }
    manifest = json.loads((results / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_kind"] == "synthetic"
    assert manifest["tickers"] == ["DEMOETF1", "DEMOETF2", "DEMOETF3"]
    assert manifest["requested_start_month"] == "2023-01"
    assert manifest["requested_end_month"] == "2024-12"
    assert manifest["inputs"]["prices"]["sha256"] == hashlib.sha256(
        price_file.read_bytes()
    ).hexdigest()

    values_by_ticker = {}
    with price_file.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == ["Date", "Ticker", "Adj Close"]
        for row in reader:
            price = float(row["Adj Close"])
            assert math.isfinite(price) and price > 0
            values_by_ticker.setdefault(row["Ticker"], set()).add(price)
    assert set(values_by_ticker) == set(manifest["tickers"])
    assert all(len(values) > 1 for values in values_by_ticker.values())

    duplicate = run_demo(tmp_path, "demo-again")
    assert duplicate.returncode == 0, duplicate.stderr
    assert (tmp_path / "demo-again" / "prices.csv").read_bytes() == price_file.read_bytes()


@pytest.mark.parametrize("has_file", [False, True])
def test_demo_preserves_an_existing_directory(tmp_path, has_file):
    directory = tmp_path / "demo"
    directory.mkdir()
    prior_file = directory / "prices.csv"
    if has_file:
        prior_file.write_text("Existing data\n", encoding="utf-8")

    result = run_demo(tmp_path, "demo")

    assert result.returncode != 0
    assert "exists" in result.stderr.lower()
    assert list(directory.iterdir()) == ([prior_file] if has_file else [])
    if has_file:
        assert prior_file.read_text(encoding="utf-8") == "Existing data\n"


def test_demo_removes_its_prices_if_the_comparison_fails(tmp_path, monkeypatch):
    from etf_backtest import cli, demo

    monkeypatch.setattr(cli, "main", lambda _args: 1)
    directory = tmp_path / "failed-demo"

    assert demo.main([str(directory)]) == 1
    assert not directory.exists()
