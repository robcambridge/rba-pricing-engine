"""Does the model add anything beyond market pricing? Historical test, 2011-2022.

Market-implied probabilities are rebuilt from the 1-month overnight indexed
swap (OIS) rate the RBA published daily in table F1 until December 2022. A
1-month OIS is the expected average cash rate over the next month, so on the
day before a decision:

    OIS = (n_pre * r_pre + (30 - n_pre) * r_post) / 30

where n_pre is the days until the decision takes effect. Solving for r_post
gives the priced move, and dividing by 25bp gives the probability, the same
convention as the futures strip.

Two tests:
  1. Brier scores on the same meetings: market vs model vs historical frequency.
  2. Encompassing regression: an ordered probit of the outcome on the market's
     signed probability plus a candidate signal. If the signal's coefficient is
     indistinguishable from zero, it adds nothing the market did not already know.

Caveats: OIS carries a small term premium; meetings with moves other than 25bp
(15bp in November 2020, 50bp in mid-2022) are capped at certainty.
"""
from datetime import date

import numpy as np
import pandas as pd
from statsmodels.miscmodels.ordinal_model import OrderedModel

from . import data, reaction
from .meetings import effective_date

OIS_DAYS = 30


def market_probabilities(meetings_: list[date]) -> pd.DataFrame:
    """Market-implied cut / hold / hike probabilities on the eve of each meeting."""
    f1 = data.fetch_rba_table("f1", "%d-%b-%Y").set_index("date")
    ois = pd.to_numeric(f1["FIRMMOIS1D"], errors="coerce").dropna()
    cash = pd.to_numeric(f1["FIRMMCRID"], errors="coerce").dropna()
    rows = []
    for m in meetings_:
        seen = ois[ois.index < m]
        if seen.empty or (m - seen.index[-1]).days > 5:
            continue
        quote_day = seen.index[-1]
        rows.append({"meeting": m, **implied_probs(seen.iloc[-1], cash[cash.index <= quote_day].iloc[-1],
                                                    (effective_date(m) - quote_day).days)})
    return pd.DataFrame(rows)


def implied_probs(ois: float, r_pre: float, n_pre: int) -> dict:
    priced_bp = (ois - r_pre) * OIS_DAYS / (OIS_DAYS - n_pre) * 100
    p = float(np.clip(priced_bp / 25, -1, 1))
    return {"priced_bp": priced_bp, "mkt_cut": max(-p, 0), "mkt_hold": 1 - abs(p), "mkt_hike": max(p, 0)}


def build(df: pd.DataFrame) -> pd.DataFrame:
    """Join outcomes, out-of-sample model forecasts and market probabilities by meeting.

    `df` is the reaction-function dataset, including the nowcast growth gap.
    """
    bt = reaction.backtest(df, reaction.BASE)
    mkt = market_probabilities(list(df["meeting"]))
    out = bt.merge(mkt, on="meeting").merge(df[["meeting", "growth_gap"]], on="meeting")
    out["mkt_signed"] = out["mkt_hike"] - out["mkt_cut"]
    out["model_signed"] = out["p_hike"] - out["p_cut"]
    return out


def brier_table(t: pd.DataFrame) -> dict:
    y = t["outcome"].to_numpy()
    return {
        "market": reaction.brier(t[["mkt_cut", "mkt_hold", "mkt_hike"]].to_numpy(), y),
        "model": reaction.brier(t[["p_cut", "p_hold", "p_hike"]].to_numpy(), y),
        "historical frequency": reaction.brier(t[["base_cut", "base_hold", "base_hike"]].to_numpy(), y),
    }


def encompassing(t: pd.DataFrame, signal: str) -> dict:
    """Ordered probit of outcome on market pricing plus one candidate signal."""
    fit = OrderedModel(t["outcome"], t[["mkt_signed", signal]], distr="probit").fit(method="bfgs", disp=False)
    return {"signal": signal, "market_coef": fit.params["mkt_signed"], "market_z": fit.tvalues["mkt_signed"],
            "signal_coef": fit.params[signal], "signal_z": fit.tvalues[signal], "signal_p": fit.pvalues[signal]}


def calibration(t: pd.DataFrame, edges=(0, 0.05, 0.25, 0.5, 0.75, 1.0001)) -> pd.DataFrame:
    """How often the RBA moved in the priced direction, by bucket of market-implied probability."""
    p = t["mkt_signed"].abs()
    direction = np.sign(t["mkt_signed"]).replace(0, np.nan)
    moved = ((t["outcome"] - 1) == direction).astype(float)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi)
        if mask.any():
            rows.append({"bucket": f"{lo:.0%} to {min(hi, 1):.0%}", "meetings": int(mask.sum()),
                         "mean_priced": float(p[mask].mean()), "moved": float(moved[mask].mean())})
    return pd.DataFrame(rows)