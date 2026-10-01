"""Command line entry point: python -m rbaengine <command>."""
import argparse
import warnings
from pathlib import Path

import pandas as pd

from . import carry, contracts, crossmarket, data, events, implied_path, ledger, nowcast, reaction, report, tracking

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
    _, _, table, _, probs, changes = _model_vs_market(d, path)
    log = tracking.record(d, path.iloc[0]["meeting"], probs)
    print(f"\nNext meeting {path.iloc[0]['meeting']:%d %b %Y} (logged to {tracking.LOG})\n" + table.to_string(index=False))
    _write_scorecard(snap, log, changes, d)
    report.plot_path(path, cash, d, Path("output") / "implied_path_latest.png")
    y3, y10 = (100 - _front(snap, p)["settle"] for p in ("YT", "XT"))
    report.write_latest(d, cash, y3, y10, path, table, marks)


def _write_scorecard(snap, log, changes, d):
    scored = tracking.score(log, changes, d) if len(log) else pd.DataFrame()
    Path("SCORECARD.md").write_text(ledger.scorecard(snap) + "\n" + tracking.markdown(scored, len(log)),
                                    encoding="utf-8")


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
    "3y -10, 10y -5": (-10, -5),
    "3y +10, 10y +5": (10, 5),
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
        "Long 3y": [(yt, "long", n_yt)],
        "Long 10y": [(xt, "long", n_xt)],
        "Steepener": [(yt, "long", steep_yt), (xt, "short", steep_xt)],
    }
    print(f"\nLong 3y   = long {n_yt} {yt['symbol']}"
          f"\nLong 10y  = long {n_xt} {xt['symbol']}"
          f"\nSteepener = long {steep_yt} {yt['symbol']}, short {steep_xt} {xt['symbol']}")
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


def cmd_carry(args):
    snap, d, cash, ib, path = _state()
    t = carry.carry_roll(snap, path, cash, d)
    print(f"\nCarry and roll-down over {carry.HORIZON_DAYS} days if the curve is unchanged "
          f"(funding = average priced cash rate)\n")
    print(t.to_string(index=False))
    yt, xt = t.iloc[0], t.iloc[1]
    ratio = contracts.curve_ratio(yt["symbol"], 100 - yt["yield"], xt["symbol"], 100 - xt["yield"])
    print(f"\nA long earns the breakeven in bp: yields can rise that far before it loses money."
          f"\nDV01-neutral steepener (long {ratio:.2f} {yt['symbol']} per short {xt['symbol']}): "
          f"{yt['breakeven_bp'] - xt['breakeven_bp']:+.1f}bp of carry and roll per quarter.")


def cmd_spread(args):
    df = crossmarket.spread_history()
    s = crossmarket.summary(df)
    report.plot_spread(df, s, Path("output") / "au_us_10y_spread.png")
    print(f"\nAU vs US 10-year spread, {s['date']:%d %b %Y}\n")
    print(f"  AU 10y {s['au10']:.2f}%  US 10y {s['us10']:.2f}%  spread {s['spread_bp']:+.0f}bp")
    print(f"  3-month change {s['change_3m_bp']:+.0f}bp | 5-year average {s['mean_5y_bp']:+.0f}bp | "
          f"z-score {s['zscore_5y']:+.1f} | higher than {s['percentile_all']:.0%} of days since {s['start']:%Y}")
    print("\nChart: output/au_us_10y_spread.png")


def cmd_scorecard(args):
    snap = data.load_snapshot()
    log = pd.read_csv(tracking.LOG, dtype={"date": str, "meeting": str}) if tracking.LOG.exists() else pd.DataFrame()
    _write_scorecard(snap, log, reaction.fetch_inputs()[2], data.snapshot_date(snap))
    print("Wrote SCORECARD.md")


