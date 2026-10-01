"""GDP nowcast input, from github.com/robcambridge/aus-gdp-nowcast.

That project's headline model is an equal-weight average of bridge equations
on monthly indicators, estimated on a rolling 40-quarter window. Here it is
re-run on freshly downloaded ABS data, at any vintage date, using the
project's own point-in-time panel so each date only sees published data.

The signal passed to the reaction function is the growth gap: the nowcast for
the latest unpublished quarter less average growth over the same 40 quarters.
"""
from datetime import date
from pathlib import Path

import pandas as pd
from ausgdp.benchmarks import Context
from ausgdp.bridge import make_bridge_average
from ausgdp.config import MONTHLY_PREDICTORS, SHORT_HISTORY, SPECS_BY_NAME, TARGET
from ausgdp.dataset import as_of
from ausgdp.fetch import fetch_all
from ausgdp.transforms import make_panel

WINDOW = 40
PANEL = Path("data/nowcast_panel.csv")
INDICATORS = sorted(s.name for s in MONTHLY_PREDICTORS if s.name not in SHORT_HISTORY)
_bridge_average = make_bridge_average(INDICATORS, add_ar=False, window=WINDOW)


def refresh_panel() -> pd.DataFrame:
    """Download the latest ABS data and rebuild the point-in-time panel."""
    raw = fetch_all([TARGET, *MONTHLY_PREDICTORS], verbose=False)
    missing = {TARGET.name, *INDICATORS} - set(raw)
    if missing:
        raise RuntimeError(f"ABS download failed for: {', '.join(sorted(missing))}")
    panel = make_panel(raw, SPECS_BY_NAME)
    panel["fetched"] = str(date.today())  # file timestamps do not survive a git checkout
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(PANEL, index=False)
    return panel


def load_panel(max_age_days: int = 7) -> pd.DataFrame:
    """The saved panel, refreshed from the ABS if it is older than `max_age_days`."""
    if PANEL.exists():
        panel = pd.read_csv(PANEL, parse_dates=["ref_end", "available_from"])
        if "fetched" in panel and (date.today() - date.fromisoformat(panel["fetched"].iloc[0])).days < max_age_days:
            return panel
    print("Downloading ABS data for the GDP nowcast (can take several minutes)...")
    return refresh_panel()


def nowcast_at(panel: pd.DataFrame, when: date) -> dict:
    """Bridge-average nowcast using only data published by `when`."""
    snap = as_of(panel, pd.Timestamp(when))
    y = snap.quarterly["gdp_growth"].dropna()
    target = y.index[-1] + 1
    ctx = Context(y=y, snapshot=snap, target=target, vintage=pd.Timestamp(when))
    nowcast = float(_bridge_average(ctx))
    trend = float(y.iloc[-WINDOW:].mean())
    return {"quarter": str(target), "nowcast": nowcast, "trend": trend, "growth_gap": nowcast - trend}
