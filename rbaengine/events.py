"""Event study: how much does the 3-year yield move on data releases?

Two questions:

  1. Which releases matter? Compare the typical absolute daily move in the
     3-year yield on labour force, CPI and RBA decision days with ordinary days.
     This needs no measure of surprise at all.
  2. How much per unit of surprise? Regress the release-day yield change on
     the surprise in the data.

Release dates are exact, scraped from each ABS release page (available from
late 2019). Releases are at 11:30am and RBA yields are end-of-day, so the
same-day change captures the reaction.

WEAKNESS, stated plainly: "surprise" here is the change from the previous
reading, because consensus forecasts are not freely available. The market
reacts to actual minus consensus, so these slopes understate the true
sensitivity and the fit is poor. The data values are also today's revised
figures rather than what was first published.
"""
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import statsmodels.api as sm

from . import contracts, data, meetings

ABS = "https://www.abs.gov.au"
RELEASES = {
    "labour": "/statistics/labour/employment-and-unemployment/labour-force-australia",
    "cpi": "/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
CACHE = Path("data/release_dates.csv")
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def reference_month(slug: str) -> str:
    """'aug-2026' or 'sep-quarter-2025' -> '2026-08' / '2025-09'."""
    parts = slug.split("-")
    return f"{parts[-1]}-{MONTHS.index(parts[0]) + 1:02d}"


def fetch_release_dates() -> pd.DataFrame:
    """Exact ABS release dates. Only pages not already in the cache are downloaded."""
    known = pd.read_csv(CACHE, dtype=str) if CACHE.exists() else pd.DataFrame(
        columns=["kind", "slug", "reference", "released"])
    seen, rows = set(zip(known["kind"], known["slug"])), []
    for kind, path in RELEASES.items():
        index = requests.get(ABS + path, headers=HEADERS, timeout=40).text
        slugs = dict.fromkeys(re.findall(re.escape(path) + r'/([a-z]{3}(?:-quarter)?-\d{4})"', index))
        for slug in slugs:
            if (kind, slug) in seen:
                continue
            page = requests.get(f"{ABS}{path}/{slug}", headers=HEADERS, timeout=40).text
            # the "Released" field, not the first <time> tag, which can be a later update
            stamp = re.search(r'Released</div><div class="field__item">\s*(\d{2})/(\d{2})/(\d{4})', page)
            if stamp:
                d, m, y = stamp.groups()
                rows.append({"kind": kind, "slug": slug, "reference": reference_month(slug),
                             "released": f"{y}-{m}-{d}"})
    out = pd.concat([known, pd.DataFrame(rows)], ignore_index=True).sort_values(["kind", "reference"])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE, index=False)
    return out


def yield_changes() -> pd.Series:
    """Daily change in the 3-year government bond yield, in basis points (RBA table F2)."""
    f2 = data.fetch_rba_table("f2", "%d-%b-%Y").set_index("date")["FCMYGBAG3D"].dropna()
    return (f2.diff() * 100).dropna()


def surprises() -> pd.DataFrame:
    """Change-from-previous-reading surprises, keyed by kind and reference month."""
    h5 = data.fetch_rba_table("h5", "%d/%m/%Y").set_index("date")
    g1 = data.fetch_rba_table("g1", "%d/%m/%Y").set_index("date")
    unemp = h5["GLFSURSA"].dropna().diff()
    trimmed = g1["GCPIOCPMTMQP"].dropna().diff()
    rows = [{"kind": "labour", "reference": f"{d:%Y-%m}", "surprise": v} for d, v in unemp.dropna().items()]
    rows += [{"kind": "cpi", "reference": f"{d:%Y-%m}", "surprise": v} for d, v in trimmed.dropna().items()]
    return pd.DataFrame(rows)


def build(releases: pd.DataFrame, dy: pd.Series, surp: pd.DataFrame) -> pd.DataFrame:
    """One row per release: date, yield move that day, and the surprise."""
    ev = releases.merge(surp, on=["kind", "reference"], how="left")
    ev["released"] = pd.to_datetime(ev["released"]).dt.date
    ev["move_bp"] = ev["released"].map(dy)
    return ev.dropna(subset=["move_bp"]).reset_index(drop=True)


