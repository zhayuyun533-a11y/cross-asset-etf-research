---
name: cross-asset-etf-research
description: Use when comparing historical monthly equal-weight and 12-month absolute-momentum strategies for US-listed USD ETFs, changing a prior comparison's trading fee, checking its recovery from drawdown, or running the bundled synthetic demo.
---

# 跨资产 ETF 策略研究助手

Compare two historical strategies with the bundled Python engine. E rebalances all selected ETFs to equal weights. M keeps the same per-ETF sleeve only when 12-month momentum is strictly positive and leaves the remaining sleeves in zero-interest cash. Both use the same daily adjusted closes, trade dates and fees.

## Select the workflow

- **New historical comparison.** Use the user's ETFs and inclusive start and end months. Interpret bare years as January through December. Ask only for missing inputs that prevent a run. Accept US-listed, USD-denominated ETFs on a shared US calendar, including gold or other cross-asset exposures. Start with a user-supplied CSV of verifiable origin when available. Otherwise obtain Yahoo Finance prices by default. Follow [market data](references/market-data.md) to check listings, dates, provenance and insufficient history, then [comparison](references/price-only.md) to run the engine.
- **Change only the fee or replay a result.** Find the original frozen price CSV, provenance and run manifest. Verify the recorded price fingerprint and reuse the same tickers, dates, calendar and rules. For a new fee, pass `--fee-bp` (0.25% is 25 bp per side) and use the baseline 10 bp and requested-fee rows. If the frozen inputs or settings are unavailable, request the saved files. A fresh download can support a separate new comparison with the user's agreement.
- **First run or synthetic example.** Run `"<environment-python>" -m etf_backtest.demo "<new-demo-folder>"`. It generates fictional ETF prices locally and saves the input CSV and comparison results. Label both input and results as synthetic.
- **Explain archived results.** Read `references/case-study.md` in the bundle or `docs/case-study.md` at the source repository root. For user-supplied results, check their manifest against the tables and input files when available. State which facts are unverified if supporting inputs are missing. Replay the original study only when requested, following its separate [original protocol](references/methodology.md).

## Run and report

Use the Python environment installed from this checkout or portable bundle. Follow [runtime setup](references/runtime.md) when needed. Write results to a fresh directory and verify the engine's exit status and manifest. If a requested month or daily record is missing, give the affected ETFs and dates. When complete data supports a later start with every ETF and the same end month, give that candidate and wait for confirmation before changing the window or basket.

Lead with the historical trade-off in the user's language. Say which strategy had the higher net annualized return and which had the smaller maximum drawdown, the deepest fall from a previous high. Compare recovery separately. For the main 0.10% per-side scenario, show each strategy's return, drawdown, its own peak, trough and recovery date, calendar days from trough to recovery first and then days from peak to recovery. Label open events “截至期末未恢复” in Chinese and say when a path had no drawdown. If both paths are still underwater at the end, the sample cannot establish which recovered faster.

Use the 0%, 0.05%, 0.10% and 0.20% rows to explain whether the net return ranking changed. For a requested fee, lead with whether the judgments about return, drawdown or recovery changed versus 0.10%, then show the two fee scenarios. Treat tiny floating-point differences as ties and keep the display precision consistent with the conclusion.

Report the input kind, actual comparison dates, price coverage including warmup, source and retrieval date when available, plus the applied per-side fees. Label historical returns in USD. Link the comparison and recovery tables, manifest and frozen inputs that support a replay. Base attribution on holdings or trades when available. Stay within historical evidence when asked what to buy now. Explain the scope of that evidence and offer another historical basket or period as appropriate.
