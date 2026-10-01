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
| Estimate an RBA reaction function and backtest it out of sample | `rbaengine/reaction.py` | `output/reaction_backtest.csv` |
| Re-run my [GDP nowcast](https://github.com/robcambridge/aus-gdp-nowcast) on fresh ABS data at any vintage date | `rbaengine/nowcast.py` | `data/nowcast_panel.csv` |
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

**Reaction function.** An ordered probit for cut / hold / hike at each scheduled
meeting since 1998, on three inputs: trimmed mean inflation less 2.5, the
unemployment rate less its trailing five-year average, and the two-quarter
change in trimmed mean inflation. Each meeting only sees data published before
it. The backtest refits each January on prior years and predicts that year.

Result (176 meetings, 2010 to September 2026, multi-class Brier score, lower is better):

| Forecast | Brier |
|---|---|
| Ordered probit | 0.386 |
| Historical frequencies | 0.390 |
| Always hold | 0.466 |
| Ordered probit plus direction of previous move | 0.414 |
| Ordered probit plus GDP nowcast | 0.413 |

The model barely beats a know-nothing forecast, and adding policy inertia makes
it worse out of sample despite a strong in-sample coefficient. I read this as:
public macro data alone tells you little about the next meeting that base rates
do not. The model is used as a consistency check on my own view, not as a
signal to trade against the market.

**Does the GDP nowcast help?** The nowcast enters as a growth gap: the
bridge-average nowcast for the latest unpublished quarter less average growth
over the previous ten years, computed point-in-time at each meeting date. In
sample its coefficient is positive and significant (z = 2.5): stronger growth,
more hikes. Out of sample it is a wash before COVID (Brier 0.323 with the
nowcast against 0.327 without, 2010 to 2019) and clearly worse after, because
the 2020-21 rebound produced record growth gaps while the RBA held at the
lower bound. The nowcast is therefore reported next to the base model in each
note but is not the headline.

**Trade expression.** `express` sizes an outright 3-year, an outright 10-year
and a DV01-neutral 3s10s steepener to the same dollar loss at the stop, then
shows P&L under parallel and non-parallel curve moves, so the choice of
instrument is explicit rather than assumed.

## Limitations

- The reaction function uses today's revised data rather than real-time
  vintages, and proxies full employment with a trailing average. It has not yet
  been scored against market-implied probabilities, which needs historical
  futures data.

- The stripped path is a risk-neutral expectation and contains a term premium.
  "Probability of a 25bp move" is a pricing convention, reliable for the next
  meeting or two and progressively less so further out.
- IB futures settle on the interbank overnight cash rate, not the target. Any
  spread between the two is treated as zero.
- End-of-day data only. No transaction costs, margin or futures roll.
- Back-month IB contracts trade thinly; their settlements are exchange marks.

## Usage

    pip install -r requirements.txt
    pip install --no-deps https://github.com/robcambridge/aus-gdp-nowcast/archive/f2a072959c8b2e3d5ce2ac507c58b5584b73efae.zip
    python -m rbaengine update          # daily: fetch, strip, chart, mark positions
    python -m rbaengine size YTZ2026 --stop-bp 10 --risk 10000
    python -m rbaengine open YTZ2026 long 35 --stop 94.965 --target 95.265 --rationale "..."
    python -m rbaengine close 1
    python -m rbaengine view            # reaction function: backtest, model vs market
    python -m rbaengine express --stop-bp 10 --risk 10000
    python -m rbaengine note            # weekly note skeleton with section 1 filled in
    python -m pytest

## Roadmap

- [x] Implied path, contract maths, ledger, note generator
- [x] Reaction function: ordered probit on inflation and unemployment gaps, backtested out of sample
- [x] Add the GDP nowcast as an input and test whether it improves the backtest
- [x] Compare outright and curve expressions at equal risk
- [ ] Test whether the model adds information beyond market pricing (needs historical futures data)
- [ ] Event study: front-end move per unit of CPI and labour force surprise
- [ ] Carry and roll-down, AU vs US cross-market expression
