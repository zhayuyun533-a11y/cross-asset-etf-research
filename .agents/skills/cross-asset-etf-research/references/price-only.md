# Compare a new ETF basket

Use one daily adjusted-close CSV with `Date`, `Ticker` and `Adj Close`, with ISO dates and finite positive prices. Each ETF needs one record per required session. For historical data, confirm US listings and USD quotes from the source and preserve the price snapshot and provenance. See [market data](market-data.md).

```bash
"<environment-python>" -m etf_backtest compare \
  --prices "local-data/example/prices.csv" \
  --provenance "local-data/example/provenance.json" \
  --tickers SPY IEF GLD --start 2016-01 --end 2020-12 \
  --input-kind historical --verify-us-calendar --output-dir "runs/three-etfs"
```

Use the exact Python path from [runtime setup](runtime.md). User-defined start and end months are inclusive. A January 2016 start needs December 2014 month-end data as the first lookback endpoint. The first signal compares December 2015 with December 2014. Use the exchange's actual last session for each month. The strategy starts from cash and executes on the first common close of the comparison window. Old holdings earn the move into later execution closes. E assigns `1/N` to each ETF. M assigns `1/N` only if momentum is strictly positive and keeps the remainder in cash at zero interest.

Fees are charged on each ETF buy and sell at the chosen rate times traded notional. The standard comparison shows the main scenario at 10 bp per side and calculates sensitivity at 0, 5, 10 and 20 bp. Total return includes the entry fee and volatility excludes the initial fee-only observation.

## Rerun with a different fee

Use the same price CSV and provenance, match their hashes to the prior manifest, and pass the same dates, tickers and calendar option. Use a fresh output folder.

```bash
"<environment-python>" -m etf_backtest compare \
  --prices "local-data/example/prices.csv" \
  --provenance "local-data/example/provenance.json" \
  --tickers SPY IEF GLD --start 2016-01 --end 2020-12 \
  --input-kind historical --verify-us-calendar --fee-bp 25 \
  --output-dir "runs/three-etfs-25bp"
```

`performance.csv` contains net total and annualized return, annualized volatility and maximum drawdown at 10 bp. In `fee_sensitivity.csv`, E and M are net CAGR and M − E is their difference. Convert decimal fractions to percentages and differences to percentage points. `drawdowns.csv` and `drawdown_events.csv` show daily drawdown and main-scenario events. A requested rate produces `fee_comparison.csv`, with fee, strategy, CAGR, maximum drawdown and recovery event for each base and requested rate.

The event is measured from each strategy's own peak and maximum drawdown trough. Recovery is the first subsequent close that reaches or exceeds that peak. The pre-fee starting cash can count as a peak. Compare both recovery durations in calendar days. Read the recovery status before interpreting blanks or zero values. `run_manifest.json` records the settings, dates, software versions and price hash for a replay.
