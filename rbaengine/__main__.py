"""Command line entry point: python -m rbaengine <command>."""
import argparse
from pathlib import Path

import pandas as pd

from . import contracts, data, implied_path, ledger, reaction, report

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


def _model_vs_market(d, path):
    """Reaction function and market-implied probabilities for the next meeting."""
    cpi, unemp, changes = reaction.fetch_inputs()
    df = reaction.build_dataset(cpi, unemp, changes, d)
    nxt = path.iloc[0]
    view = reaction.next_meeting_view(df, reaction.BASE, nxt["meeting"], cpi, unemp, changes)
    p = nxt["prob_25bp"]
    market = {"cut": max(-p, 0), "hold": 1 - abs(p), "hike": max(p, 0)}
    table = pd.DataFrame({
        "Outcome": reaction.OUTCOMES,
        "Model": [f"{view['probs'][o]:.0%}" for o in reaction.OUTCOMES],
        "Market": [f"{market[o]:.0%}" for o in reaction.OUTCOMES],
    })
    return df, view, table


def cmd_view(args):
    snap, d, cash, ib, path = _state()
    df, view, table = _model_vs_market(d, path)
    print(f"\nOrdered probit on {len(df)} scheduled meetings since {reaction.START_YEAR}\n")
    print(view["model"].summary().tables[1])
    print("\nOut-of-sample Brier score, expanding window from 2010 (lower is better)")
    for name, cols in (("inflation and unemployment", reaction.BASE), ("plus previous move", reaction.INERTIA)):
        bt = reaction.backtest(df, cols)
        s = reaction.scores(bt)
        print(f"  {name:28s} model {s['model']:.3f} | historical frequency "
              f"{s['historical frequency']:.3f} | always hold {s['always hold']:.3f} | n={s['meetings']}")
        if cols is reaction.BASE:
            bt.round(4).to_csv(Path("output") / "reaction_backtest.csv", index=False)
    x = view["inputs"]
    print(f"\nNext meeting {path.iloc[0]['meeting']:%d %b %Y}: inflation gap {x['infl_gap']:+.1f}, "
          f"unemployment gap {x['unemp_gap']:+.2f}, inflation change {x['infl_change']:+.1f}\n")
    print(table.to_string(index=False))


def cmd_note(args):
    snap, d, cash, ib, path = _state()
    scen = implied_path.meeting_scenarios(ib, path, cash)
    y3, y10 = (100 - _front(snap, p)["settle"] for p in ("YT", "XT"))
    out = report.write_note(d, cash, y3, y10, path, scen, ledger.mark_to_market(snap),
                            _model_vs_market(d, path)[2])
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

    sub.add_parser("view", help="reaction function: backtest and next-meeting probabilities vs market").set_defaults(f=cmd_view)

    sub.add_parser("note", help="write this week's note skeleton").set_defaults(f=cmd_note)

    args = ap.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
