"""Strip a meeting-by-meeting implied cash rate path from 30-day interbank futures.

An IB contract settles at 100 minus the average interbank overnight cash rate
over its calendar month. If a decision takes effect on day d of an N-day month:

    avg = ((d - 1) * r_pre + (N - d + 1) * r_post) / N

Two ways to read the post-meeting rate r_post:

  clean-month  the following month has no meeting, so its contract is a pure
               read of r_post. Preferred: no division by a small day count.
  in-month     solve the equation above using the meeting month's contract and
               the r_pre already stripped from the previous meeting.

The stripped path is a risk-neutral expectation. It includes a term premium
that grows with horizon, so "probability" is a pricing convention, not a
forecast, particularly beyond the next couple of meetings.
"""
from calendar import monthrange
from datetime import date

import pandas as pd

from .meetings import effective_date, upcoming

STEP_BP = 25


def _month_key(d: date, offset: int = 0) -> str:
    y, m = divmod(d.year * 12 + d.month - 1 + offset, 12)
    return f"{y}-{m + 1:02d}"


def month_average(r_pre: float, r_post: float, eff: date) -> float:
    n = monthrange(eff.year, eff.month)[1]
    return ((eff.day - 1) * r_pre + (n - eff.day + 1) * r_post) / n


def strip_path(ib: dict[str, float], cash_rate: float, asof: date) -> pd.DataFrame:
    """`ib` maps contract month ('2026-11') to settlement price."""
    meetings = upcoming(asof)
    eff_months = {_month_key(effective_date(m)) for m in meetings}
    rows, r_prev = [], cash_rate
    for m in meetings:
        eff = effective_date(m)
        this_m, next_m = _month_key(eff), _month_key(eff, 1)
        if next_m in ib and next_m not in eff_months:
            r_post, method, contract = 100 - ib[next_m], "clean-month", next_m
        elif this_m in ib:
            n = monthrange(eff.year, eff.month)[1]
            days_post = n - eff.day + 1
            r_post = (n * (100 - ib[this_m]) - (eff.day - 1) * r_prev) / days_post
            method, contract = "in-month", this_m
        else:
            break
        change_bp = (r_post - r_prev) * 100
        rows.append({
            "meeting": m,
            "effective": eff,
            "implied_rate": round(r_post, 4),
            "change_bp": round(change_bp, 1),
            "prob_25bp": round(change_bp / STEP_BP, 3),
            "cum_bp": round((r_post - cash_rate) * 100, 1),
            "method": method,
            "contract": contract,
        })
        r_prev = r_post
    return pd.DataFrame(rows)


def meeting_scenarios(ib: dict[str, float], path: pd.DataFrame, cash_rate: float,
                      moves_bp=(-25, 0, 25)) -> pd.DataFrame:
    """Where the next meeting-month IB contract settles under each outcome.

    Returns the final settlement price and the move from today's price in bp,
    which is the P&L per contract in units of the contract's DV01.
    """
    nxt = path.iloc[0]
    eff = nxt["effective"]
    contract = _month_key(eff)
    if contract not in ib:
        return pd.DataFrame()
    rows = []
    for mv in moves_bp:
        final = 100 - month_average(cash_rate, cash_rate + mv / 100, eff)
        rows.append({
            "outcome_bp": mv,
            "contract": contract,
            "price_now": ib[contract],
            "final_settle": round(final, 4),
            "price_move_bp": round((final - ib[contract]) * 100, 1),
        })
    return pd.DataFrame(rows)
