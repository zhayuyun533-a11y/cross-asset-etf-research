"""Validate USD ETF observations against the shared U.S. equity session calendar."""

from itertools import groupby

import exchange_calendars
import pandas as pd

from .core import LOOKBACK_MONTHS


CALENDAR_NAME = "XNYS"
CALENDAR_VERSION = exchange_calendars.__version__


def expected_sessions(warmup_month, end):
    """Get expected close dates from the first required month-end through end."""
    try:
        calendar = exchange_calendars.get_calendar(
            CALENDAR_NAME, start=warmup_month.start_time, end=end.end_time.normalize()
        )
        sessions = calendar.sessions
    except ValueError as error:
        raise ValueError(f"Cannot verify the {CALENDAR_NAME} calendar for {warmup_month} to {end}: {error}") from error
    first_month = sessions[sessions.to_period("M") == warmup_month]
    if first_month.empty:
        raise ValueError(f"No {CALENDAR_NAME} month-end session found for {warmup_month}")
    return sessions[sessions >= first_month[-1]]


def _describe_missing(sessions, missing):
    positions = sessions.get_indexer(missing)
    intervals = []
    for _, group in groupby(enumerate(positions), key=lambda pair: pair[1] - pair[0]):
        consecutive = [position for _, position in group]
        first = sessions[consecutive[0]].date().isoformat()
        last = sessions[consecutive[-1]].date().isoformat()
        intervals.append(first if first == last else f"{first} to {last}")
    examples = ", ".join(intervals[:8])
    if len(intervals) > 8:
        examples += f", …, {intervals[-1]}"
    return f"{len(missing)} required session(s) ({examples})"


def validate_session_coverage(observed, sessions, start, end, *, first_trade_dates=None):
    """Raise with per-symbol gaps and a verified alternative start if available."""
    issues = []
    last_missing = None
    has_unexpected_dates = False
    for ticker, dates in observed.items():
        dates = pd.DatetimeIndex(dates)
        missing = sessions.difference(dates)
        extra = dates[dates >= sessions[0]].difference(sessions)
        if len(extra):
            has_unexpected_dates = True
            days = ", ".join(day.date().isoformat() for day in extra[:5])
            issues.append(f"{ticker}: date(s) outside the {CALENDAR_NAME} trading calendar ({days})")
        if len(missing):
            last_missing = missing[-1] if last_missing is None else max(last_missing, missing[-1])
            listing_date = (first_trade_dates or {}).get(ticker)
            listing = f"; provider first trade date {listing_date}" if listing_date else ""
            issues.append(f"{ticker}: missing {_describe_missing(sessions, missing)}{listing}")
    if not issues:
        return
    message = "Incomplete or inconsistent adjusted-close calendar coverage. " + ". ".join(issues) + "."
    if has_unexpected_dates:
        raise ValueError(message)
    month_ends = sessions[~sessions.to_period("M").duplicated(keep="last")]
    clean_month_ends = month_ends[month_ends > last_missing]
    if len(clean_month_ends):
        candidate = clean_month_ends[0].to_period("M") + LOOKBACK_MONTHS + 1
        if candidate <= end and len(sessions[sessions >= candidate.start_time]) >= 3:
            raise ValueError(
                f"{message} The requested window from {start} needs a complete 12-month signal history "
                "before its first rebalance and complete prices through the end. "
                f"Earliest valid start keeping all tickers and end {end}: {candidate}; "
                "confirm before running."
            )
    raise ValueError(
        f"{message} No valid later start keeping all tickers and end {end} "
        "has both a usable 12-month signal and complete coverage to the requested end."
    )
