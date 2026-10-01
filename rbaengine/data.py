"""Data collection: ASX 24 futures settlements and the RBA cash rate.

Everything is end-of-day. Futures are marked at the official previous-day
settlement price, never at last-trade (back-month last trades can be days old).
"""
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

ASX_URL = ("https://asx.api.markitdigital.com/asx-research/1.0/derivatives/"
           "interest-rate/{product}/futures?days=1&height=179&width=179")
RBA_F1_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# IB = 30-day interbank cash rate, YT = 3-year bond, XT = 10-year bond
PRODUCTS = ["IB", "YT", "XT"]
MONTH_CODES = "FGHJKMNQUVXZ"

SNAPSHOT_DIR = Path("data/snapshots")


def contract_month(symbol: str) -> str:
    """'IBX2026' -> '2026-11'."""
    return f"{symbol[3:]}-{MONTH_CODES.index(symbol[2]) + 1:02d}"


def fetch_futures(product: str) -> pd.DataFrame:
    r = requests.get(ASX_URL.format(product=product), headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = []
    for item in r.json()["data"]["items"]:
        settle = item.get("pricePreviousSettlement")
        if settle is None:
            continue
        rows.append({
            "date": item["datePreviousSettlement"],
            "product": product,
            "symbol": item["symbol"],
            "month": contract_month(item["symbol"]),
            "settle": round(settle, 4),
            "volume": item.get("volume", 0),
        })
    return pd.DataFrame(rows)


def fetch_cash_rate() -> pd.DataFrame:
    """Daily cash rate target and interbank overnight cash rate from RBA table F1."""
    r = requests.get(RBA_F1_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Series ID"))
    df = pd.read_csv(StringIO("\n".join(lines[start:])))
    df = df.rename(columns={"Series ID": "date", "FIRMMCRTD": "target", "FIRMMCRID": "cash_rate"})
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y").dt.date
    return df[["date", "target", "cash_rate"]].dropna(subset=["target"]).reset_index(drop=True)


def take_snapshot() -> pd.DataFrame:
    """Fetch today's settlements for all products and save one CSV per settlement date."""
    futures = pd.concat([fetch_futures(p) for p in PRODUCTS], ignore_index=True)
    cash = fetch_cash_rate().iloc[-1]
    futures["cash_rate"] = cash["cash_rate"] if pd.notna(cash["cash_rate"]) else cash["target"]
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for d, grp in futures.groupby("date"):
        grp.to_csv(SNAPSHOT_DIR / f"{d}.csv", index=False)
    return futures


def load_snapshot(asof: str | None = None) -> pd.DataFrame:
    """Load the snapshot for `asof` (YYYY-MM-DD), or the latest one saved."""
    files = sorted(SNAPSHOT_DIR.glob("*.csv"))
    if not files:
        raise FileNotFoundError("No snapshots yet. Run: python -m rbaengine update")
    path = SNAPSHOT_DIR / f"{asof}.csv" if asof else files[-1]
    return pd.read_csv(path, dtype={"month": str})


def snapshot_date(snapshot: pd.DataFrame) -> date:
    return date.fromisoformat(snapshot["date"].iloc[0])
