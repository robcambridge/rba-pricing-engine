"""Carry and roll-down for bond futures over a three-month horizon.

If the yield curve does not move, a long bond position still earns two things:

  carry      the bond yields more than it costs to fund. Expressed as the
             yield rise that would wipe it out: (yield - funding) * h / duration.
  roll-down  the bond ages into a shorter maturity. On an upward-sloping
             curve that means a lower yield: slope per year * h.

Their sum is the breakeven: how far yields can rise over the horizon before a
long loses money. A futures price already embeds carry as a forward yield, so
this is the convergence a long earns if spot yields stay put.

Approximations: funding is the average market-implied cash rate over the
horizon (from the IB strip) rather than repo; slope comes from the RBA's
interpolated 2, 3, 5 and 10-year yields; the futures basket and
cheapest-to-deliver effects are ignored.
"""
from datetime import date, timedelta

import pandas as pd

from . import contracts, data

HORIZON_DAYS = 91
# product -> (shorter tenor column, own tenor column, years between them) in RBA table F2
SLOPE = {"YT": ("FCMYGBAG2D", "FCMYGBAG3D", 1), "XT": ("FCMYGBAG5D", "FCMYGBAG10D", 5)}


def average_funding(path: pd.DataFrame, cash_rate: float, asof: date, days: int = HORIZON_DAYS) -> float:
    """Average implied cash rate over the next `days`, stepping at each meeting's effective date."""
    total = 0.0
    for i in range(days):
        d = asof + timedelta(days=i)
        passed = path[path["effective"] <= d]
        total += passed["implied_rate"].iloc[-1] if len(passed) else cash_rate
    return total / days


def carry_roll(snapshot: pd.DataFrame, path: pd.DataFrame, cash_rate: float, asof: date) -> pd.DataFrame:
    curve = data.fetch_rba_table("f2", "%d-%b-%Y").dropna(subset=["FCMYGBAG3D"]).iloc[-1]
    funding = average_funding(path, cash_rate, asof)
    h = HORIZON_DAYS / 365
    rows = []
    for product, (short_col, own_col, years) in SLOPE.items():
        c = snapshot[snapshot["product"] == product].sort_values("volume", ascending=False).iloc[0]
        y = 100 - c["settle"]
        dv01 = contracts.dv01(c["symbol"], c["settle"])
        duration = dv01 * 10_000 / contracts.contract_value(c["symbol"], c["settle"])
        carry_bp = (y - funding) * h / duration * 100
        roll_bp = (curve[own_col] - curve[short_col]) / years * h * 100
        rows.append({
            "symbol": c["symbol"],
            "yield": round(y, 3),
            "funding": round(funding, 3),
            "carry_bp": round(carry_bp, 1),
            "roll_bp": round(roll_bp, 1),
            "breakeven_bp": round(carry_bp + roll_bp, 1),
            "dollars_per_contract": round((carry_bp + roll_bp) * dv01),
        })
    return pd.DataFrame(rows)
