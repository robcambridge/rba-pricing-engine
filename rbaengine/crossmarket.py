"""Australia vs United States 10-year government bond spread.

The spread is a way to express a relative view (the RBA will be more or less
hawkish than the Fed) without taking a view on global yields. This module
only monitors it: the ledger trades ASX 24 futures, so a spread position is
not paper traded here.

AU yields are RBA table F2; US yields are the Treasury's daily par yield curve.
Days are matched by calendar date, so the US close is about 16 hours after the
Australian close on the same date.
"""
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from . import data

TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")
CACHE = Path("data/us_10y.csv")
START_YEAR = 2013


def fetch_us_10y() -> pd.Series:
    """US 10-year yield. Past years are cached; the latest cached year onward is refetched."""
    cached = pd.read_csv(CACHE, parse_dates=["date"]) if CACHE.exists() else pd.DataFrame(columns=["date", "us10"])
    first = cached["date"].max().year if len(cached) else START_YEAR
    parts = [cached[cached["date"].dt.year < first]] if len(cached) else []
    for year in range(first, date.today().year + 1):
        r = requests.get(TREASURY_URL.format(year=year), headers=data.HEADERS, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        parts.append(pd.DataFrame({"date": pd.to_datetime(df["Date"], format="%m/%d/%Y"), "us10": df["10 Yr"]}))
    out = pd.concat(parts, ignore_index=True).dropna().sort_values("date")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE, index=False)
    return out.set_index("date")["us10"]


def spread_history() -> pd.DataFrame:
    au = data.fetch_rba_table("f2", "%d-%b-%Y").set_index("date")["FCMYGBAG10D"].dropna()
    au.index = pd.to_datetime(au.index)
    df = pd.DataFrame({"au10": au, "us10": fetch_us_10y()}).dropna()
    df["spread_bp"] = (df["au10"] - df["us10"]) * 100
    return df


def summary(df: pd.DataFrame) -> dict:
    s = df["spread_bp"]
    five = s[s.index >= s.index[-1] - pd.DateOffset(years=5)]
    return {
        "date": s.index[-1].date(),
        "au10": df["au10"].iloc[-1],
        "us10": df["us10"].iloc[-1],
        "spread_bp": s.iloc[-1],
        "change_3m_bp": s.iloc[-1] - s.iloc[-64],
        "mean_5y_bp": five.mean(),
        "zscore_5y": (s.iloc[-1] - five.mean()) / five.std(),
        "percentile_all": (s < s.iloc[-1]).mean(),
        "start": s.index[0].date(),
    }
