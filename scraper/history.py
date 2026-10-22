"""Team-level history (2022 onwards) for Monte Carlo FC.

For each league it downloads, once, every finished match of the previous
seasons since 2022 (score, shots, shots on target and xG per team) and turns
them into:
- a starting rating for every team in the current season (what its previous
  seasons say, shrunk by how much that rating usually carries over from one
  season to the next in this league; promoted teams start from the average of
  promoted teams);
- the league's home advantage and each team's own home effect, shrunk by how
  consistent that effect is from season to season;
- a per-team summary of past seasons (final position, points, xG for/against).
"""
import json
import math
import os
import random
import time

FIRST_YEAR = 2022
METRICS = ("attSh", "defSh", "attAcc", "defAcc", "attQ", "defQ")


def _load(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))


def past_seasons(lg):
    cur = (lg.get("details") or {}).get("selectedSeason") or ""
    out = []
    for s in lg.get("allAvailableSeasons") or []:
        try:
            y = int(str(s)[:4])
        except ValueError:
            continue
        if s != cur and y >= FIRST_YEAR:
            out.append(s)
    return sorted(out)


def collect(lid, lg, fm, team_stats, cache_dir, budget, delay):
    """Download past seasons (team stats only). Returns ({season: {match_id: rec}}, new)."""
    new = 0
    data = {}
    for season in past_seasons(lg):
        path = os.path.join(cache_dir, f"{lid}_{season.replace('/', '-')}.json")
        c = _load(path) or {"fixtures": None, "m": {}}
        if c["fixtures"] is None:
            s_lg = fm("leagues", id=lid, season=season.replace("/", "%2F"))
            fx = []
            for m in (s_lg or {}).get("fixtures", {}).get("allMatches") or []:
                st = m.get("status") or {}
                if st.get("finished") and not st.get("cancelled"):
                    fx.append([str(m["id"]), st.get("utcTime"), str(m["home"]["id"]), m["home"].get("shortName") or m["home"]["name"],
                               str(m["away"]["id"]), m["away"].get("shortName") or m["away"]["name"], st.get("scoreStr") or ""])
            c["fixtures"] = fx
            _save(path, c)
            time.sleep(delay)
        for mid, utc, h, hn, a, an, score in c["fixtures"]:
            if mid in c["m"]:
                continue
            if new >= budget:
                break
            try:
                hg, ag = [int(x.strip()) for x in score.split("-")]
            except ValueError:
                c["m"][mid] = None
                continue
            rec = {"t": utc, "h": h, "hn": hn, "a": a, "an": an, "hg": hg, "ag": ag, "s": None}
            try:
                d = fm("matchDetails", matchId=mid)
                st = team_stats((d or {}).get("content") or {})
                if "total_shots" in st:
                    rec["s"] = [st["total_shots"], st.get("ShotsOnTarget", [0, 0]), st.get("expected_goals", [-1, -1])]
            except Exception:  # noqa: BLE001
                pass
            c["m"][mid] = rec
            new += 1
            if new % 50 == 0:
                _save(path, c)
            time.sleep(delay + random.random() * 0.3)
        _save(path, c)
        data[season] = {k: v for k, v in c["m"].items() if v}
        if new >= budget:
            break
    return data, new


# ------------------------------------------------------------------ summaries
def _season_table(recs):
    """Per team aggregates for one season."""
    T = {}
    for r in recs:
        s = r.get("s")
        for side, tid, name, gf, ga in ((0, r["h"], r["hn"], r["hg"], r["ag"]), (1, r["a"], r["an"], r["ag"], r["hg"])):
            t = T.setdefault(tid, {"name": name, "n": 0, "pts": 0, "gf": 0, "ga": 0, "ns": 0, "shf": 0, "sha": 0, "sotf": 0, "sota": 0,
                                   "xgf": 0, "xga": 0, "nx": 0, "h": [0, 0, 0], "a": [0, 0, 0]})
            t["n"] += 1
            t["gf"] += gf
            t["ga"] += ga
            t["pts"] += 3 if gf > ga else 1 if gf == ga else 0
            hx = None
            if s:
                o = 1 - side
                t["ns"] += 1
                t["shf"] += s[0][side]; t["sha"] += s[0][o]
                t["sotf"] += s[1][side]; t["sota"] += s[1][o]
                if s[2][side] >= 0 and s[2][o] >= 0:
                    t["nx"] += 1
                    t["xgf"] += s[2][side]; t["xga"] += s[2][o]
                    hx = (s[2][side], s[2][o])
            q = hx or (gf, ga)
            bucket = t["h"] if side == 0 else t["a"]
            bucket[0] += 1; bucket[1] += q[0]; bucket[2] += q[1]
    return T


