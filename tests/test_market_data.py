import hashlib
import io
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

import exchange_calendars
import pytest

from etf_backtest import market_data


def chart(symbol="SPY", currency="USD", days=None):
    if days is None:
        calendar = exchange_calendars.get_calendar("XNYS", start="2024-12-31", end="2026-01-31")
        days = tuple(day.date().isoformat() for day in calendar.sessions)
    timestamps = [int(datetime.fromisoformat(day + "T14:30:00+00:00").timestamp()) for day in days]
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "instrumentType": "ETF", "currency": currency,
                 "exchangeTimezoneName": "America/New_York", "exchangeName": "PCX", "firstTradeDate": 728317800},
        "timestamp": timestamps,
        "indicators": {"quote": [{"close": [901.0 + index for index in range(len(days))]}],
                       "adjclose": [{"adjclose": [101.25, 102.5] + [103.0 + index for index in range(len(days) - 2)]}]},
    }]}}


def encode(payload):
    return json.dumps(payload, indent=2).encode()


def respond(monkeypatch, payloads):
    def opener(request, timeout):
        symbol = request.full_url.split("/chart/")[1].split("?")[0]
        return io.BytesIO(payloads[symbol])
    monkeypatch.setattr(market_data, "urlopen", opener)


def test_download_preserves_raw_bytes_actual_adjclose_and_provenance(tmp_path, monkeypatch):
    raw = encode(chart()) + b"\n"
    respond(monkeypatch, {"SPY": raw})
    output = tmp_path / "download"
    market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert (output / "raw/SPY.json").read_bytes() == raw
    prices = (output / "prices.csv").read_bytes()
    assert prices.decode().splitlines()[:3] == [
        "Date,Ticker,Adj Close", "2024-12-31,SPY,101.25", "2025-01-02,SPY,102.5"]
    assert prices.decode().splitlines()[-1] == "2026-01-30,SPY,371.0"
    assert len(prices.decode().splitlines()) == 272
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["schema_version"] == 1
    assert provenance["trading_calendar"] == {"name": "XNYS", "version": exchange_calendars.__version__}
    assert provenance["normalized"]["sha256"] == hashlib.sha256(prices).hexdigest()
    assert provenance["requested"]["warmup_start"] == "2024-12"
    source = provenance["raw_files"][0]
    assert source["sha256"] == hashlib.sha256(raw).hexdigest()
    assert source["coverage"] == {"first_date": "2024-12-31", "last_date": "2026-01-30", "observations": 271}
    assert source["currency"] == "USD"
    assert source["exchange_timezone"] == "America/New_York"
    assert source["request_url"].startswith("https://query1.finance.yahoo.com/v8/finance/chart/SPY?")
    assert "includeAdjustedClose=true" in source["request_url"]
    assert datetime.fromisoformat(source["retrieved_at"].replace("Z", "+00:00")).tzinfo == timezone.utc


@pytest.mark.parametrize("mutation, message", [
    (lambda result: result["indicators"].pop("adjclose"), "adjusted close"),
    (lambda result: result["meta"].update(currency="EUR"), "USD"),
    (lambda result: result["meta"].update(symbol="QQQ"), "symbol"),
    (lambda result: result["meta"].update(instrumentType="EQUITY"), "ETF"),
    (lambda result: result["indicators"]["adjclose"][0]["adjclose"].__setitem__(0, None), "positive"),
    (lambda result: result["indicators"]["adjclose"][0]["adjclose"].__setitem__(0, 0), "positive"),
    (lambda result: result["indicators"]["adjclose"][0]["adjclose"].__setitem__(0, float("inf")), "finite"),
    (lambda result: result["timestamp"].__setitem__(1, result["timestamp"][0]), "Duplicate"),
])
def test_invalid_provider_data_fails_without_partial_output(tmp_path, monkeypatch, mutation, message):
    payload = chart()
    mutation(payload["chart"]["result"][0])
    respond(monkeypatch, {"SPY": encode(payload)})
    output = tmp_path / "download"
    with pytest.raises(ValueError, match=message):
        market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_rejects_different_trading_calendars(tmp_path, monkeypatch):
    respond(monkeypatch, {"SPY": encode(chart()), "QQQ": encode(chart("QQQ", days=("2024-12-31", "2025-01-03")))})
    output = tmp_path / "download"
    with pytest.raises(ValueError, match="calendar"):
        market_data.download_data(["SPY", "QQQ"], "2026-01", "2026-01", output)
    assert not output.exists()


def test_uses_exchange_local_date(tmp_path, monkeypatch):
    payload = chart()
    payload["chart"]["result"][0]["timestamp"][0] = int(datetime.fromisoformat("2025-01-01T01:00:00+00:00").timestamp())
    respond(monkeypatch, {"SPY": encode(payload)})
    output = tmp_path / "download"
    market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert (output / "prices.csv").read_text().splitlines()[1].startswith("2024-12-31,")


