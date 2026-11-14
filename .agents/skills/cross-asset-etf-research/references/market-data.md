# Historical price data

Use daily USD adjusted closes for US-listed ETFs on a shared trading calendar. Prefer the user's verifiable source when supplied and keep the CSV unchanged. If the user provides only symbols and dates, default to Yahoo Finance.

```bash
"<environment-python>" -m etf_backtest.market_data \
  --tickers SPY IEF GLD --start 2016-01 --end 2020-12 \
  --output-dir "local-data/example"
```

The helper requests the lookback history along with the test window, preserves each raw chart response and writes `prices.csv` and `provenance.json`. Use a fresh path. Read the provenance for the provider, actual price field, retrieval time, per-symbol coverage and file hashes. Use `--verify-us-calendar` for historical comparison whether using a downloaded or provided CSV. This checks dates against the NYSE trading calendar, including dates missing for every ticker. Verify that the selected ETF listings use that calendar. Refer to [adjusted close on Yahoo Finance](https://help.yahoo.com/kb/SLN28256.html).

## User-provided data

Verify the declared provider, adjusted-close field, the selected US-listed symbols and their USD currency. Ask the user for the specific missing source detail if the CSV does not establish it. Freeze a copy of the file in the run's local data directory. Preserve the provider's source record if it matches the CSV. When recording source details supplied by the user, keep the source name and link or export reference, retrieval time if known, declared symbols, USD currency, coverage and the CSV SHA-256. Mark attestations as user-supplied. For the comparison's `--provenance` argument, the compatible JSON keys are `input_kind: "historical"`, `source.provider`, `retrieved_at`, `requested.tickers`, and `normalized.sha256`. If the retrieval date is unknown, record it as unknown and do not present the time of this run as a retrieval date.

## Reject or shorten an invalid window

Keep the requested end month, the entire basket and the original price series. A valid start needs the actual month-end prices for its first signal and 12-month-ago comparison endpoint, then complete verified coverage through the specified end month. An ETF listed partway through the earliest lookback month can still supply that month's actual month-end price. Report missing ticker dates or ranges, duplicates and invalid prices. Keep a truncated or in-progress last month pending until the required closes are available or the user changes the end date.

When a shorter common period can be verified, explain the earliest candidate and why the original first signal could not be formed. Get the user's confirmation before running a narrower window. On a provider download or verification failure, give the specific diagnostic and ask for a verifiable CSV. Use the original frozen prices and provenance for a fee-only rerun. Keep local price snapshots and raw responses in the user's working data directory so published examples can cite sources without repackaging their price files.
