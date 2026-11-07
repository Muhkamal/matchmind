# Paper-trading the diversified trend-following system

This is the forward test of the one strategy in the whole research project
that showed a real, literature-consistent, increasingly-significant edge
(tests #7, #14, #15, #16): daily time-series momentum across a diversified
futures/FX basket. Backtested on history through 2024-03-28; this is how you
track it forward on live data, which this sandbox cannot reach itself.

## Why this runs on YOUR computer, not here

This environment's network access is locked to GitHub and package registries
-- it cannot reach Yahoo Finance, a broker, or any live price feed. The
historical data used for the whole backtest (pysystemtrade's GitHub CSVs)
stops at 2024-03-28 and isn't updated daily. So there is no way to run this
forward test from inside a Claude session automatically. Instead, you run two
small scripts yourself, whenever you want a fresh signal (monthly fits the
system's own rebalance cadence).

## One-time setup (on your own computer)

```
pip install yfinance pandas
```

## Each month (or whenever you check in)

```
python3 download_prices.py      # pulls live data from Yahoo Finance -> prices.csv
python3 monthly_signal.py prices.csv   # prints this month's target positions
```

The second command tells you, for each of the 17 instruments: LONG, SHORT,
or flat, and a `target_weight` -- a relative risk-weighting, NOT a literal
number of contracts. Two instruments with weight 3.0 and 0.03 both mean
"take this position," just sized very differently to hit the same
volatility contribution; translating that into actual contracts/lots needs
your own account size and broker's point values, which this script has no
way to know. **For paper trading specifically, the only thing that actually
matters right now is the DIRECTION call** -- log what it said and compare to
what price actually did, the same way the backtest was judged.

## What to log, every time you run it (even when nothing changes)

Date, the full table from `last_signal.csv`, and whether it was a rebalance
window. Keep this in one place over time -- that running log is what lets
anyone judge in 6-12 months whether live behavior matched the backtest's
Sharpe ~0.6-0.8 / modest-but-real edge, or didn't.

## Rules -- frozen, do not tune based on what you see live

This is the exact discipline this whole project was built on: the rule
(EWMAC... actually TSMOM(252), see monthly_signal.py's docstring) is frozen.
If live results look bad for a few months, that is not, by itself, a signal
to change the lookback, the universe, or the position sizing -- a real
Sharpe-0.7-ish strategy has losing stretches lasting a year or more; that's
already priced into the honest expectation. Changing the rule after seeing
live results defeats the entire point of a forward test. If you want to test
a genuinely different rule, that's a new pre-registered test, not a tweak to
this one.

## Files in this folder

- `download_prices.py` -- pulls live data (run on your machine)
- `monthly_signal.py` -- computes the frozen signal from that data
- `prices.csv` / `last_signal.csv` -- generated each time you run the above

## Known caveats, stated plainly

- The 17-instrument universe here is a liquid, easy-to-track subset of the
  86-130 instrument universe actually validated in the backtest -- expect
  somewhat more noise/lower Sharpe than the full-universe backtest numbers,
  since you're running fewer, correlated markets.
- `JPY` on Yahoo Finance (`JPY=X`) is USD/JPY, i.e. USD is the base currency
  -- opposite convention from EUR/GBP/AUD pairs here. The sign still means
  "which way the system wants exposure," just be aware when eyeballing it.
- This is a paper test of a borderline-but-promising backtest result, not a
  proven live edge or financial advice. Treat early months as "does the
  live signal behave sanely and match expectations," not as a verdict.