def _ratings(T):
    """Log ratings relative to the league average, for the six model metrics."""
    L = {"sh": 0, "sot": 0, "xg": 0, "ns": 0, "nxsh": 0}
    for t in T.values():
        L["sh"] += t["shf"]; L["sot"] += t["sotf"]; L["ns"] += t["ns"]
        if t["nx"]:
            L["xg"] += t["xgf"]; L["nxsh"] += t["shf"] * t["nx"] / max(1, t["ns"])
    if not L["ns"] or not L["sh"]:
        return {}
    sh_pg = L["sh"] / L["ns"]
    acc = L["sot"] / L["sh"]
    q = L["xg"] / L["nxsh"] if L["nxsh"] else None
    out = {}
    for tid, t in T.items():
        if t["ns"] < 10:
            continue
        r = {
            "attSh": math.log((t["shf"] / t["ns"]) / sh_pg),
            "defSh": math.log((t["sha"] / t["ns"]) / sh_pg),
            "attAcc": math.log(max(t["sotf"], 1) / max(t["shf"], 1) / acc),
            "defAcc": math.log(max(t["sota"], 1) / max(t["sha"], 1) / acc),
        }
        if q and t["nx"] >= 10:
            r["attQ"] = math.log(max(t["xgf"], 0.5) / max(t["shf"] * t["nx"] / t["ns"], 1) / q)
            r["defQ"] = math.log(max(t["xga"], 0.5) / max(t["sha"] * t["nx"] / t["ns"], 1) / q)
        out[tid] = r
    return out


def _slope(pairs):
    num = sum(x * y for x, y in pairs)
    den = sum(x * x for x, _ in pairs)
    return max(0.0, min(1.0, num / den)) if den > 0 else 0.5


