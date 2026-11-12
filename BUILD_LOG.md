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

## 9. v2 (13 Nov 2026, ~06:30–07:30 WIB) — measured improvements

**Why:** the v1 entry is live but ranked 705 of 2,416+ agents (dev log
score −35.0545); the $500 prize pays the top 20, so v1 needed a real
improvement, not a marginal tweak. Entries close 13 Nov 2026 13:00 WIB.

**New data first.** The public archive
(github.com/Kenny-Dhub/roblox-snapshots) has snapshots through
2026-11-12T18:57Z. Fetched the 7 post-practice reads (11-10 → 11-12) via
the GitHub API contents endpoint (raw.githubusercontent times out here)
→ `~/workspace/make-fun/roblox-v2/tests/extended_answer.json`. Full
history for v2 validation: kit 34 reads + practice 10 + extended 7 =
**51 reads, 2026-10-30 → 2026-11-12**. Expanded harness
(`~/workspace/make-fun/roblox-v2/tests/exp.py`): 15 rolling cuts
(train on reads [0..k), score the next 10 reads, k = 12, 14, …, 40) +
the practice window + a recent 7-read window (train to 11-10, score
11-10 → 11-12). v1 on this harness: **mean −38.404, practice −34.712,
recent −37.706** (baseline mean −47.787). All v2 decisions are made on
the 15-cut mean, with practice + recent as guards against overfitting a
single window.

**Diagnostics that shaped v2.**
- Oracle marginal (exact future per-place frequencies on the practice
  window): **−13.179** vs v1 −34.712 — ~21 nats of headroom, but the
  oracle is unachievable; it bounds the problem, not the target.
  Per-place oracle entropy 0.8–1.83 nats (3–7 distinct games per place)
  vs v1 typical p 0.031 (~32 games) — v1 is far too diffuse *in the
  wrong places*, but see the temperature result below.
- Feature correlations (conditional on current rank, practice window):
  time-on-chart/age +0.161 at 24 h (older games decline); top_trending
  rank −0.092; playing share −0.079; top_playing_now ~0 overall but a
  split test (ranks 6–15) showed P(top10 later) 0.60 vs 0.39 on it.
- New-entrant share in the top 10: 11.6% across cuts, 8% practice,
  2.9% recent. v1's unnamed mass (~0.13/place, capped at 0.88 named)
  already matches that rate — the tail calibration is right, so the
  lever is ranking *within* the named set, not the tail.
- The archive carries `playing` in every chart row and
  `top_trending` / `top_playing_now` on all 51 reads; `details` always
  null (archive source).

**What was tested and what happened** (all on the 15-cut mean;
practice / recent in brackets; v1 = −38.404 / −34.712 / −37.706).
- Temperature sharpening (global γ = 1.2/1.4/1.7/2.0): mean *worse*
  (−38.53 at γ 1.2, −41.30 at γ 2.0) even though practice improved
  slightly at γ 1.2. **Rejected** — the kernel's diffuseness is honest
  uncertainty, not miscalibration.
- Per-place temperature (sharpen places 1–3 only): mean tied at best
  (−37.57 at γ 1.2) but practice worse. **Rejected.**
- Age drift (ranks/day added to r_eff): 0.5 → −38.51, 1.0 → −40.10
  (too strong), but **0.1 → −38.25 and 0.2 → −38.34** — small and
  consistent. **Kept at 0.2** (jointly re-tuned, see below).
- Playing-growth (log playing ratio over 24 h, ranks subtracted):
  0.5 → −38.39, 1.0 → −38.33, **2.0 → −38.13, 4.0 → −37.92**,
  6.0 → −38.18. 12 h window worse. **Kept at 4.0 / 24 h** — the
  strongest single new signal. (Note: v1's "growth does not predict"
  conclusion was an unconditional linear correlation; as a conditional
  rank offset inside the kernel it helps — the A/B is the arbiter.)
- Top-playing-now bonus (1/2/3 ranks, all ranks or borderline 6–15
  only): all worse (−38.54 to −38.82). **Rejected.**
- Top-trending bonus, playing-share level: all worse. **Rejected.**
- Windowed velocity (12 h / 24 h instead of last interval): worse.
  **Rejected.** Recency half-life 2 d worse, 7 d tied. **Kept 4 d.**
- New-game offset (±1/±2 ranks for games first seen < 1 day): worse.
  **Rejected.** END_SMOOTH 0.15/0.0 and PLACE_CAP 0.95: worse.
  **Rejected.**
- Asymmetric velocity shrink (on top of growth 4 + age 0.2): rise
  0.2 / fall 0.8 → **−37.570** (rise 0.2 / fall 0.6 → −37.638;
  rise 0.3 / fall 0.8 → −37.565; symmetric 0.40 → −37.764). A rise
  mean-reverts, a fall persists. **Kept at rise 0.2 / fall 0.8**
  (the 0.2/0.3-rise difference is noise; 0.2 has the better practice
  and recent scores).
- Joint re-tune (growth × age with asymmetric velocity): growth 4.0 /
  age 0.3 → −37.567, growth 4.0 / age 0.2 → **−37.570**,
  growth 2.0 / age 0.2 → −37.677. **Final: growth 4.0, age 0.2.**

