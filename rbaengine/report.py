"""Chart and weekly note output."""
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

INK, MUTED, GRID, SERIES = "#1f2933", "#6b7280", "#e5e7eb", "#2f6fde"


def plot_path(path: pd.DataFrame, cash_rate: float, asof: date, out: Path) -> None:
    xs = [asof] + list(path["effective"])
    ys = [cash_rate] + list(path["implied_rate"])
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    ax.axhline(cash_rate, color=MUTED, lw=1, ls=(0, (4, 3)))
    # extend the final level so the last meeting's rate is visible as a step
    ax.step(xs + [xs[-1] + timedelta(days=40)], ys + [ys[-1]], where="post", color=SERIES, lw=2)
    ax.plot(xs[1:], ys[1:], "o", color=SERIES, ms=5, mec="white", mew=1.5)

    ax.annotate(f"Cash rate today {cash_rate:.2f}%", (xs[-1], cash_rate), xytext=(0, -12),
                textcoords="offset points", ha="right", color=MUTED, fontsize=9)
    peak = max(range(1, len(ys)), key=lambda i: ys[i])
    for i in sorted({1, peak, len(ys) - 1}):
        # the final label sits under its extended step, clear of the previous level
        offset = (22, -15) if i == len(ys) - 1 and i != peak else (0, 9)
        ax.annotate(f"{ys[i]:.2f}%", (xs[i], ys[i]), xytext=offset, textcoords="offset points",
                    ha="center", color=INK, fontsize=9)

    ax.set_title(f"Market-implied RBA cash rate path, as of {asof:%d %b %Y}",
                 loc="left", color=INK, fontsize=12, pad=14)
    ax.text(0, 1.015, "Rate priced to prevail after each Monetary Policy Board meeting, per cent",
            transform=ax.transAxes, color=MUTED, fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.set_ylim(min(ys) - 0.15, max(ys) + 0.15)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.tick_params(colors=MUTED, length=0, labelsize=9)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    fig.text(0.01, 0.01, "Source: ASX 30-day interbank cash rate futures settlements, RBA. "
             "Includes term premium; not a pure forecast.", color=MUTED, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)


def plot_events(events: pd.DataFrame, fits: dict, out: Path) -> None:
    """Release-day move in the 3-year yield against the surprise, one panel per release."""
    panels = [("cpi", "Quarterly CPI", "Change in trimmed mean inflation, q/q (ppt)"),
              ("labour", "Labour force", "Change in unemployment rate (ppt)")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), dpi=150)
    for ax, (kind, title, xlabel) in zip(axes, panels):
        ev = events[(events["kind"] == kind) & events["surprise"].notna()]
        ax.axhline(0, color=GRID, lw=1)
        ax.axvline(0, color=GRID, lw=1)
        ax.scatter(ev["surprise"], ev["move_bp"], s=26, color=SERIES, edgecolor="white", linewidth=0.8, alpha=0.9)
        f = fits[kind]
        # clip the fitted line to the bulk of the data so COVID outliers do not stretch it
        lo, hi = ev["surprise"].quantile([0.02, 0.98])
        xs = pd.Series([lo, hi])
        ax.plot(xs, f["intercept"] + f["slope"] * xs, color=INK, lw=1.5)
        ax.set_xlim(lo - 0.1, hi + 0.1)
        ax.set_title(title, loc="left", color=INK, fontsize=11, pad=20)
        ax.text(0, 1.02, f"{f['slope'] / 10:+.1f}bp per 0.1ppt, t = {f['t']:.1f}, R² = {f['r2']:.2f}, n = {f['n']}",
                transform=ax.transAxes, color=MUTED, fontsize=8.5, va="bottom")
        ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
        ax.grid(color=GRID, lw=0.6)
        ax.tick_params(colors=MUTED, length=0, labelsize=9)
        for side in ax.spines.values():
            side.set_visible(False)
    axes[0].set_ylabel("3-year yield change on release day (bp)", color=MUTED, fontsize=9)
    fig.suptitle("Front-end reaction to data: surprise measured against the previous reading",
                 x=0.01, ha="left", color=INK, fontsize=12)
    fig.text(0.01, 0.01, "Source: ABS release dates, RBA tables F2, G1, H5. Revised data; no consensus forecasts, "
             "so slopes understate true sensitivity. Axes exclude the largest 2020 moves.", color=MUTED, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None._"
    head = "| " + " | ".join(df.columns) + " |"
    rule = "|" + "|".join("---" for _ in df.columns) + "|"
    body = ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, rule, *body])


def path_table(path: pd.DataFrame) -> pd.DataFrame:
    t = path[["meeting", "implied_rate", "change_bp", "prob_25bp", "cum_bp"]].copy()
    t["meeting"] = [f"{m:%d %b %Y}" for m in t["meeting"]]
    t["implied_rate"] = t["implied_rate"].map("{:.2f}%".format)
    t["change_bp"] = t["change_bp"].map("{:+.1f}".format)
    t["prob_25bp"] = t["prob_25bp"].map("{:+.0%}".format)
    t["cum_bp"] = t["cum_bp"].map("{:+.1f}".format)
    t.columns = ["Meeting", "Implied rate", "Priced move (bp)", "As share of 25bp", "Cumulative (bp)"]
    return t


NOTE = """# RBA pricing note: {asof:%d %B %Y}

_Prices are ASX 24 official settlements for {asof:%d %b %Y}. Paper portfolio; not investment advice._

## 1. What is priced

Cash rate: **{cash:.2f}%**. 3-year futures yield: **{y3:.2f}%**. 10-year futures yield: **{y10:.2f}%**. 3s10s: **{curve:.0f}bp**.

{path}

![Implied path](../output/implied_path_{asof}.png)

### Next meeting scenarios ({contract} IB contract)

{scen}

### Reaction function vs market, next meeting

{model}

_The model is an ordered probit on the inflation and unemployment gaps. It is a
benchmark, not an edge: out of sample it only marginally beats historical frequencies._

## 2. My view

<!-- Where do you disagree with the table above, and what is the specific
reason the market might be wrong (premium, overreaction, misread data detail)?
If you have no reason, say so and do not trade. -->

TODO

## 3. Trade

<!-- Instrument, direction, entry, stop, target, contracts, dollar risk at stop,
and why this instrument rather than the alternatives. -->

TODO

## 4. Open positions

{marks}
"""


def write_note(asof: date, cash: float, y3: float, y10: float, path: pd.DataFrame,
               scen: pd.DataFrame, marks: pd.DataFrame, model: pd.DataFrame,
               out_dir: Path = Path("notes")) -> Path:
    out = out_dir / f"{asof}.md"
    if out.exists():
        raise FileExistsError(f"{out} already exists; notes are never overwritten")
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(NOTE.format(
        asof=asof, cash=cash, y3=y3, y10=y10, curve=(y10 - y3) * 100,
        path=md_table(path_table(path)),
        contract=scen["contract"].iloc[0] if not scen.empty else "n/a",
        scen=md_table(scen.drop(columns="contract")) if not scen.empty else "_No contract._",
        marks=md_table(marks),
        model=md_table(model),
    ), encoding="utf-8")
    return out
