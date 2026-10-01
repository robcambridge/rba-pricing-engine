"""Live forecast record: model vs market, scored after each meeting.

Every daily update logs the probabilities of cut / hold / hike at the next
meeting from three sources: futures pricing, the reaction function, and the
reaction function with the GDP nowcast. Once a meeting has happened, the last
forecast logged before the decision day is scored with the Brier score.

This is the forward-looking version of the test the backtest cannot do without
historical futures data: does the model add anything to market pricing?
The log is committed to git daily, so forecasts cannot be revised after the fact.
"""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .meetings import effective_date
from .reaction import OUTCOMES, brier

LOG = Path("data/forecast_log.csv")
SOURCES = ["market", "model", "model_nowcast"]


def record(asof: date, meeting: date, probs: dict[str, dict[str, float]]) -> pd.DataFrame:
    """Add today's forecasts, replacing any earlier row for the same date."""
    row = {"date": str(asof), "meeting": str(meeting)}
    for source in SOURCES:
        for outcome in OUTCOMES:
            row[f"{source}_{outcome}"] = round(float(probs[source][outcome]), 4)
    log = pd.read_csv(LOG, dtype={"date": str, "meeting": str}) if LOG.exists() else pd.DataFrame()
    if len(log):
        log = log[log["date"] != row["date"]]
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True).sort_values("date")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(LOG, index=False)
    return log


def score(log: pd.DataFrame, changes: pd.Series, asof: date) -> pd.DataFrame:
    """One row per decided meeting: the outcome and each source's final pre-meeting forecast.

    `changes` is the RBA's record of cash rate changes, indexed by effective date.
    """
    rows = []
    for meeting, grp in log.groupby("meeting"):
        m = date.fromisoformat(meeting)
        eff = effective_date(m)
        before = grp[grp["date"] < meeting]
        if eff > asof or before.empty:
            continue
        last = before.iloc[-1]
        move = changes[changes.index == eff].sum()
        outcome = int(np.sign(move)) + 1
        row = {"meeting": meeting, "forecast_date": last["date"], "outcome": OUTCOMES[outcome]}
        for source in SOURCES:
            p = np.array([[last[f"{source}_{o}"] for o in OUTCOMES]])
            row[source] = round(brier(p, np.array([outcome])), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def markdown(scored: pd.DataFrame, n_logged: int) -> str:
    lines = ["## Forecast record: model vs market", "",
             "Brier score of the last forecast logged before each RBA decision (0 is perfect, lower is better).", ""]
    if scored.empty:
        return "\n".join(lines + [f"No meetings scored yet. Daily forecasts logged so far: {n_logged}.", ""])
    lines += ["| Meeting | Forecast date | Outcome | Market | Model | Model + nowcast |", "|---|---|---|---|---|---|"]
    for _, r in scored.iterrows():
        lines.append(f"| {r['meeting']} | {r['forecast_date']} | {r['outcome']} | "
                     f"{r['market']:.3f} | {r['model']:.3f} | {r['model_nowcast']:.3f} |")
    lines.append(f"| **Average ({len(scored)})** | | | **{scored['market'].mean():.3f}** | "
                 f"**{scored['model'].mean():.3f}** | **{scored['model_nowcast'].mean():.3f}** |")
    return "\n".join(lines + [""])