def summarize(seasons, current_recs, current_teams):
    """seasons: {season: {mid: rec}} (past, complete); current_recs: list of recs in the same format.
    current_teams: list of FotMob team ids in the current season."""
    order = sorted(seasons)
    tables = {s: _season_table(list(seasons[s].values())) for s in order}
    ratings = {s: _ratings(tables[s]) for s in order}

    # season-to-season carry-over (regression slope) per metric
    b = {}
    for m in METRICS:
        pairs = []
        for s0, s1 in zip(order, order[1:]):
            for tid, r0 in ratings[s0].items():
                r1 = ratings[s1].get(tid)
                if r1 and m in r0 and m in r1:
                    pairs.append((r0[m], r1[m]))
        b[m] = round(_slope(pairs), 3) if len(pairs) >= 15 else 0.5

    # promoted teams: average first-season rating
    promo = {m: [] for m in METRICS}
    for s0, s1 in zip(order, order[1:]):
        for tid, r1 in ratings[s1].items():
            if tid not in tables[s0]:
                for m in METRICS:
                    if m in r1:
                        promo[m].append(r1[m])
    promo_mean = {m: (sum(v) / len(v) if v else 0.0) for m, v in promo.items()}

    # starting rating for the current season
    priors = {}
    last = order[-1] if order else None
    prev = order[-2] if len(order) > 1 else None
    for tid in current_teams:
        r_last = ratings.get(last, {}).get(tid) if last else None
        r_prev = ratings.get(prev, {}).get(tid) if prev else None
        pr = []
        for m in METRICS:
            if r_last and m in r_last:
                base = r_last[m] if not (r_prev and m in r_prev) else 0.75 * r_last[m] + 0.25 * r_prev[m]
                v = b[m] * base
            else:
                v = promo_mean[m]
            pr.append(round(math.exp(v), 3))
        priors[tid] = pr

    # home advantage: league (by season) and team effects
    all_s = order + ["cur"]
    tables["cur"] = _season_table(current_recs)
    season_rows, hq_tot, aq_tot, w_tot = [], 0.0, 0.0, 0.0
    for s in all_s:
        recs = list(seasons[s].values()) if s != "cur" else current_recs
        if not recs:
            continue
        hq = aq = g = 0.0
        for r in recs:
            x = r.get("s")
            hx = x[2][0] if x and x[2][0] >= 0 else r["hg"]
            ax = x[2][1] if x and x[2][1] >= 0 else r["ag"]
            hq += r["hg"] + hx
            aq += r["ag"] + ax
            g += r["hg"] + r["ag"]
        season_rows.append([s if s != "cur" else "atual", len(recs), round(hq / aq, 3) if aq else None, round(g / len(recs), 2)])
        w = 0.6 ** (len(all_s) - 1 - all_s.index(s))  # recent seasons weigh more
        hq_tot += w * hq / len(recs)
        aq_tot += w * aq / len(recs)
        w_tot += w
    league_hfa = round(hq_tot / aq_tot, 3) if aq_tot else 1.2

    def logit(p):
        p = min(max(p, 0.05), 0.95)
        return math.log(p / (1 - p))

    d = {}  # season -> {tid: home-away share difference minus league mean}
    for s in all_s:
        T = tables.get(s) or {}
        vals = {}
        for tid, t in T.items():
            h, a = t["h"], t["a"]
            if h[0] >= 5 and a[0] >= 5 and h[1] + h[2] > 0 and a[1] + a[2] > 0:
                vals[tid] = logit(h[1] / (h[1] + h[2])) - logit(a[1] / (a[1] + a[2]))
        if vals:
            mean = sum(vals.values()) / len(vals)
            d[s] = {k: v - mean for k, v in vals.items()}
    # reliability: correlation of team effects in consecutive seasons
    pairs = []
    for s0, s1 in zip(all_s, all_s[1:]):
        for tid, v in (d.get(s0) or {}).items():
            if tid in (d.get(s1) or {}):
                pairs.append((v, d[s1][tid]))
    rel = 0.0
    if len(pairs) >= 15:
        mx = sum(p[0] for p in pairs) / len(pairs)
        my = sum(p[1] for p in pairs) / len(pairs)
        cov = sum((x - mx) * (y - my) for x, y in pairs)
        vx = sum((x - mx) ** 2 for x, _ in pairs)
        vy = sum((y - my) ** 2 for _, y in pairs)
        rel = max(0.0, cov / math.sqrt(vx * vy)) if vx > 0 and vy > 0 else 0.0
    team_hfa = {}
    for tid in current_teams:
        vs = [d[s][tid] for s in all_s if s in d and tid in d[s]]
        if not vs:
            continue
        k = len(vs)
        shrink = (k * rel) / (1 + (k - 1) * rel) if rel > 0 else 0.0
        team_hfa[tid] = round(shrink * sum(vs) / k, 3)

    # past seasons per team: [season, position, points, games, xG for/g, xG against/g, goals for, goals against]
    past = {}
    for s in order:
        T = tables[s]
        ranked = sorted(T.items(), key=lambda kv: (-kv[1]["pts"], -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"]))
        for pos, (tid, t) in enumerate(ranked, 1):
            past.setdefault(tid, []).append([s, pos, t["pts"], t["n"],
                                             round(t["xgf"] / t["nx"], 2) if t["nx"] else None,
                                             round(t["xga"] / t["nx"], 2) if t["nx"] else None, t["gf"], t["ga"]])
    return {
        "seasons": season_rows, "hfa": league_hfa, "hfaRel": round(rel, 3), "b": b,
        "metrics": list(METRICS), "prior": priors, "teamHfa": team_hfa,
        "past": {tid: past[tid] for tid in current_teams if tid in past},
        "nMatches": sum(len(v) for v in seasons.values()),
    }
