# Research protocol

The engine fixes a six ETF universe in the order DBC, EFA, GLD, IEF, SPY and VNQ, with Cash as the remaining balance. The protocol version is six-etf-em-2008-2025-v1.

## Inputs

The long price CSV requires Date, Ticker and Adj Close. Dates identify local trading sessions. Each date must have one finite positive price for each of the six ETFs. Additional source fields may be present. The archived study has 4,800 observed sessions from 1 December 2006 to 31 December 2025.

Each development target CSV requires Execution Date and DBC, EFA, GLD, IEF, SPY, VNQ, Cash. Weights use decimal fractions, are finite and nonnegative, and sum to one. The two files have the same 96 observed execution dates from January 2008 through December 2015. E has one sixth in each ETF and zero cash. M has zero or one sixth in each ETF with the remainder in cash.

The development targets are explicit inputs, preserving the original archive. Later signals are regenerated from the supplied prices. The caller is responsible for supplying development targets derived from the same source prices and frozen rules. Input hashes make that choice inspectable.

## Monthly signals and execution

Monthly levels are the last observed adjusted close in each calendar month. A momentum signal compares that value with the value 12 months earlier, taking the ratio minus one. December 2006 provides the first endpoint, and 2007 is the warm-up year. Complete lookbacks are required.

A strictly positive signal gives M a one sixth allocation to that ETF. A signal of zero or below places that sleeve in cash. Qualifying assets keep one sixth each.

The last observed close of the signal month determines the signal. Trading takes place at the next observed session’s close. The old holdings earn the move into that execution close. New holdings earn the next close-to-close market return. All positions are long and cash interest is zero.

## Valuation and fees

Each run starts from 600 cash units on the first execution date in 2008. ETF holding values move by adjusted-close ratios. Adjusted close serves as a valuation ratio input. Dividend effects are already reflected in the supplied series.

On each execution date, targets use the post-fee portfolio value. The self-financing fee solver iterates the following relationship, using a tolerance of 1e-10 and a maximum of 20 iterations.

Fee = fee rate × sum of absolute ETF trade amounts.

Cash transfers have zero trading fee. Every scenario uses one continuous path from 2008 for each strategy. The main rate is 10 bp and preset sensitivity rates are 0, 5 and 20 bp.

## Periods and measurements

Development covers 2008–2015 and uses the original 600 as its growth and drawdown anchor. The first fee-only row is excluded from the daily volatility estimate. Validation covers 2016–2020 and anchors on the 31 December 2015 close. Holdout covers 2021–2025 and anchors on the 31 December 2020 close. Later segments carry actual holdings forward and retain their first full daily net return.

Cumulative return is the end/start NAV ratio minus one. CAGR annualizes the growth factor using actual calendar days divided by 365.25. Volatility is the sample standard deviation of full daily net returns times sqrt(252). Maximum drawdown is the minimum NAV/running-peak ratio minus one, with the anchor included. Metric files use decimal fractions.

## Scope and provenance

The study uses a fixed universe, one vendor snapshot, adjusted-close execution and zero-interest cash. The Yahoo Finance snapshot was acquired on 23 October 2026. The holdout describes 2021–2025 under the archived rules. Reproducing the recorded figures requires the same snapshot and development weights. The public case study summarizes the archived results and describes the tradeoffs across the three periods.
