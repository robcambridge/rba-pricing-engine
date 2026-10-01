from datetime import date

import numpy as np
import pandas as pd
import pytest

from rbaengine import carry, contracts, events, ledger, markettest, reaction, tracking
from rbaengine.implied_path import month_average, strip_path
from rbaengine.meetings import effective_date, historical


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


def test_historical_meetings_are_first_tuesdays():
    dates = historical(2022, date(2023, 12, 31))
    assert len(dates) == 22 and all(d.weekday() == 1 and d.day <= 7 for d in dates)
    assert date(2022, 5, 3) in dates  # first hike of the 2022 cycle


def test_features_only_use_published_data():
    quarters = pd.date_range("2020-03-31", "2026-06-30", freq="QE").date
    cpi = pd.Series(np.arange(len(quarters), dtype=float), index=quarters)
    months = pd.date_range("2015-01-31", "2026-08-31", freq="ME").date
    unemp = pd.Series(5.0, index=months)
    # Three days after June quarter end, the June CPI is not yet published.
    early = reaction.features(date(2026, 7, 3), cpi, unemp)
    late = reaction.features(date(2026, 8, 11), cpi, unemp)
    assert early["infl_gap"] == cpi.iloc[-2] - 2.5
    assert late["infl_gap"] == cpi.iloc[-1] - 2.5
    assert late["infl_change"] == 2.0 and late["unemp_gap"] == 0.0


def test_brier_bounds():
    y = np.array([0, 1, 2])
    assert reaction.brier(np.eye(3), y) == 0.0
    assert reaction.brier(np.eye(3)[[1, 2, 0]], y) == 2.0


def test_event_reference_month():
    assert events.reference_month("aug-2026") == "2026-08"
    assert events.reference_month("sep-quarter-2025") == "2025-09"


def test_event_build_matches_release_day_move():
    releases = pd.DataFrame({"kind": ["cpi"], "slug": ["jun-2022"], "reference": ["2022-06"],
                             "released": ["2022-07-27"]})
    dy = pd.Series([3.0, -11.8], index=[date(2022, 7, 26), date(2022, 7, 27)])
    surp = pd.DataFrame({"kind": ["cpi"], "reference": ["2022-06"], "surprise": [0.1]})
    ev = events.build(releases, dy, surp)
    assert ev.loc[0, "move_bp"] == -11.8 and ev.loc[0, "surprise"] == 0.1


def test_average_funding_steps_at_effective_date():
    path = pd.DataFrame({"effective": [date(2026, 11, 4)], "implied_rate": [5.0]})
    # 10 days at 4.0 then 10 days at 5.0
    assert carry.average_funding(path, 4.0, date(2026, 10, 25), days=20) == pytest.approx(4.5)


def test_risk_at_stop():
    t = {"contracts": 35, "entry_dv01": 28.0, "entry_price": 95.00, "stop_price": 94.90}
    assert ledger.risk_at_stop(t) == pytest.approx(9800)


def test_tracking_scores_last_forecast_before_meeting():
    def row(day, hike):
        r = {"date": day, "meeting": "2026-11-03"}
        for s in tracking.SOURCES:
            r.update({f"{s}_cut": 0.0, f"{s}_hold": 1 - hike, f"{s}_hike": hike})
        return r
    # the forecast logged on decision day itself must be ignored
    log = pd.DataFrame([row("2026-10-30", 0.2), row("2026-11-02", 1.0), row("2026-11-03", 0.0)])
    changes = pd.Series([0.25], index=[date(2026, 11, 4)])
    scored = tracking.score(log, changes, date(2026, 11, 5))
    assert scored.loc[0, "outcome"] == "hike" and scored.loc[0, "forecast_date"] == "2026-11-02"
    assert scored.loc[0, "market"] == 0.0
    assert tracking.score(log, changes, date(2026, 11, 3)).empty  # not yet decided


def test_ois_implied_probability():
    # cash 1.50; a fully priced 25bp cut effective in 2 days leaves OIS at (2*1.50 + 28*1.25)/30
    p = markettest.implied_probs((2 * 1.50 + 28 * 1.25) / 30, 1.50, 2)
    assert p["priced_bp"] == pytest.approx(-25) and p["mkt_cut"] == pytest.approx(1.0)
    half = markettest.implied_probs((2 * 1.50 + 28 * 1.625) / 30, 1.50, 2)
    assert half["mkt_hike"] == pytest.approx(0.5) and half["mkt_hold"] == pytest.approx(0.5)


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
