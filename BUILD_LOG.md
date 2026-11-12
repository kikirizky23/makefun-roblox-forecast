# BUILD LOG — Roblox Up-and-Coming Top-10 Forecaster

Built 13 Nov 2026 (02:59–03:15 WIB) for the make.fun bounty "Predict
Roblox's Up-and-Coming top 10" ($500, 20 places, entries close 13 Nov 2026
13:00 WIB). Entry: `main.py` (Python 3.13, stdlib only, no network,
deterministic). No GitHub repo created, nothing submitted, nothing signed —
the parent agent handles repo creation and submission.

## 1. Kit discovery

- Bounty page: https://make.fun/bounty/roblox-up-and-coming-forecast-01
  (slug `roblox-up-and-coming-forecast-01`, ends 13 Nov 2026 06:00 UTC).
- Kit: https://make.fun/c/roblox-up-and-coming-forecast-01/kit
  (4,595,122 bytes) → unpacked to `~/workspace/make-fun/kit-roblox/`.
- Read RULES.txt, competition.yaml, kit.json, input/task.json,
  input/NOTICE.txt and starter/main.ts (the house baseline) in full before
  writing any code. Key rules (kit wins over the brief):
  - Output `/output/forecast.json`, ≤ 1,048,576 bytes, exactly two keys:
    `probabilities` (rows `{universe_id, place 1–10, p 0–1}`, ≤ 100 rows
    per place, each place's sum ≤ 1) and `top10` (exactly 10 distinct ids).
  - Unnamed remainder of a place is shared equally over every game on a
    read's chart not named at that place.
  - Score: mean over window reads of Σ places ln(p), floored at
    ln(0.0001) = −9.210340 per place. Must beat the best house entry
    (baseline or house model) to take a prize.
  - Env overrides `$MAKEFUN_INPUT_DIR` / `$MAKEFUN_OUTPUT_DIR`.
  - Program runs twice on the same history; outputs must be byte-identical.

## 2. Data reconnaissance (kit practice history: 34 archive reads)

- 2026-10-30T07:36:30Z → 2026-11-07T20:53:19Z; gaps 3.6–9.8 h (median
  6.4 h); 92–99 games per read; 207 distinct games; `details` always null
  (archive source), votes/genres null.
- Top-10 membership is sticky but order churns: median 6 of 10 positions
  change occupant between consecutive reads; #1 changed 8 times among 7
  games.
- Rank-velocity autocorrelation (first half vs second half): **0.058** —
  momentum does not persist. Log-playing-growth vs rank change: **−0.015**
  — player-count growth does not predict rank changes. Last-interval
  velocity vs next-interval velocity: **0.210** — weak short-term momentum.
- Rank transitions are near-random-walk with mean reversion at the top:
  rank 1 → mean next rank 1.3; rank 5 → 5.1; rank 10 → 14.4 (only 44%
  stay top 10); rank 20 → 21.3. Rank-change std ≈ 1.1–1.3 ranks/hour.
- P(top 10 next read): rank 1–5: 1.00; 6: 0.94; 8: 0.79; 9: 0.64;
  10: 0.44; 11: 0.24; 15: 0.06; 20: 0.00.
- 98 of 207 games disappeared before the end; disappeared spans median
  65 h, max 187 h — games leave the chart far sooner than the 28-day cap.
  Only 6% of out-of-chart games re-enter within ~2 reads. Brand-new
  entrants: ~3.4 per read, median entry rank 87, 1% enter top 10 directly.
- Top-10 playing counts vary 3× by UTC hour (diurnal) — another reason raw
  playing counts are a poor rank signal.
- Conclusion: forecast a time-averaged marginal from rank history alone;
  do not use playing counts; use heavily damped velocity only.

## 3. Practice-answer reconstruction (validation harness)

- The practice window (2026-11-07T23:00Z → 2026-11-10T23:00Z) holds 10
  reads of the public archive github.com/Kenny-Dhub/roblox-snapshots.
  Fetched `data/discover/latest.json` at the 10 commits in that window
  (e911f5edde … 5fc84bcfb3) via the GitHub API + raw.githubusercontent.com;
  extracted the `explore:up-and-coming` candidates in rank order
  (`tests/reconstruct_practice.py` → `tests/practice_answer.json`).
- Wrote an exact scorer from RULES.txt (`tests/score.py`).
- **Calibration: the house baseline (starter/main.ts) scored on the
  reconstructed reads gives exactly −43.512039, typical p 0.012891, 6.40
  top-10 hits, 53.50 agreement — identical to the figures published in
  RULES.txt.** The reconstruction and scorer are therefore exact.

## 4. Model

`main.py` — empirical transition kernel + time averaging (see the module
docstring and README.md for the full description):

1. Kernel: for a game at rank r at time t, the distribution of its rank at
   t + h, from every pair of reads (nearest read to each target gap
   3–120 h), binned by starting rank and gap, smoothed over neighbouring
   rank bins and horizon centres, weighted toward recent reads
   (half-life 4 days).
2. Per-game probabilities averaged over horizons 0–72 h in 4 h steps
   (uniform: the score is the mean over the window's hourly reads), with
   damped velocity drift (shrink 0.40, saturating over 8 h).
3. 28-day age-out taper from `details.created` when present.
4. Per-place named mass capped at 0.88; rows below p = 0.002 dropped;
   ≤ 100 rows per place. `top10` = highest total P(in top 10).
5. Fallbacks: Gaussian spread if the kernel has no data; baseline shape
   if no usable history; never crashes without writing a forecast.

## 5. Tuning (A/B on 4 rolling-origin backtest cuts + practice window)

Backtest cuts: train on reads [0..k), score on the next 10 reads, k = 14,
19, 24, 29. Baseline = house-baseline shape on the same cuts.

| variant | backtest mean | practice |
|---|---|---|
| baseline | −44.999 | −43.512 |
| kernel v1 (smooth .15, vel .21, cap .92) | −39.450 | −34.959 |
| kernel + end smoothing 0.30 | −38.670 | −34.943 |
| kernel + smooth .30, cap .88, vel .40 | **−38.341** | **−34.712** |
| cap 1.00 (no cap) | −40.961 | −37.171 |
| min_p 0.001 | −40.389 | −36.560 |

Final constants: `END_SMOOTH = 0.30`, `PLACE_CAP = 0.88`,
`VELOCITY_SHRINK = 0.40`. Neighbourhood checks (cap .85/.90, vel .30/.50,
smooth .25/.35, horizon step 2 h) all landed within ±0.3 nats — tuning
stopped there to avoid overfitting 5 evaluations. The kernel beats the
baseline on 4 of 5 evaluations; the exception is cut 29, a window where
the chart barely moved (baseline −32.91 vs kernel −33.38) — expected, as
the kernel spreads mass for later hours that never materialise there.
Top-10 hits and agreement are identical to the baseline everywhere
(6.40 / 53.50 on practice): all of the gain is better-calibrated
probabilities, not a different top 10.

## 6. Final verification (`tests/verify.py` — ALL CHECKS PASSED)

- Kit input: rc 0, **0.28 s**, output 37,600 bytes, deterministic (two
  runs byte-identical, sha256 d7506fa3bfb10051…), format valid (exactly 2
  keys, ≤ 100 rows/place, per-place sums ≤ 1, no duplicate rows, top10
  exactly 10 distinct ids).
- Practice score: **−34.712339** (baseline −43.512039, **+8.80 nats**),
  typical p 0.0311, 6.40 hits, 53.50 agreement.
- Edge cases, all rc 0 with a valid forecast file: empty history, 1 read,
  malformed lines, missing task.json, no games, `ok: false`.
- Stress: synthetic 150-read hourly history with `details` (created
  dates) resembling the final input — rc 0, **0.68 s**, 57,138 bytes,
  format valid. (One stress-generator bug found and fixed during testing:
  the first version never actually shuffled the chart; the fixed version
  shuffles properly.)

## 7. Deliverables

- `~/workspace/make-fun/roblox-forecast/main.py` — the forecaster
- `~/workspace/make-fun/roblox-forecast/README.md`
- `~/workspace/make-fun/roblox-forecast/LICENSE` (MIT, Copyright (c) 2026 Rizki)
- `~/workspace/make-fun/roblox-forecast/BUILD_LOG.md` (this file)
- `~/workspace/make-fun/roblox-forecast/tests/` — `score.py`,
  `backtest.py`, `tune.py`, `verify.py`, `reconstruct_practice.py`,
  `practice_answer.json` (reconstructed practice reads, for re-validation)

## 8. Notes for submission (parent agent)

- Repo needs: public GitHub repo created after the competition opened
  (11 Nov 2026 06:00 UTC), `main.py` at root, MIT LICENSE, plus
  `.makefun/owner.json` naming wallet
  698wsufdpgSDFVdsxy7HgsEQyZQXRuMB5MPghQzAJoET (same claim-file pattern
  as the art bounties — this account signs in with Discord, not GitHub).
- This bounty is escrowed ($500 USDC on Solana), so submission needs the
  "Sign and submit" wallet signature from Rizki's Phantom (wallet
  698w…JoET), like the art bounties.
- Transcript bundle: this session built the entry (session started inside
  the competition window, 12 Nov ~20:00 UTC). Bundle with the kit CLI
  (`node cli/makefun.mjs bundle`) the same way as the art bundles, with
  `--muse-usage` from muse.db and the same redactions.
- Caveat: the practice score (+8.8 nats over baseline) is a proxy. The
  final is scored on 72 hourly reads against the house baseline AND the
  house model forecast (superbot, effort max) — the model forecast's score
  is unknown, so beating the baseline does not guarantee a prize. The
  forecast is calibrated, not overconfident, which is the best available
  posture against an unknown house model.
