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


# The 2024 reforms moved the Board from eleven meetings a year to eight.
DECISION_DATES_2024_25 = [
    date(2024, 2, 6), date(2024, 3, 19), date(2024, 5, 7), date(2024, 6, 18),
    date(2024, 8, 6), date(2024, 9, 24), date(2024, 11, 5), date(2024, 12, 10),
    date(2025, 2, 18), date(2025, 4, 1), date(2025, 5, 20), date(2025, 7, 8),
    date(2025, 8, 12), date(2025, 9, 30), date(2025, 11, 4), date(2025, 12, 9),
]


def historical(start_year: int, asof: date) -> list[date]:
    """Scheduled decision dates from `start_year` up to `asof`.

    Before 2024 the Board met on the first Tuesday of every month except
    January. Unscheduled moves (e.g. 19 March 2020) are not meetings here.
    """
    dates = []
    for y in range(start_year, 2024):
        for m in range(2, 13):
            first = date(y, m, 1)
            dates.append(first + timedelta(days=(1 - first.weekday()) % 7))
    dates += DECISION_DATES_2024_25 + DECISION_DATES
    return [d for d in dates if d <= asof]


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
