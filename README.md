# RBA pricing and trade expression engine

Tracks what the Australian rates market is pricing for the RBA, and keeps a
timestamped paper trading record of views expressed in ASX 24 futures.

The question this project asks is not "can I out-forecast the market" but
"what is priced, where and why might I disagree, what is the right instrument,
and how much do I risk". Every note and trade is committed before the event it
is a view on, losers included.

## What it does

| Step | Module | Output |
|---|---|---|
| Collect end-of-day settlements for 30-day interbank (IB), 3-year (YT) and 10-year (XT) futures, plus the RBA cash rate | `rbaengine/data.py` | `data/snapshots/YYYY-MM-DD.csv` |
| Strip a meeting-by-meeting implied cash rate path | `rbaengine/implied_path.py` | `output/implied_path_*.csv`, `.png` |
| Value contracts, compute DV01, size positions by dollar risk | `rbaengine/contracts.py` | |
| Record and mark paper trades | `rbaengine/ledger.py` | `ledger/trades.csv` |
| Write the weekly note | `rbaengine/report.py` | `notes/YYYY-MM-DD.md` |

## Method

**Implied path.** An IB contract settles at 100 minus the average interbank
overnight cash rate over its month. If a decision takes effect on day *d* of an
*N*-day month:

    avg = ((d - 1) * r_pre + (N - d + 1) * r_post) / N

Where the month after a meeting has no meeting of its own, that contract is
used as a direct read of `r_post`. Otherwise the equation is solved within the
meeting month, chaining `r_pre` from the previous meeting. Rate changes take
effect the business day after the decision.

**Contract maths.** Bond futures are quoted as 100 minus yield and valued as a
notional 6% semi-annual coupon bond with $100,000 face (6 periods for YT, 20 for
XT), so DV01 is recomputed from the price each day. IB is linear at $24.66 per
basis point.

**Ledger rules.** Fills are at the official settlement price of the latest
snapshot, never a price of my choosing. Each trade has a stop, target and
rationale at entry. Position size comes from a fixed dollar risk at the stop.

## Limitations

- The stripped path is a risk-neutral expectation and contains a term premium.
  "Probability of a 25bp move" is a pricing convention, reliable for the next
  meeting or two and progressively less so further out.
- IB futures settle on the interbank overnight cash rate, not the target. Any
  spread between the two is treated as zero.
- End-of-day data only. No transaction costs, margin or futures roll.
- Back-month IB contracts trade thinly; their settlements are exchange marks.

## Usage

    pip install -r requirements.txt
    python -m rbaengine update          # daily: fetch, strip, chart, mark positions
    python -m rbaengine size YTZ2026 --stop-bp 10 --risk 10000
    python -m rbaengine open YTZ2026 long 35 --stop 94.965 --target 95.265 --rationale "..."
    python -m rbaengine close 1
    python -m rbaengine note            # weekly note skeleton with section 1 filled in
    python -m pytest

## Roadmap

- [x] Implied path, contract maths, ledger, note generator
- [ ] Reaction function: ordered probit on inflation and unemployment gaps, fed by the GDP nowcast
- [ ] Test whether that signal adds information beyond market pricing (Brier score, encompassing regression)
- [ ] Event study: front-end move per unit of CPI and labour force surprise
- [ ] Carry and roll-down, curve and cross-market expressions
