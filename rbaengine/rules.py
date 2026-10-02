"""Candidate systematic rules, tested on free historical data.

Each rule is a simple, pre-specified idea from the rates literature. They are
reported together, including the ones that fail, because testing several rules
and showing only the winner is how backtests lie: with five rules and a few
variants each, one "significant" result is expected by chance.

P&L is in basis points of yield, before carry and costs. A t-statistic above
about 2 is the usual bar; with this many rules tried, the honest bar is higher.
"""
from datetime import date

import numpy as np
import pandas as pd

from . import data, events, meetings


def _t(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 else float("nan")


def load() -> dict:
    f1 = data.fetch_rba_table("f1", "%d-%b-%Y").set_index("date")
    f2 = data.fetch_rba_table("f2", "%d-%b-%Y").set_index("date")
    for f in (f1, f2):
        f.index = pd.to_datetime(f.index)
    return {
        "cash": pd.to_numeric(f1["FIRMMCRID"], errors="coerce").dropna(),
        "ois3m": pd.to_numeric(f1["FIRMMOIS3D"], errors="coerce").dropna(),
        "y3": f2["FCMYGBAG3D"].dropna(),
        "y10": f2["FCMYGBAG10D"].dropna(),
        "releases": events.fetch_release_dates(),
    }


def momentum(y: pd.Series, lookback: int = 252) -> dict:
    """Trend following: long the bond if its yield fell over the lookback, short if it rose."""
    position = -np.sign(y - y.shift(lookback))
    pnl = (position.shift(1) * -(y.diff() * 100)).dropna()
    return {"n": len(pnl), "unit": "days", "mean_bp": pnl.mean(), "t": _t(pnl),
            "signal": "long" if position.iloc[-1] > 0 else "short"}


def post_release_drift(y3: pd.Series, releases: pd.DataFrame, kind: str, hold: int, min_move: float = 3.0) -> dict:
    """After a release-day move of at least `min_move` bp, follow it for `hold` trading days."""
    dy = y3.diff() * 100
    out = []
    for d0 in pd.to_datetime(releases.loc[releases["kind"] == kind, "released"]):
        if d0 in dy.index:
            i = dy.index.get_loc(d0)
            if i + hold < len(dy) and abs(dy.iloc[i]) >= min_move:
                out.append(np.sign(dy.iloc[i]) * dy.iloc[i + 1:i + 1 + hold].sum())
    out = np.array(out)
    return {"n": len(out), "unit": "releases", "mean_bp": out.mean(), "t": _t(out), "hit": float((out > 0).mean())}


def ois_premium(ois: pd.Series, cash: pd.Series, days: int = 91) -> dict:
    """Receive 3-month OIS and hold: profit if rates turn out lower than priced."""
    daily = cash.resample("D").ffill()
    rows = []
    for t, v in ois.items():
        window = daily[(daily.index > t) & (daily.index <= t + pd.Timedelta(days=days))]
        if len(window) >= days - 1:
            rows.append((v - window.mean()) * 100)
    out = np.array(rows[::63])  # non-overlapping quarters
    return {"n": len(out), "unit": "quarters", "mean_bp": out.mean(), "t": _t(out), "hit": float((out > 0).mean())}


def curve_reversion(y3: pd.Series, y10: pd.Series, threshold: float = 1.5) -> dict:
    """Fade 3s10s when it is more than `threshold` standard deviations from its one-year average."""
    slope = ((y10 - y3) * 100).dropna()
    z = (slope - slope.rolling(250).mean()) / slope.rolling(250).std()
    position = pd.Series(0.0, index=slope.index)
    position[z > threshold], position[z < -threshold] = -1, 1   # +1 is a steepener
    pnl = (position.shift(1) * slope.diff())[position.shift(1) != 0].dropna()
    state = "flattener" if z.iloc[-1] > threshold else "steepener" if z.iloc[-1] < -threshold else "none"
    return {"n": len(pnl), "unit": "days", "mean_bp": pnl.mean(), "t": _t(pnl), "signal": f"{state} (z = {z.iloc[-1]:+.1f})"}


def decision_day(y3: pd.Series, asof: date) -> dict:
    """Is there a systematic drift in the 3-year yield on RBA decision days?"""
    dy = y3.diff() * 100
    moves = np.array([dy[m] for m in pd.to_datetime(meetings.historical(2013, asof)) if m in dy.index])
    return {"n": len(moves), "unit": "meetings", "mean_bp": np.nanmean(moves), "t": _t(moves)}


def run_all(asof: date) -> pd.DataFrame:
    d = load()
    tests = [
        ("12-month momentum, 3-year", momentum(d["y3"], 252)),
        ("12-month momentum, 10-year", momentum(d["y10"], 252)),
        ("3-month momentum, 3-year", momentum(d["y3"], 63)),
        ("Follow CPI-day move for 5 days", post_release_drift(d["y3"], d["releases"], "cpi", 5)),
        ("Follow CPI-day move for 3 days", post_release_drift(d["y3"], d["releases"], "cpi", 3)),
        ("Follow labour-day move for 5 days", post_release_drift(d["y3"], d["releases"], "labour", 5)),
        ("Receive 3-month OIS and hold", ois_premium(d["ois3m"], d["cash"])),
        ("Fade 3s10s beyond 1.5 sd", curve_reversion(d["y3"], d["y10"])),
        ("Drift on RBA decision day", decision_day(d["y3"], asof)),
    ]
    rows = []
    for name, r in tests:
        rows.append({
            "Rule": name,
            "Sample": f"{r['n']} {r['unit']}",
            "Mean (bp)": f"{r['mean_bp']:+.2f}",
            "t-stat": f"{r['t']:+.1f}",
            "Hit rate": f"{r['hit']:.0%}" if "hit" in r else "",
            "Signal now": r.get("signal", ""),
        })
    return pd.DataFrame(rows)
