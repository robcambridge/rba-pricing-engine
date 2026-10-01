"""Paper trading ledger.

Rules that make the record credible:
  * Entries and exits are filled at the official settlement price of the
    latest saved snapshot. You cannot choose your own fill.
  * Every trade carries a stop, a target and a rationale at entry.
  * The ledger is a plain CSV committed to git, so each entry is timestamped
    by a commit made before the event it is a view on.
"""
from pathlib import Path

import pandas as pd

from .contracts import dv01, pnl

LEDGER = Path("ledger/trades.csv")
COLUMNS = ["trade_id", "entry_date", "symbol", "direction", "contracts", "entry_price",
           "stop_price", "target_price", "entry_dv01", "rationale", "exit_date", "exit_price",
           "realised_pnl"]


def load() -> pd.DataFrame:
    if not LEDGER.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(LEDGER, dtype={"exit_date": str, "rationale": str})


def save(trades: pd.DataFrame) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(LEDGER, index=False)


def _settle(snapshot: pd.DataFrame, symbol: str) -> float:
    row = snapshot.loc[snapshot["symbol"] == symbol, "settle"]
    if row.empty:
        raise ValueError(f"{symbol} not in snapshot; available: {', '.join(snapshot['symbol'])}")
    return float(row.iloc[0])


def open_trade(snapshot: pd.DataFrame, symbol: str, direction: str, contracts: int,
               stop: float, target: float, rationale: str) -> pd.Series:
    direction = direction.upper()
    entry = _settle(snapshot, symbol)
    sign = 1 if direction == "LONG" else -1
    if not (sign * stop < sign * entry < sign * target):
        raise ValueError(f"For a {direction} at {entry}, stop and target must sit either side of entry")
    trades = load()
    trade = {
        "trade_id": len(trades) + 1,
        "entry_date": snapshot["date"].iloc[0],
        "symbol": symbol,
        "direction": direction,
        "contracts": contracts,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "entry_dv01": round(dv01(symbol, entry), 2),
        "rationale": rationale,
        "exit_date": None,
        "exit_price": None,
        "realised_pnl": None,
    }
    trades = pd.concat([trades.astype(object), pd.DataFrame([trade], dtype=object)], ignore_index=True)
    save(trades)
    return pd.Series(trade)


def close_trade(snapshot: pd.DataFrame, trade_id: int) -> pd.Series:
    trades = load().astype(object)
    idx = trades.index[trades["trade_id"] == trade_id]
    if idx.empty or pd.notna(trades.loc[idx[0], "exit_date"]):
        raise ValueError(f"No open trade with id {trade_id}")
    t = trades.loc[idx[0]]
    exit_price = _settle(snapshot, t["symbol"])
    trades.loc[idx[0], "exit_date"] = snapshot["date"].iloc[0]
    trades.loc[idx[0], "exit_price"] = exit_price
    trades.loc[idx[0], "realised_pnl"] = round(
        pnl(t["symbol"], t["direction"], int(t["contracts"]), float(t["entry_price"]), exit_price), 2)
    save(trades)
    return trades.loc[idx[0]]


def mark_to_market(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Open positions marked at the snapshot's settlement prices."""
    trades = load()
    rows = []
    for _, t in trades[trades["exit_date"].isna()].iterrows():
        mark = _settle(snapshot, t["symbol"])
        sign = 1 if t["direction"] == "LONG" else -1
        if sign * mark <= sign * t["stop_price"]:
            status = "STOP HIT"
        elif sign * mark >= sign * t["target_price"]:
            status = "TARGET HIT"
        else:
            status = "open"
        rows.append({
            "trade_id": t["trade_id"],
            "symbol": t["symbol"],
            "direction": t["direction"],
            "contracts": t["contracts"],
            "entry_price": t["entry_price"],
            "mark": mark,
            "move_bp": round(sign * (mark - t["entry_price"]) * 100, 1),
            "pnl": round(pnl(t["symbol"], t["direction"], int(t["contracts"]), t["entry_price"], mark), 2),
            "status": status,
        })
    return pd.DataFrame(rows)
