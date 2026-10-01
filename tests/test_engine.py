from datetime import date

import pytest

from rbaengine import contracts
from rbaengine.implied_path import month_average, strip_path
from rbaengine.meetings import effective_date


def test_effective_date_skips_weekend():
    assert effective_date(date(2026, 11, 3)) == date(2026, 11, 4)
    assert effective_date(date(2026, 10, 2)) == date(2026, 10, 5)  # Friday -> Monday


def test_strip_recovers_known_path():
    """Build futures prices from a known path and check the strip returns it."""
    cash, asof = 4.60, date(2026, 10, 1)
    # hike 25bp in Nov, hold in Dec, hike 10bp-equivalent in Feb
    r_nov, r_dec, r_feb = 4.85, 4.85, 4.95
    ib = {
        "2026-10": 100 - cash,
        "2026-11": 100 - month_average(cash, r_nov, date(2026, 11, 4)),
        "2026-12": 100 - month_average(r_nov, r_dec, date(2026, 12, 9)),
        "2027-01": 100 - r_dec,
        "2027-02": 100 - month_average(r_dec, r_feb, date(2027, 2, 10)),
    }
    path = strip_path(ib, cash, asof)
    assert list(path["implied_rate"]) == pytest.approx([r_nov, r_dec, r_feb], abs=1e-4)
    assert list(path["method"]) == ["in-month", "clean-month", "in-month"]
    assert path["prob_25bp"].iloc[0] == pytest.approx(1.0)
    assert path["cum_bp"].iloc[-1] == pytest.approx(35.0)


def test_ib_dv01():
    assert contracts.dv01("IBX2026", 95.35) == pytest.approx(24.66, abs=0.01)


def test_bond_futures_value_at_par():
    # At a 6% yield the notional 6% coupon bond is worth face value.
    assert contracts.contract_value("YTZ2026", 94.0) == pytest.approx(100_000)
    assert contracts.contract_value("XTZ2026", 94.0) == pytest.approx(100_000)


def test_dv01_ordering_and_pnl_sign():
    d3, d10 = contracts.dv01("YTZ2026", 95.0), contracts.dv01("XTZ2026", 94.6)
    assert 2.5 < d10 / d3 < 3.5
    assert contracts.pnl("YTZ2026", "long", 10, 95.00, 95.10) > 0
    assert contracts.pnl("YTZ2026", "short", 10, 95.00, 95.10) < 0


def test_size_by_risk_never_exceeds_budget():
    n = contracts.size_by_risk("YTZ2026", 95.0, stop_bp=10, risk_budget=10_000)
    assert n * contracts.dv01("YTZ2026", 95.0) * 10 <= 10_000
