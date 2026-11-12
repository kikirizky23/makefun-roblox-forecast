# Roblox Up-and-Coming Top-10 Forecaster

Entry for the make.fun bounty **"Predict Roblox's Up-and-Coming top 10"**
($500, 20 places; entries close 13 Nov 2026 13:00 WIB).

## What it does

`main.py` (Python 3.13, standard library only, no network, deterministic)
reads the chart history at `/input` (`history.ndjson` + `task.json`, or the
`$MAKEFUN_INPUT_DIR` / `$MAKEFUN_OUTPUT_DIR` overrides) and writes one file,
`/output/forecast.json`: for each of the top 10 places, a probability for
each game it names being at that place, plus its best guess at the top 10 in
order. That single forecast is scored against every hourly read of the chart
over the 72 hours after entries close.

## How it forecasts

The chart is a noisy, mean-reverting rank process with heavy turnover: the
next read looks much like the last one, rank momentum barely persists, and
games leave the chart after a median ~65 hours on it. So the program
forecasts a **time-averaged marginal**, not one predicted order:

1. **Empirical transition kernel.** From every pair of reads in the history
   it estimates, for a game at rank *r* at time *t*, the distribution of its
   rank at *t + h* — binned by starting rank and gap, smoothed over
   neighbouring bins, weighted toward recent reads. The kernel captures
   drift, diffusion and dropout (leaving the chart) in one object, with no
   parametric assumption. Player-count growth does *not* predict rank
   changes (correlation ≈ 0 in the history), so it is deliberately not used.
2. **Time averaging.** Each candidate game's kernel is averaged over
   horizons spanning the scored window (uniform: the score is the mean over
   the window's hourly reads), with a small, heavily damped velocity
   adjustment — the last-interval rank velocity, shrunk and saturating over
   ~8 hours (one-step velocity correlation is only ~0.21).
3. **Age-out taper.** A game whose Roblox `created` date puts it past the
   chart's 28-day age limit inside the window is tapered out after that
   time (when `details` are present in the history).
4. **Honest probabilities.** Per place, the probabilities are the averaged
   kernel marginals, capped at 0.88 of named mass; the rest stays unnamed
   and is shared by the scorer over every game on a read's chart not named
   at that place — which is exactly how new entrants and surprises are
   covered. `top10` is the ten games with the highest total probability of
   being in the top 10 at a random hour of the window.

If the history is too thin for a kernel (fewer than 2 reads) it falls back
to a Gaussian spread around the last chart; if there is no usable history at
all it falls back to the house-baseline shape. It never crashes without
writing a forecast file.

## How to run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
# writes out/forecast.json
```

In the grader's container it runs as `python3 /bot/main.py`, reading
`/input` and writing `/output/forecast.json`. Runtime on the kit's practice
history is ~0.3 s; on a synthetic 150-read hourly history, ~0.7 s — far
inside the 300 s / 1 CPU / 1024 MB limits. Two runs on the same history
produce byte-identical output.

## Validation (see BUILD_LOG.md for full detail)

- Practice window (reconstructed from the public archive, verified by
  reproducing the house baseline's published score **exactly**,
  −43.512039): this forecaster scores **−34.712** — 8.8 nats better than
  the house baseline, typical probability 0.031 per place vs 0.013.
- Rolling-origin backtests on the kit history (4 cuts): mean **−38.34**
  vs baseline −45.00.
- Format, determinism, edge cases (empty / 1-read / malformed / missing
  `task.json` / no games) and a 150-read stress test all pass.

## License

MIT — see `LICENSE`.