@pytest.mark.parametrize("code,expected_attempts", [(429, 2), (503, 2), (404, 1)])
def test_http_failure_has_bounded_retries_and_no_partial_output(tmp_path, monkeypatch, code, expected_attempts):
    attempts = []
    def opener(request, timeout):
        attempts.append(request.full_url)
        raise HTTPError(request.full_url, code, "provider unavailable", {}, None)
    monkeypatch.setattr(market_data, "urlopen", opener)
    monkeypatch.setattr(market_data.time, "sleep", lambda delay: None)
    output = tmp_path / "download"
    with pytest.raises(ValueError, match=f"SPY.*HTTP {code}"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert len(attempts) == expected_attempts
    assert not output.exists()


def test_existing_output_is_untouched(tmp_path, monkeypatch):
    output = tmp_path / "download"
    output.mkdir()
    (output / "keep.txt").write_text("keep me")
    def opener(request, timeout):
        pytest.fail("Existing output must be rejected before network access")
    monkeypatch.setattr(market_data, "urlopen", opener)
    with pytest.raises(ValueError, match="already exists"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert (output / "keep.txt").read_text() == "keep me"


@pytest.mark.parametrize("payload", [b"{", b'{"chart": null}', b'{"chart":{"result":[{"meta":null,"timestamp":[]}]}}'])
def test_malformed_response_fails_informatively(tmp_path, monkeypatch, payload):
    respond(monkeypatch, {"SPY": payload})
    with pytest.raises(ValueError, match="SPY.*malformed"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", tmp_path / "download")
    assert list(tmp_path.iterdir()) == []


def test_transient_http_recovers_and_preserves_only_successful_response(tmp_path, monkeypatch):
    raw = encode(chart())
    attempts = []
    def opener(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 503, "retry", {}, None)
        return io.BytesIO(raw)
    monkeypatch.setattr(market_data, "urlopen", opener)
    monkeypatch.setattr(market_data.time, "sleep", lambda delay: None)
    output = tmp_path / "download"
    market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert (output / "raw/SPY.json").read_bytes() == raw


def test_non_http_network_failure_is_not_retried(tmp_path, monkeypatch):
    attempts = []
    def opener(request, timeout):
        attempts.append(request.full_url)
        raise URLError("certificate verification failed")
    monkeypatch.setattr(market_data, "urlopen", opener)
    with pytest.raises(ValueError, match="SPY.*certificate verification"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", tmp_path / "download")
    assert len(attempts) == 1
    assert list(tmp_path.iterdir()) == []


def test_normalized_csv_sorts_symbols(tmp_path, monkeypatch):
    respond(monkeypatch, {"SPY": encode(chart()), "QQQ": encode(chart("QQQ"))})
    output = tmp_path / "download"
    market_data.download_data(["SPY", "QQQ"], "2026-01", "2026-01", output)
    assert (output / "prices.csv").read_text().splitlines()[1:3] == [
        "2024-12-31,QQQ,101.25", "2024-12-31,SPY,101.25"]


def test_concurrent_output_creation_preserves_winning_directory(tmp_path, monkeypatch):
    respond(monkeypatch, {"SPY": encode(chart())})
    output = tmp_path / "download"
    original_mkdir = market_data.Path.mkdir
    winner = []
    def racing_mkdir(path, *args, **kwargs):
        if path == output:
            original_mkdir(path)
            winner.append(path.stat().st_ino)
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(market_data.Path, "mkdir", racing_mkdir)
    with pytest.raises(ValueError, match="already exists"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", output)
    assert output.stat().st_ino == winner[0]
    assert list(output.iterdir()) == []


def test_publication_failure_rolls_back_only_owned_output(tmp_path, monkeypatch):
    respond(monkeypatch, {"SPY": encode(chart())})
    original_rename = market_data.os.rename
    def failing_rename(source, destination):
        if destination.name == "prices.csv":
            raise OSError("simulated publication failure")
        return original_rename(source, destination)
    monkeypatch.setattr(market_data.os, "rename", failing_rename)
    with pytest.raises(OSError, match="simulated publication failure"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", tmp_path / "download")
    assert list(tmp_path.iterdir()) == []


def test_missing_required_warmup_session_rejects_download(tmp_path, monkeypatch):
    payload = chart()
    result = payload["chart"]["result"][0]
    result["timestamp"].pop(2)
    result["indicators"]["adjclose"][0]["adjclose"].pop(2)
    respond(monkeypatch, {"SPY": encode(payload)})
    with pytest.raises(ValueError, match=r"SPY: missing.*2025-01-03"):
        market_data.download_data(["SPY"], "2026-01", "2026-01", tmp_path / "download")
    assert list(tmp_path.iterdir()) == []
