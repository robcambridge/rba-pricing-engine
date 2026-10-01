"""Command line entry point: python -m rbaengine <command>."""
import argparse
import warnings
from pathlib import Path

import pandas as pd

from . import contracts, data, implied_path, ledger, nowcast, reaction, report

warnings.filterwarnings("ignore", message="A date index has been provided")
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


# yield changes in bp for (3-year, 10-year)
CURVE_SCENARIOS = {
    "parallel -10": (-10, -10),
    "bull steepen (3y -10, 10y -5)": (-10, -5),
    "bear flatten (3y +10, 10y +5)": (10, 5),
    "parallel +10": (10, 10),
}


def cmd_express(args):
    """Compare bullish expressions of the same dollar risk: outright 3y, outright 10y, 3s10s steepener."""
    snap = data.load_snapshot()
    yt, xt = _front(snap, "YT"), _front(snap, "XT")
    n_yt = contracts.size_by_risk(yt["symbol"], yt["settle"], args.stop_bp, args.risk)
    n_xt = contracts.size_by_risk(xt["symbol"], xt["settle"], args.stop_bp, args.risk)
    ratio = contracts.curve_ratio(yt["symbol"], yt["settle"], xt["symbol"], xt["settle"])
    # steepener: the stop is a flattening of stop_bp, which costs one leg's DV01 per bp
    steep_yt, steep_xt = n_yt, round(n_yt / ratio)
    positions = {
        f"Long {n_yt} {yt['symbol']}": [(yt, "long", n_yt)],
        f"Long {n_xt} {xt['symbol']}": [(xt, "long", n_xt)],
        f"Steepener: long {steep_yt} {yt['symbol']} / short {steep_xt} {xt['symbol']}":
            [(yt, "long", steep_yt), (xt, "short", steep_xt)],
    }
    rows = []
    for name, (d3, d10) in CURVE_SCENARIOS.items():
        row = {"Scenario": name}
        for label, legs in positions.items():
            total = 0.0
            for c, direction, n in legs:
                move = d3 if c["product"] == "YT" else d10
                total += contracts.pnl(c["symbol"], direction, n, c["settle"], c["settle"] - move / 100)
            row[label] = f"{total:+,.0f}"
        rows.append(row)
    print(f"\nEach sized to lose about ${args.risk:,.0f} on a {args.stop_bp:g}bp adverse move. "
          f"DV01-neutral ratio: {ratio:.2f} {yt['symbol']} per {xt['symbol']}.\n")
    print(pd.DataFrame(rows).to_string(index=False))


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
    panel = nowcast.load_panel()
    df = reaction.build_dataset(cpi, unemp, changes, d, panel)
    nxt = path.iloc[0]
    views = {name: reaction.next_meeting_view(df, cols, nxt["meeting"], cpi, unemp, changes, panel)
             for name, cols in (("Model", reaction.BASE), ("Model + nowcast", reaction.NOWCAST))}
    p = nxt["prob_25bp"]
    market = {"cut": max(-p, 0), "hold": 1 - abs(p), "hike": max(p, 0)}
    table = pd.DataFrame({"Outcome": reaction.OUTCOMES})
    for name, v in views.items():
        table[name] = [f"{v['probs'][o]:.0%}" for o in reaction.OUTCOMES]
    table["Market"] = [f"{market[o]:.0%}" for o in reaction.OUTCOMES]
    return df, views, table, nowcast.nowcast_at(panel, d)


SPECS = (("inflation and unemployment", reaction.BASE),
         ("plus previous move", reaction.INERTIA),
         ("plus GDP nowcast", reaction.NOWCAST))


def cmd_view(args):
    snap, d, cash, ib, path = _state()
    df, views, table, nc = _model_vs_market(d, path)
    print(f"\nOrdered probit on {len(df)} scheduled meetings since {reaction.START_YEAR}\n")
    for v in views.values():
        print(v["model"].summary().tables[1], "\n")
    print("Out-of-sample Brier score, expanding window from 2010 (lower is better)")
    for name, cols in SPECS:
        bt = reaction.backtest(df, cols)
        s = reaction.scores(bt)
        early = reaction.scores(bt[[m.year < 2020 for m in bt["meeting"]]])
        print(f"  {name:28s} model {s['model']:.3f} (2010-19: {early['model']:.3f}) | historical frequency "
              f"{s['historical frequency']:.3f} | always hold {s['always hold']:.3f} | n={s['meetings']}")
        if cols is reaction.BASE:
            bt.round(4).to_csv(Path("output") / "reaction_backtest.csv", index=False)
    x = views["Model + nowcast"]["inputs"]
    print(f"\nGDP nowcast {nc['quarter']}: {nc['nowcast']:+.2f}% q/q vs {nc['trend']:.2f}% ten-year average")
    print(f"Next meeting {path.iloc[0]['meeting']:%d %b %Y}: inflation gap {x['infl_gap']:+.1f}, "
          f"unemployment gap {x['unemp_gap']:+.2f}, inflation change {x['infl_change']:+.1f}, "
          f"growth gap {x['growth_gap']:+.2f}\n")
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

    p = sub.add_parser("express", help="compare outright and curve expressions at equal dollar risk")
    p.add_argument("--stop-bp", type=float, required=True)
    p.add_argument("--risk", type=float, required=True)
    p.set_defaults(f=cmd_express)

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
