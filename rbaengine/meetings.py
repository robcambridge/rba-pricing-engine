"""RBA Monetary Policy Board meeting calendar.

Source: https://www.rba.gov.au/schedules-events/board-meeting-schedules.html
Dates are the decision (second) day of each two-day meeting. Update this list
when the RBA publishes a new year's schedule.
"""
from datetime import date, timedelta

DECISION_DATES = [
    date(2026, 2, 3), date(2026, 3, 17), date(2026, 5, 5), date(2026, 6, 16),
    date(2026, 8, 11), date(2026, 9, 29), date(2026, 11, 3), date(2026, 12, 8),
    date(2027, 2, 9), date(2027, 3, 23), date(2027, 5, 4), date(2027, 6, 22),
    date(2027, 8, 10), date(2027, 9, 28), date(2027, 11, 2), date(2027, 12, 14),
]


def effective_date(decision: date) -> date:
    """A change in the cash rate target applies from the next business day.

    Weekends are skipped; public holidays are not (none fall the day after a
    scheduled meeting in the current calendar).
    """
    d = decision + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def upcoming(asof: date) -> list[date]:
    """Decision dates whose rate change has not yet taken effect as of `asof`."""
    return [m for m in DECISION_DATES if effective_date(m) > asof]
