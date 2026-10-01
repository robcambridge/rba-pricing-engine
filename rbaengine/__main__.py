"""Command line entry point: python -m rbaengine <command>."""
import argparse
from pathlib import Path

import pandas as pd

from . import contracts, data, implied_path, ledger, report

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)


def _state(asof=None):
    snap = data.load_snapshot(asof)
    d = data.snapshot_date(snap)
    cash = float(snap["cash_rate"].iloc[0])
    ib = dict(zip(snap.loc[snap["product"] == "IB", "month"], snap.loc[snap["product"] == "IB", "settle"]))
    path = implied_path.strip_path(ib, cash, d)
    return snap, d, cash, ib, path


def _front(snap, product):
    """Most actively traded contract for a product."""
    rows = snap[snap["product"] == product]
    return rows.sort_values("volume", ascending=False).iloc[0]


def cmd_update(args):
    data.take_snapshot()
    snap, d, cash, ib, path = _state()
    report.plot_path(path, cash, d, Path("output") / f"implied_path_{d}.png")
    path.to_csv(Path("output") / f"implied_path_{d}.csv", index=False)
    print(f"\nSettlement date {d} | cash rate {cash:.2f}%\n")
    print(report.path_table(path).to_string(index=False))
    for p in ("YT", "XT"):
        f = _front(snap, p)
        print(f"\n{f['symbol']}: {f['settle']:.3f} (yield {100 - f['settle']:.3f}%), "
              f"DV01 ${contracts.dv01(f['symbol'], f['settle']):.2f}")
    marks = ledger.mark_to_market(snap)
    if not marks.empty:
        print("\nOpen positions\n" + marks.to_string(index=False))


def cmd_size(args):
    snap = data.load_snapshot()
    price = ledger._settle(snap, args.symbol)
    d = contracts.dv01(args.symbol, price)
    n = contracts.size_by_risk(args.symbol, price, args.stop_bp, args.risk)
    print(f"{args.symbol} at {price}: DV01 ${d:.2f}/contract. "
          f"{n} contracts loses ${n * d * args.stop_bp:,.0f} if stopped {args.stop_bp}bp away "
          f"(budget ${args.risk:,.0f}).")


def cmd_open(args):
    t = ledger.open_trade(data.load_snapshot(), args.symbol, args.direction, args.contracts,
                          args.stop, args.target, args.rationale)
    print(f"Opened trade {t['trade_id']}: {t['direction']} {t['contracts']} {t['symbol']} "
          f"at {t['entry_price']} (settlement {t['entry_date']})")


def cmd_close(args):
    t = ledger.close_trade(data.load_snapshot(), args.trade_id)
    print(f"Closed trade {t['trade_id']} at {t['exit_price']}: P&L ${t['realised_pnl']:,.2f}")


def cmd_note(args):
    snap, d, cash, ib, path = _state()
    scen = implied_path.meeting_scenarios(ib, path, cash)
    y3, y10 = (100 - _front(snap, p)["settle"] for p in ("YT", "XT"))
    out = report.write_note(d, cash, y3, y10, path, scen, ledger.mark_to_market(snap))
    print(f"Wrote {out}. Fill in sections 2 and 3, then commit it.")


def main():
    ap = argparse.ArgumentParser(prog="rbaengine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("update", help="fetch settlements, strip the implied path, mark the ledger").set_defaults(f=cmd_update)

    p = sub.add_parser("size", help="contracts for a given dollar risk and stop distance")
    p.add_argument("symbol")
    p.add_argument("--stop-bp", type=float, required=True)
    p.add_argument("--risk", type=float, required=True)
    p.set_defaults(f=cmd_size)

    p = sub.add_parser("open", help="open a paper trade at the latest settlement price")
    p.add_argument("symbol")
    p.add_argument("direction", choices=["long", "short"])
    p.add_argument("contracts", type=int)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--target", type=float, required=True)
    p.add_argument("--rationale", required=True)
    p.set_defaults(f=cmd_open)

    p = sub.add_parser("close", help="close a paper trade at the latest settlement price")
    p.add_argument("trade_id", type=int)
    p.set_defaults(f=cmd_close)

    sub.add_parser("note", help="write this week's note skeleton").set_defaults(f=cmd_note)

    args = ap.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