def day_types(events: pd.DataFrame, dy: pd.Series) -> pd.DataFrame:
    """Typical absolute 3-year yield move by type of day, over the event sample period."""
    start = events["released"].min()
    dy = dy[dy.index >= start]
    cpi = events[events["kind"] == "cpi"]
    groups = {
        "Labour force": set(events.loc[events["kind"] == "labour", "released"]),
        "CPI (quarterly trimmed mean)": set(cpi.loc[cpi["surprise"].notna(), "released"]),
        "CPI (monthly only)": set(cpi.loc[cpi["surprise"].isna(), "released"]),
        "RBA decision": set(meetings.historical(start.year, dy.index.max())),
    }
    special = set().union(*groups.values())
    groups["All other days"] = set(dy.index) - special
    base = dy[dy.index.isin(groups["All other days"])].abs().mean()
    rows = []
    for name, days in groups.items():
        moves = dy[dy.index.isin(days)].abs()
        if len(moves):
            rows.append({"Day type": name, "Days": len(moves), "Mean abs move (bp)": round(moves.mean(), 1),
                         "Median (bp)": round(moves.median(), 1), "vs ordinary day": f"{moves.mean() / base:.1f}x"})
    return pd.DataFrame(rows)


def sensitivity(events: pd.DataFrame, kind: str, exclude_2020: bool = False) -> dict:
    """OLS of release-day yield move on surprise."""
    ev = events[(events["kind"] == kind) & events["surprise"].notna()]
    if exclude_2020:
        ev = ev[[d.year != 2020 for d in ev["released"]]]
    fit = sm.OLS(ev["move_bp"], sm.add_constant(ev["surprise"])).fit(cov_type="HC1")
    resid_sd = float(np.sqrt(fit.mse_resid))
    return {"n": len(ev), "resid_sd": resid_sd, "intercept": float(fit.params["const"]), "slope": float(fit.params["surprise"]),
            "se": float(fit.bse["surprise"]), "t": float(fit.tvalues["surprise"]), "r2": float(fit.rsquared)}


def next_quarterly_cpi() -> tuple[date, str, float]:
    """Date and label of the next CPI release that carries a quarterly trimmed mean,
    plus the latest published quarterly trimmed mean (per cent, q/q)."""
    path = RELEASES["cpi"]
    index = requests.get(ABS + path, headers=HEADERS, timeout=40).text
    latest = re.search(re.escape(path) + r'/([a-z]{3}(?:-quarter)?-\d{4})"', index).group(1)
    page = requests.get(f"{ABS}{path}/{latest}", headers=HEADERS, timeout=40).text
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    upcoming = re.findall(r"Next Release (\d{1,2})/(\d{2})/(\d{4}) Consumer Price Index, Australia, (\w+) (\d{4})", text)
    g1 = data.fetch_rba_table("g1", "%d/%m/%Y").set_index("date")["GCPIOCPMTMQP"].dropna()
    for d, m, y, month, year in upcoming:
        if month in ("March", "June", "September", "December"):
            return date(int(y), int(m), int(d)), f"{month} quarter {year}", float(g1.iloc[-1])
    raise LookupError("No quarterly CPI release in the ABS future release list")


def scenario_map(fit: dict, previous: float, price: float, dv01: float, marks: pd.DataFrame,
                 steps=(-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3)) -> pd.DataFrame:
    """Expected 3-year move for each trimmed mean outcome, from the event-study fit.

    P&L for the open book assumes every open position's yield moves by the same amount.
    """
    rows = []
    for step in steps:
        move = fit["intercept"] + fit["slope"] * step
        row = {"Trimmed mean q/q": f"{previous + step:.1f}%", "vs previous": f"{step:+.1f}",
               "3y yield move (bp)": f"{move:+.1f}", "3y futures price": f"{price - move / 100:.3f}",
               "P&L per long contract": f"{-move * dv01:+,.0f}"}
        if not marks.empty:
            book = sum((1 if m["direction"] == "LONG" else -1) * m["contracts"]
                       * contracts.dv01(m["symbol"], m["mark"]) * -move for _, m in marks.iterrows())
            row["Open book P&L"] = f"{book:+,.0f}"
        rows.append(row)
    return pd.DataFrame(rows)