**Final v2 constants** (in `main.py`, § "v2 (measured, kept)"):
`AGE_DRIFT = 0.2`, `GROWTH_COEF = 4.0`, `GROWTH_WINDOW_HOURS = 24.0`,
`VEL_RISE_SHRINK = 0.2`, `VEL_FALL_SHRINK = 0.8`. Everything else is
v1's. `load_history` now also captures per-read `playing` and the
`top_playing_now` / `top_trending` ranks, and records `FIRST_SEEN`
(first appearance in the history) for the age feature.

**Final v2 numbers** (verified on the shipped `main.py`):
| window | v1 | v2 | gain |
|---|---|---|---|
| 15-cut backtest mean | −38.404 | **−37.570** | +0.834 |
| practice window | −34.712 | **−34.531** | +0.181 |
| recent 7-read window | −37.706 | **−37.352** | +0.354 |
| (house baseline, 15-cut mean) | −47.787 | — | v2 is +10.22 over baseline |

Per-cut v2 (vs v1): 12: −37.29 (−39.35), 14: −36.72 (−37.71),
16: −38.83 (−41.72), 18: −36.03 (−36.14), 20: −41.34 (−40.71),
22: −40.87 (−40.84), 24: −42.68 (−44.17), 26: −36.44 (−38.29),
28: −35.00 (−36.42), 30: −34.26 (−36.41), 32: −36.33 (−37.65),
34: −34.77 (−35.08), 36: −35.19 (−35.13), 38: −37.48 (−37.91),
40: −40.32 (−38.55). v2 wins 10 of 15 cuts; the losses (18, 20, 36,
40) are the windows where the chart moved least or the growth signal
misfired — the wins are larger than the losses.

**Verification of the shipped file** (kit input, `main.py` run twice):
- Runtime 0.17–0.18 s; two runs byte-identical (deterministic).
- Output format identical to v1: exactly two keys (`probabilities`,
  `top10`), 649 rows, per-place named mass 0.83–0.88 (capped), all
  p in (0, 1], rows sorted by place then −p, top10 exactly 10 distinct
  ids.
- No network, stdlib only, same input/output contract as v1.

**Honest caveat for the parent:** this is a real, measured improvement
(+0.83 nats on the 15-cut mean, +0.18 practice, +0.35 recent), but it
is NOT the "big jump" the live rank (705 of 2,416+, dev −35.05) would
need to reach the top 20 on its own. The evidence is consistent that
most of the oracle gap (~21 nats) is irreducible churn — new games,
random rank movement, and the kernel already being well calibrated
(temperature, tail, and cap changes all failed). The three kept
features are genuine predictive signal, each validated on held-out
windows, and they do not overfit the practice window (the mean, not
practice, drove the decisions). If the final window behaves like the
backtests, v2 should score ~0.5–1 nat better than v1; whether that
moves the rank materially depends on how tightly packed the field is
around rank 705, which is unknowable from here.

**Work files:** `~/workspace/make-fun/roblox-v2/` — `v2exp.py`
(experiment module), `tests/exp.py` (expanded harness), `tests/postproc.py`
(temperature/cap), `tests/feat.py` + `tests/feat2.py` (feature A/B),
`tests/combo.py`, `tests/joint.py`, `tests/velgrid.py`,
`tests/placetemp.py`, `tests/diag.py` + `tests/diag3.py`,
`tests/fetch_extended.py`, `tests/extended_answer.json`,
`tests/practice_answer.json` (copied from the v1 tests).

## 10. Notes for resubmission (parent agent)

- This is a **resubmission** of the existing repo
  https://github.com/kikirizky23/makefun-roblox-forecast (v1 HEAD
  af80b01b706163d136229eefe3c3d57b1b72294b): update `main.py`,
  `README.md`, `BUILD_LOG.md` in place, keep `.makefun/owner.json`
  untouched, new commit on top. Do NOT create a new repo.
- Transcript bundle: include **both** sessions — the v1 build session
  (3c42713d-1450-4467-91cc-27e6977bf2f0, 69 turns) AND this v2 session
  (5bde63e7-b15f-401b-8017-818a444c575d). The v2 session wrote/edited
  `main.py`, `README.md` and `BUILD_LOG.md` with the write/edit tools
  in substantial edits, and the v1 session authored the rest — together
  they cover the shipped code (the chess lesson: a bundle with only one
  session failed the 30% transcript-coverage gate). Use both usage rows
  from muse.db. Same kit CLI, `--no-discover --no-family`, harness Muse,
  model ipnext/avocado-5.17-v2, same redactions as the art bundles.
- This bounty is escrowed ($500), so the resubmission needs Rizki's
  Phantom "Sign and submit" (wallet 698w…JoET) — or confirm whether he
  wants to submit it himself (he submitted v1 himself at 03:17 WIB) or
  wants the agent to submit it like chess. **Entries close 13 Nov 2026
  13:00 WIB** — the resubmission must land before then; the most recent
  submission before the deadline is the final entry.
- The v1 entry is already live (dev score −35.0545, rank 705). If the
  resubmission fails its checks, the v1 entry stands — resubmitting does
  not delete v1 unless the new one is accepted.
