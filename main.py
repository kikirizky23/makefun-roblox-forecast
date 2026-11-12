#!/usr/bin/env python3
"""Forecast Roblox's Up-and-Coming chart top 10 (make.fun bounty
roblox-up-and-coming-forecast-01).

Reads /input/history.ndjson + /input/task.json (or $MAKEFUN_INPUT_DIR), writes
/output/forecast.json (or $MAKEFUN_OUTPUT_DIR). Python 3.13, standard library
only, no network, fully deterministic.

Method
------
The chart is a noisy, mean-reverting rank process with heavy turnover: the
next read looks much like the last one, rank momentum barely persists
(velocity autocorrelation ~0.06 over halves, ~0.21 over one step), and games
leave the chart after a median ~65 h on it. The forecast is therefore a
time-averaged marginal, not a single predicted order:

1. From the history, build an empirical transition kernel: for a game at rank
   r at time t, the distribution of its rank at t + h, estimated from every
   pair of reads in the history (binned by starting rank and gap, smoothed
   over neighbouring bins). The kernel captures drift, diffusion and
   dropout (leaving the chart) in one object, with no parametric assumption.
2. For each candidate game, average that kernel over horizons h spanning the
   scored window (uniform: the score is the mean over the window's hourly
   reads), with a small, heavily damped velocity adjustment (last-interval
   rank velocity, shrunk by ~0.21 and saturating over ~8 h).
3. Games whose Roblox `created` date puts them past the chart's 28-day age
   limit inside the window are tapered out after that time.
4. Per place, the probabilities are the averaged kernel marginals, capped so
   a place never names more than CAP of its mass; the rest stays unnamed and
   is shared by the scorer over every game on a read's chart not named at
   that place (which is exactly how new entrants and surprises are covered).
5. top10 is the ten games with the highest total probability of being in the
   top 10 at a random hour of the window.

Fallbacks: if the history is too thin for a kernel (fewer than 2 reads), use
a Gaussian spread around the last chart; if there is no usable history at
all, fall back to the house-baseline shape on whatever games exist; the
program never crashes without writing a forecast file.
"""

import json
import math
import os
import sys

# ---------------------------------------------------------------- tunables
PLACES = 10
HORIZON_STEP = 4.0          # hours between kernel horizons
HORIZON_MAX = 72.0          # scored window length (hours)
PLACE_CAP = 0.88            # max named probability mass per place
MIN_P = 0.002               # drop rows below this probability
POOL_RECENT_HOURS = 72.0    # how far back a dropout may still be a candidate
POOL_TOP_RANK = 30          # a dropout qualifies if it was this good recently
VELOCITY_SHRINK = 0.40      # shrinkage of last-interval velocity
VELOCITY_TAU = 8.0          # hours over which velocity drift saturates
RECENCY_HALF_LIFE_DAYS = 4.0  # half-life for weighting old transitions
END_SMOOTH = 0.30           # blur over neighbouring ending ranks (fixes
                            # lumpiness from small per-cell samples)
MAX_ROWS_PER_PLACE = 100    # format limit
AGE_LIMIT_DAYS = 28.0       # chart only lists games published < 28 days ago

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

# rank bins: single ranks 1..10, then widening bands
RANK_EDGES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 16, 21, 26, 31, 41, 51,
              66, 81, 101, 10 ** 9]
# ending categories: ranks 1..10, then bands, then OUT
END_EDGES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 21, 31, 51, 101, 10 ** 9]
N_END = len(END_EDGES)  # last category is OUT (index N_END - 1)


def rank_bin(rank):
    """Bin index for a starting rank (0-based)."""
    for i in range(len(RANK_EDGES) - 1):
        if RANK_EDGES[i] <= rank < RANK_EDGES[i + 1]:
            return i
    return len(RANK_EDGES) - 2


def end_cat(rank):
    """Ending category for a rank, or OUT when the game left the chart."""
    if rank is None:
        return N_END - 1
    for i in range(len(END_EDGES) - 1):
        if END_EDGES[i] <= rank < END_EDGES[i + 1]:
            return i
    return N_END - 2


def parse_time(s):
    """ISO 8601 UTC -> epoch seconds (float). Returns None on failure."""
    try:
        if not isinstance(s, str):
            return None
        t = s.strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        from datetime import datetime
        return datetime.fromisoformat(t).timestamp()
    except Exception:
        return None


