"""Backtest cases for model v2 (opponent-adjusted ratings from the detailed history).

Same output format as backtest_build.py, plus L.rat (ratings per team as of the match week) and
L.ratHome. Usage: python scraper/backtest_build_v2.py OUT.jsonl [half_life_days] [k_prior]
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_build as bb  # noqa: E402
import history  # noqa: E402
import ratings  # noqa: E402
import lineup_strength  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RICH = os.path.join(ROOT, "data", "cache", "rich")


FULL = os.path.join(ROOT, "data", "cache", "history_full")


def load_league(lid):
    seasons = {}
    for f in glob.glob(os.path.join(RICH, f"{lid}_*.json")):
        season = os.path.basename(f)[len(str(lid)) + 1:-5].replace("-", "/")
        full = {}
        fp = os.path.join(FULL, os.path.basename(f))
        if os.path.exists(fp):
            full = {k: v for k, v in json.load(open(fp))["m"].items() if v}
        recs = []
        for mid, r in json.load(open(f)).items():
            if not r.get("sh") and mid in full and full[mid].get("s"):
                s_ = full[mid]["s"]
                r = dict(r, t_tot=[[s_[0][0], s_[1][0], s_[2][0]], [s_[0][1], s_[1][1], s_[2][1]]])
            ts = int(datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp())
            # team totals for the engine's current-season aggregates
            sh = [[0, 0, 0.0], [0, 0, 0.0]]
            for s in r.get("sh") or []:
                sh[s[1]][0] += 1; sh[s[1]][1] += s[3]; sh[s[1]][2] += s[2]
            rec = {"ts": ts, "h": r["h"], "hn": r["hn"], "a": r["a"], "an": r["an"], "hg": r["hg"], "ag": r["ag"],
                   "t": sh if r.get("sh") else r.get("t_tot"), "rich": r}
            recs.append(rec)
        seasons[season] = sorted(recs, key=lambda x: x["ts"])
    return seasons


def main(out_path, half_life=120.0, k_prior=6.0):
    out = open(out_path, "w")
    leagues = sorted({int(os.path.basename(f).split("_")[0]) for f in glob.glob(os.path.join(RICH, "*.json"))})
    if os.environ.get("LIDS"):  # only some leagues, e.g. LIDS=57,61
        leagues = [l for l in leagues if str(l) in os.environ["LIDS"].split(",")]
    for lid in leagues:
        seasons = load_league(lid)
        order = sorted(seasons)
        allm = []
        for s in order:
            for r in seasons[s]:
                v = ratings.match_values(r["rich"])
                if v:
                    allm.append({"ts": r["ts"], "h": r["h"], "a": r["a"], "vh": v[0], "va": v[1], "w": v[2]})
        allm.sort(key=lambda m: m["ts"])
        odds = bb.load_odds(lid)
        cache_week = {}
        n = 0
        trk = lineup_strength.Tracker()
        for r in seasons[order[0]]:
            trk.add(r["rich"], r["ts"])
        for si, season in enumerate(order[1:], 1):
            past = {s: {str(i): bb.hist_rec(r) for i, r in enumerate(seasons[s])} for s in order[:si]}
            recs = seasons[season]
            team_ids = sorted({r["h"] for r in recs} | {r["a"] for r in recs})
            tidx = {t: i for i, t in enumerate(team_ids)}
            names = {}
            for r in recs:
                names[r["h"]] = r["hn"]; names[r["a"]] = r["an"]
            for i, m in enumerate(recs):
                prior = recs[:i]
                full_h = history.summarize(past, [bb.hist_rec(r) for r in prior], team_ids)
                hist = {k: full_h[k] for k in ("hfa", "prior", "teamHfa")}
                week = m["ts"] // (7 * 86400)
                if week not in cache_week:
                    asof = week * 7 * 86400
                    # promoted teams: shrink toward the history's promoted-team prior
                    pri = {t: full_h["prior"][t] for t in team_ids if t in full_h["prior"]}
                    pri6 = {t: [p[0], p[2] * p[0], p[4] * p[0], p[1], p[3] * p[1], p[5] * p[1]] for t, p in pri.items()}
                    fitted, home = ratings.fit(allm, asof, half_life, k_prior, prior=pri6)
                    cache_week[week] = ({t: ratings.engine_rating(v) for t, v in fitted.items()}, [round(x, 4) for x in home])
                rat, home = cache_week[week]
                matches = []
                for r in prior:
                    t = r["t"]
                    matches.append([r["ts"], tidx[r["h"]], tidx[r["a"]], r["hg"], r["ag"]] +
                                   ([int(t[0][0]), int(t[0][1]), round(t[0][2], 2), int(t[1][0]), int(t[1][1]), round(t[1][2], 2)] if t else [-1] * 6))
                pos_avg = trk.pos_averages()
                pl = m["rich"].get("pl") or []
                xh = [p_[0] for p_ in pl if p_[1] == 0 and p_[2]]
                xa = [p_[0] for p_ in pl if p_[1] == 1 and p_[2]]
                fh = trk.factors(m["h"], xh, pos_avg)
                fa = trk.factors(m["a"], xa, pos_avg)
                trk.add(m["rich"], m["ts"])
                L = {"v": 3, "teams": [[t, names[t]] for t in team_ids], "matches": matches, "players": [], "next": [],
                     "hist": hist, "hasXg": True, "rat": {t: rat[t] for t in team_ids if t in rat}, "ratHome": home,
                     "lu": [round(fh[0], 3), round(fh[1], 3), round(fa[0], 3), round(fa[1], 3)]}
                day = datetime.fromtimestamp(m["ts"], timezone.utc).date()
                o = bb.find_odds(odds, day, m["hn"], m["an"], m["hg"], m["ag"]) if odds else None
                if o:
                    o = {k: v for k, v in o.items() if k not in ("d", "h", "a")}
                n += 1
                out.write(json.dumps({"lid": lid, "season": season, "i": i, "L": L,
                                      "m": {"h": m["h"], "a": m["a"], "hg": m["hg"], "ag": m["ag"], "t": m["t"]}, "odds": o}) + "\n")
        print(lid, "jogos", n, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 120.0, float(sys.argv[3]) if len(sys.argv) > 3 else 6.0)
