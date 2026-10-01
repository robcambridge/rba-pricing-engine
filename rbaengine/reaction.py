"""RBA reaction function: ordered probit for cut / hold / hike.

    y* = b1 * (trimmed mean inflation - 2.5)
       + b2 * (unemployment - trailing 5-year average)
       + b3 * (change in trimmed mean inflation over two quarters)
       [+ b4 * direction of the previous move]
       [+ b5 * (GDP nowcast - trend growth)]

Each meeting only sees data already published by the meeting date, using
conservative publication lags. The values themselves are today's revised
series, not real-time vintages, which flatters the model slightly.

This is a benchmark for discipline, not a claim of edge: the backtest reports
whether the model beats a know-nothing forecast, and the README is explicit
that it has not yet been tested against market pricing.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
from statsmodels.miscmodels.ordinal_model import OrderedModel

from . import data, meetings

TARGET_MIDPOINT = 2.5
CPI_LAG_DAYS = 32      # quarterly CPI is published about four weeks after quarter end
LABOUR_LAG_DAYS = 25   # labour force is published about three weeks after month end
START_YEAR = 1998
OUTCOMES = ["cut", "hold", "hike"]
BASE = ["infl_gap", "unemp_gap", "infl_change"]
INERTIA = BASE + ["last_move"]
NOWCAST = BASE + ["growth_gap"]


def fetch_inputs():
    cpi = data.fetch_rba_table("g1", "%d/%m/%Y").set_index("date")["GCPIOCPMTMYP"].dropna()
    unemp = data.fetch_rba_table("h5", "%d/%m/%Y").set_index("date")["GLFSURSA"].dropna()
    a2 = data.fetch_rba_table("a2", "%d-%b-%Y")
    changes = a2.set_index("date")["ARBAMPCCCR"].dropna()
    # early rows are ranges like '-0.50 to -1.00'; only the sign matters here
    changes = changes.astype(str).str.split(" to ").str[0].astype(float)
    return cpi, unemp, changes


def features(when: date, cpi: pd.Series, unemp: pd.Series, panel: pd.DataFrame | None = None) -> dict:
    cpi_seen = cpi[cpi.index <= when - timedelta(days=CPI_LAG_DAYS)]
    unemp_seen = unemp[unemp.index <= when - timedelta(days=LABOUR_LAG_DAYS)]
    out = {
        "infl_gap": cpi_seen.iloc[-1] - TARGET_MIDPOINT,
        "unemp_gap": unemp_seen.iloc[-1] - unemp_seen.iloc[-60:].mean(),
        "infl_change": cpi_seen.iloc[-1] - cpi_seen.iloc[-3],
    }
    if panel is not None:
        from .nowcast import nowcast_at
        out["growth_gap"] = nowcast_at(panel, when)["growth_gap"]
    return out


def build_dataset(cpi, unemp, changes, asof: date, panel=None) -> pd.DataFrame:
    """One row per scheduled meeting: outcome and the data visible at the time."""
    rows = []
    for m in meetings.historical(START_YEAR, asof):
        window = changes[(changes.index > m) & (changes.index <= m + timedelta(days=7))]
        prior = changes[changes.index <= m]
        rows.append({
            "meeting": m,
            "outcome": int(np.sign(window.sum())) + 1,   # 0 cut, 1 hold, 2 hike
            "last_move": int(np.sign(prior.iloc[-1])) if len(prior) else 0,
            **features(m, cpi, unemp, panel),
        })
    return pd.DataFrame(rows)


def fit(df: pd.DataFrame, cols: list[str]):
    return OrderedModel(df["outcome"], df[cols], distr="probit").fit(method="bfgs", disp=False)


def brier(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Multi-class Brier score: 0 is perfect, lower is better."""
    onehot = np.eye(3)[outcomes]
    return float(((probs - onehot) ** 2).sum(axis=1).mean())


def backtest(df: pd.DataFrame, cols: list[str], first_test_year: int = 2010) -> pd.DataFrame:
    """Expanding window: refit each January on prior years only, predict that year."""
    out = []
    years = pd.Series([m.year for m in df["meeting"]])
    for y in range(first_test_year, years.max() + 1):
        train, test = df[(years < y).values], df[(years == y).values]
        if test.empty:
            continue
        model = fit(train, cols)
        p = np.asarray(model.predict(test[cols]))
        base = np.bincount(train["outcome"], minlength=3) / len(train)
        for i, (_, row) in enumerate(test.iterrows()):
            out.append({"meeting": row["meeting"], "outcome": row["outcome"],
                        "p_cut": p[i, 0], "p_hold": p[i, 1], "p_hike": p[i, 2],
                        "base_cut": base[0], "base_hold": base[1], "base_hike": base[2]})
    return pd.DataFrame(out)


def scores(bt: pd.DataFrame) -> dict:
    y = bt["outcome"].to_numpy()
    return {
        "meetings": len(bt),
        "model": brier(bt[["p_cut", "p_hold", "p_hike"]].to_numpy(), y),
        "historical frequency": brier(bt[["base_cut", "base_hold", "base_hike"]].to_numpy(), y),
        "always hold": brier(np.tile([0.0, 1.0, 0.0], (len(bt), 1)), y),
    }


def next_meeting_view(df: pd.DataFrame, cols: list[str], when: date, cpi, unemp, changes, panel=None) -> dict:
    """Model probabilities for a meeting on `when`, fitted on every past meeting."""
    model = fit(df, cols)
    x = {**features(when, cpi, unemp, panel), "last_move": int(np.sign(changes[changes.index <= when].iloc[-1]))}
    p = np.asarray(model.predict(pd.DataFrame([x])[cols]))[0]
    return {"inputs": x, "probs": dict(zip(OUTCOMES, p)), "model": model}