def load_history(input_dir):
    """Return (reads, task): reads = [{t, chart: {uid: rank}, size}], task."""
    task = {"places": PLACES, "output": "forecast.json"}
    try:
        with open(os.path.join(input_dir, "task.json"), "r",
                  encoding="utf-8") as f:
            t = json.load(f)
        if isinstance(t, dict):
            task.update(t)
    except Exception:
        pass
    reads = []
    try:
        with open(os.path.join(input_dir, "history.ndjson"), "r",
                  encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                except Exception:
                    continue
                if not isinstance(snap, dict) or not snap.get("ok", True):
                    continue
                chart = snap.get("up_and_coming")
                if not isinstance(chart, list) or not chart:
                    continue
                t = parse_time(snap.get("at"))
                if t is None:
                    continue
                ranks = {}
                for row in chart:
                    if not isinstance(row, dict):
                        continue
                    try:
                        uid = int(row["universe_id"])
                        rank = int(row["rank"])
                    except Exception:
                        continue
                    if rank >= 1 and uid not in ranks:
                        ranks[uid] = rank
                if ranks:
                    reads.append({"t": t, "chart": ranks,
                                  "size": len(ranks)})
                # keep created dates when a read carries details
                det = snap.get("details")
                if isinstance(det, dict):
                    for k, v in det.items():
                        if isinstance(v, dict) and v.get("created"):
                            ct = parse_time(v["created"])
                            if ct is not None:
                                CREATED[int(k)] = ct
    except Exception:
        pass
    reads.sort(key=lambda r: r["t"])
    return reads, task


CREATED = {}  # universe_id -> created epoch seconds (filled by load_history)


# ------------------------------------------------------------------- kernel
def build_kernel(reads):
    """Empirical transition kernel.

    hist[(rank_bin, horizon_bin)] = [counts over ending categories].
    Horizon bins are centred on HORIZON_CENTERS (hours); a transition with
    gap g contributes to the nearest centre with a triangular weight shared
    with its neighbour, so the kernel is smooth in h.
    """
    centers = [0.0, 3.0, 6.0, 9.0, 12.0, 18.0, 24.0, 32.0, 40.0, 48.0, 56.0,
               64.0, 72.0, 84.0, 96.0, 120.0]
    hist = {}
    n = len(reads)
    if n < 2:
        return hist, centers
    t_end = reads[-1]["t"]
    # For each read i, pair it with reads near each target gap. This keeps
    # the transition count linear in the number of reads.
    targets = [3.0, 6.0, 9.0, 12.0, 18.0, 24.0, 36.0, 48.0, 60.0, 72.0, 96.0,
               120.0]
    for i in range(n):
        ti = reads[i]["t"]
        ci = reads[i]["chart"]
        # recency weight: transitions ending near the end count more
        for target in targets:
            # nearest read at gap ~= target (binary search by time)
            lo, hi = i + 1, n - 1
            best, best_gap = None, None
            while lo <= hi:
                mid = (lo + hi) // 2
                gap = (reads[mid]["t"] - ti) / 3600.0
                if best_gap is None or abs(gap - target) < abs(best_gap - target):
                    best, best_gap = mid, gap
                if gap < target:
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best is None:
                continue
            gap_h = (reads[best]["t"] - ti) / 3600.0
            if gap_h <= 0 or abs(gap_h - target) > max(4.0, 0.35 * target):
                continue
            cj = reads[best]["chart"]
            # recency weight of the later read
            age_days = max(0.0, (t_end - reads[best]["t"]) / 86400.0)
            w_time = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
            # horizon bin (nearest centre, triangular split)
            cidx = min(range(len(centers)),
                       key=lambda k: abs(centers[k] - gap_h))
            for uid, r0 in ci.items():
                r1 = cj.get(uid)  # None -> OUT
                rb = rank_bin(r0)
                ec = end_cat(r1)
                key = (rb, cidx)
                cell = hist.get(key)
                if cell is None:
                    cell = hist[key] = [0.0] * N_END
                cell[ec] += w_time
    return hist, centers


def kernel_dist(hist, centers, r0, h):
    """P(ending category) for a game at rank r0 after h hours.

    Interpolates between neighbouring horizon centres and neighbouring rank
    bins. Returns a list of N_END probabilities (sums to 1) or None when the
    history carries no usable transition for this (rank, horizon).
    """
    if not hist:
        return None
    rb = rank_bin(r0)
    # horizon interpolation weights
    if h <= centers[0]:
        hws = [(0, 1.0)]
    elif h >= centers[-1]:
        hws = [(len(centers) - 1, 1.0)]
    else:
        j = 0
        while j < len(centers) - 1 and centers[j + 1] < h:
            j += 1
        span = centers[j + 1] - centers[j]
        f = (h - centers[j]) / span if span > 0 else 0.0
        hws = [(j, 1.0 - f), (j + 1, f)]
    # rank-bin smoothing: centre 0.5, neighbours 0.25 each
    rws = [(rb, 0.5)]
    if rb > 0:
        rws.append((rb - 1, 0.25))
    if rb < len(RANK_EDGES) - 2:
        rws.append((rb + 1, 0.25))
    acc = [0.0] * N_END
    total_w = 0.0
    for rbi, rw in rws:
        for hi, hw in hws:
            cell = hist.get((rbi, hi))
            if cell is None:
                continue
            s = sum(cell)
            if s <= 0:
                continue
            w = rw * hw * s
            total_w += w
            for k in range(N_END):
                acc[k] += w * cell[k] / s
    if total_w <= 0:
        return None
    dist = [a / total_w for a in acc]
    if END_SMOOTH > 0:
        # blur over neighbouring ending ranks (categories 0..10 cover ranks
        # 1..10 and the 11-20 band): the per-cell samples are small, so raw
        # histograms are lumpy (e.g. a dip at one place).
        s = END_SMOOTH
        blurred = dist[:]
        for k in range(11):
            lo = dist[k - 1] if k > 0 else dist[k]
            hi = dist[k + 1] if k < 10 else dist[k]
            blurred[k] = (1.0 - 2 * s) * dist[k] + s * lo + s * hi
        # renormalise the blurred head so total mass is unchanged
        head_old = sum(dist[:11])
        head_new = sum(blurred[:11])
        if head_old > 0 and head_new > 0:
            f = head_old / head_new
            for k in range(11):
                blurred[k] *= f
        dist = blurred
    return dist


def gauss_rank_dist(r0, h):
    """Fallback rank distribution (no kernel data): Normal around r0 with
    diffusion sigma growing as sqrt(h), over ending categories 1..10.
    Returns P(end rank = k) for k = 1..10 (sums to < 1; the rest is spread
    over lower ranks / out of chart)."""
    sigma = 2.0 + 1.2 * math.sqrt(max(h, 0.0))
    out = []
    for k in range(1, 11):
        # P(k - 0.5 < X <= k + 0.5) for X ~ N(r0, sigma)
        z1 = (k - 0.5 - r0) / sigma
        z0 = (k + 0.5 - r0) / sigma
        p = 0.5 * (math.erf(z1 / math.sqrt(2.0)) -
                   math.erf(z0 / math.sqrt(2.0)))
        out.append(max(p, 0.0))
    return out


# ------------------------------------------------------------------ forecast
def forecast(reads, task):
    places = int(task.get("places", PLACES) or PLACES)
    if places < 1:
        places = PLACES
    output = task.get("output") or "forecast.json"
    if not reads:
        return {"probabilities": [], "top10": list(range(1, places + 1))}, output

    last = reads[-1]
    t_last = last["t"]
    hist_until = parse_time(task.get("history_until")) or t_last
    h_first = max(0.0, (hist_until - t_last) / 3600.0)
    horizons = []
    h = h_first
    while h <= HORIZON_MAX + 1e-9:
        horizons.append(h)
        h += HORIZON_STEP
    if not horizons:
        horizons = [h_first]

    # per-game series for velocity + last-seen info
    series = {}
    for r in reads:
        for uid, rank in r["chart"].items():
            series.setdefault(uid, []).append((r["t"], rank))
    last_chart = last["chart"]

    # candidate pool: last chart + recent top-ranked dropouts
    pool = {}  # uid -> (rank0, age_hours_since_seen)
    for uid, rank in last_chart.items():
        pool[uid] = (rank, 0.0)
    for uid, pts in series.items():
        if uid in pool:
            continue
        t_seen, r_seen = pts[-1]
        age = (t_last - t_seen) / 3600.0
        if age <= POOL_RECENT_HOURS and r_seen <= POOL_TOP_RANK:
            pool[uid] = (r_seen, age)

    # last-interval velocity (ranks/hour) per game, from its last two reads
    velocity = {}
    for uid, pts in series.items():
        if len(pts) >= 2:
            (t1, r1), (t2, r2) = pts[-2], pts[-1]
            dt = (t2 - t1) / 3600.0
            if 0 < dt <= 24.0:
                velocity[uid] = (r2 - r1) / dt

    hist, centers = build_kernel(reads)

    # per-game, per-place probabilities (averaged over horizons)
    per_game = {}  # uid -> [p(place 1..places)]
    for uid, (r0, age) in pool.items():
        v = velocity.get(uid, 0.0)
        probs = [0.0] * places
        for h in horizons:
            hh = h + age  # dropouts have already been drifting
            # damped velocity drift, saturating over VELOCITY_TAU
            drift = (v * VELOCITY_SHRINK * VELOCITY_TAU *
                     (1.0 - math.exp(-hh / VELOCITY_TAU))) if v else 0.0
            r_eff = min(max(r0 + drift, 1.0), 200.0)
            dist = kernel_dist(hist, centers, r_eff, hh)
            if dist is None:
                g = gauss_rank_dist(r_eff, hh)
                for k in range(places):
                    probs[k] += g[k]
            else:
                for k in range(places):
                    probs[k] += dist[k]
        per_game[uid] = [p / len(horizons) for p in probs]

    # Age-out taper: a game whose created date puts it past the chart's
    # 28-day age limit inside the window is scaled down by its mean taper
    # weight over the horizons (1 until 12 h before the limit, 0 after
    # 12 h past it, linear in between).
    if CREATED:
        for uid in list(per_game.keys()):
            created = CREATED.get(uid)
            if created is None:
                continue
            age_out = created + AGE_LIMIT_DAYS * 86400.0
            ws = []
            for h in horizons:
                t_h = hist_until + h * 3600.0
                if t_h <= age_out - 12 * 3600:
                    ws.append(1.0)
                elif t_h >= age_out + 12 * 3600:
                    ws.append(0.0)
                else:
                    ws.append((age_out + 12 * 3600 - t_h) / (24 * 3600))
            wmean = sum(ws) / len(ws) if ws else 1.0
            if wmean < 1.0:
                per_game[uid] = [p * wmean for p in per_game[uid]]

    # assemble rows, honouring the per-place cap and row limit
    probabilities = []
    for k in range(places):
        rows = [(uid, per_game[uid][k]) for uid in per_game
                if per_game[uid][k] >= MIN_P]
        rows.sort(key=lambda x: (-x[1], x[0]))
        rows = rows[:MAX_ROWS_PER_PLACE]
        total = sum(p for _, p in rows)
        if total > PLACE_CAP:
            scale = PLACE_CAP / total
            rows = [(uid, p * scale) for uid, p in rows]
        for uid, p in rows:
            probabilities.append({"universe_id": int(uid), "place": k + 1,
                                  "p": round(p, 6)})
    probabilities.sort(key=lambda r: (r["place"], -r["p"], r["universe_id"]))

    # top10: highest total probability of being in the top 10
    totals = {uid: sum(per_game[uid]) for uid in per_game}
    ranked = sorted(totals.keys(),
                    key=lambda u: (-totals[u], pool[u][0], u))
    top10 = [int(u) for u in ranked[:places]]
    if len(top10) < places:
        # pad with the smallest unused positive integers (degenerate input)
        used = set(top10)
        cand = 1
        while len(top10) < places:
            if cand not in used:
                top10.append(cand)
                used.add(cand)
            cand += 1
    return {"probabilities": probabilities, "top10": top10}, output


def fallback_forecast(reads, task):
    """House-baseline shape on the last chart (never crashes)."""
    places = int(task.get("places", PLACES) or PLACES)
    output = task.get("output") or "forecast.json"
    if not reads:
        return {"probabilities": [], "top10": list(range(1, places + 1))}, output
    chart = sorted(reads[-1]["chart"].items(), key=lambda kv: kv[1])
    ids = [uid for uid, _ in chart]
    probs = []
    for place in range(1, places + 1):
        if place <= len(ids):
            probs.append({"universe_id": int(ids[place - 1]), "place": place,
                          "p": 0.5})
        if place == 1:
            if len(ids) > 1:
                probs.append({"universe_id": int(ids[1]), "place": 1,
                              "p": 0.3})
            continue
        if place - 2 >= 0 and place - 2 < len(ids):
            probs.append({"universe_id": int(ids[place - 2]), "place": place,
                          "p": 0.15})
        if place < len(ids):
            probs.append({"universe_id": int(ids[place]), "place": place,
                          "p": 0.15})
    top10 = [int(u) for u in ids[:places]]
    if len(top10) < places:
        used = set(top10)
        cand = 1
        while len(top10) < places:
            if cand not in used:
                top10.append(cand)
                used.add(cand)
            cand += 1
    return {"probabilities": probs, "top10": top10}, output


def main():
    try:
        reads, task = load_history(INPUT_DIR)
    except Exception:
        reads, task = [], {"places": PLACES, "output": "forecast.json"}
    try:
        result, output = forecast(reads, task)
    except Exception:
        try:
            result, output = fallback_forecast(reads, task)
        except Exception:
            result = {"probabilities": [],
                      "top10": list(range(1, PLACES + 1))}
            output = "forecast.json"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, output)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