def cmd_events(args):
    releases = events.fetch_release_dates()
    dy = events.yield_changes()
    ev = events.build(releases, dy, events.surprises())
    ev.to_csv(Path("output") / "event_study.csv", index=False)
    print(f"\n3-year yield, {ev['released'].min():%b %Y} to {dy.index.max():%b %Y}: which days move the front end?\n")
    print(events.day_types(ev, dy).to_string(index=False))
    print("\nRelease-day move per 0.1ppt of surprise (surprise = change from previous reading)\n")
    fits = {}
    for kind, label in (("cpi", "Trimmed mean CPI, q/q"), ("labour", "Unemployment rate")):
        for ex in (False, True):
            f = events.sensitivity(ev, kind, exclude_2020=ex)
            fits.setdefault(kind, f)
            print(f"  {label + (' ex-2020' if ex else ''):30s} {f['slope'] / 10:+5.1f}bp  "
                  f"t = {f['t']:+.1f}  R2 = {f['r2']:.2f}  n = {f['n']}")
    report.plot_events(ev, fits, Path("output") / "event_study.png")
    print("\nChart: output/event_study.png")


def cmd_scenario(args):
    """Pre-CPI scenario map: trimmed mean outcome -> expected 3-year yield move -> P&L."""
    snap = data.load_snapshot()
    ev = events.build(events.fetch_release_dates(), events.yield_changes(), events.surprises())
    fit = events.sensitivity(ev, "cpi")
    when, label, previous = events.next_quarterly_cpi()
    yt = _front(snap, "YT")
    dv01 = contracts.dv01(yt["symbol"], yt["settle"])
    marks = ledger.mark_to_market(snap)
    table = events.scenario_map(fit, previous, yt["settle"], dv01, marks)
    print(f"\nScenario map for {label} CPI, released {when:%d %b %Y}")
    print(f"Previous trimmed mean: {previous:.1f}% q/q. Sensitivity {fit['slope'] / 10:+.1f}bp per 0.1ppt "
          f"(t = {fit['t']:.1f}); typical miss around the fitted move is +/-{fit['resid_sd']:.0f}bp.\n")
    print(table.to_string(index=False))
    print(f"\nP&L is for one long {yt['symbol']} (DV01 ${dv01:.0f})"
          + (" and for the open book." if not marks.empty else ". No open positions."))


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
    p = max(-1.0, min(1.0, p))  # more than 25bp priced is treated as a certain move
    market = {"cut": max(-p, 0), "hold": 1 - abs(p), "hike": max(p, 0)}
    table = pd.DataFrame({"Outcome": reaction.OUTCOMES})
    for name, v in views.items():
        table[name] = [f"{v['probs'][o]:.0%}" for o in reaction.OUTCOMES]
    table["Market"] = [f"{market[o]:.0%}" for o in reaction.OUTCOMES]
    probs = {"market": market, "model": views["Model"]["probs"], "model_nowcast": views["Model + nowcast"]["probs"]}
    return df, views, table, nowcast.nowcast_at(panel, d), probs, changes


SPECS = (("inflation and unemployment", reaction.BASE),
         ("plus previous move", reaction.INERTIA),
         ("plus GDP nowcast", reaction.NOWCAST))


def cmd_view(args):
    snap, d, cash, ib, path = _state()
    df, views, table, nc, _, _ = _model_vs_market(d, path)
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

    sub.add_parser("carry", help="carry and roll-down for 3-year and 10-year futures").set_defaults(f=cmd_carry)
    sub.add_parser("spread", help="AU vs US 10-year spread monitor").set_defaults(f=cmd_spread)
    sub.add_parser("scorecard", help="write SCORECARD.md from the ledger").set_defaults(f=cmd_scorecard)
    sub.add_parser("scenario", help="scenario map for the next quarterly CPI release").set_defaults(f=cmd_scenario)
    sub.add_parser("events", help="event study: 3-year yield moves on CPI and labour force days").set_defaults(f=cmd_events)

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
