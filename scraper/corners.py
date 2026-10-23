"""Corner kicks per team from FotMob match details: whole match and first half.

Cache: data/cache/corners/<league>_<season>.json = {match id: {"t", "h", "a", "hg", "ag", "c": [home, away, home 1st half, away 1st half]}}
(-1 when FotMob has no corner count). Usage: python scraper/corners.py LEAGUE_ID   (backfill since 2022, resumable)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import history  # noqa: E402

DIR = os.path.join(collect.ROOT, "data", "cache", "corners")
KEYS = ("corners", "corner_kicks", "Corners")


def _period(content, name):
    try:
        groups = content["stats"]["Periods"][name]["stats"]
    except (KeyError, TypeError):
        return None
    for g in groups:
        for s in g.get("stats") or []:
            if s.get("key") in KEYS or (s.get("title") or "").lower() == "corners":
                raw = s.get("rawStats") or s.get("stats") or []
                try:
                    if len(raw) == 2 and isinstance(raw[0], dict):
                        return [int(float(raw[0].get("value") or 0)), int(float(raw[1].get("value") or 0))]
                    if len(raw) == 2:
                        return [int(float(raw[0] or 0)), int(float(raw[1] or 0))]
                except (TypeError, ValueError):
                    return None
    return None


def extract(d):
    content = (d or {}).get("content") or {}
    full = _period(content, "All")
    first = _period(content, "FirstHalf")
    if not full:
        return None
    return full + (first or [-1, -1])


def path(lid, season):
    return os.path.join(DIR, f"{lid}_{season.replace('/', '-')}.json")


def load(p):
    try:
        return json.load(open(p))
    except Exception:  # noqa: BLE001
        return {}


def save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(data, open(p, "w"), separators=(",", ":"))


def process_season(lid, season, s_lg, budget=10**9, verbose=True):
    """Fetch corners for finished matches of one season not yet cached. Returns number fetched."""
    p = path(lid, season)
    data = load(p)
    ms = [m for m in (s_lg or {}).get("fixtures", {}).get("allMatches") or []
          if (m.get("status") or {}).get("finished") and not (m.get("status") or {}).get("cancelled")]
    new = miss = 0
    for m in ms:
        mid = str(m["id"])
        if mid in data:
            continue
        if new >= budget:
            break
        try:
            hg, ag = [int(x.strip()) for x in (m["status"].get("scoreStr") or "").split("-")]
        except ValueError:
            continue
        try:
            d = collect.fm("matchDetails", matchId=mid)
        except Exception as e:  # noqa: BLE001
            print("  falhou", mid, e, flush=True)
            continue
        c = extract(d)
        if c is None:
            miss += 1
            if verbose and miss == 1:
                try:
                    g = d["content"]["stats"]["Periods"]["All"]["stats"]
                    print("  chaves disponíveis:", [s_.get("key") for x in g for s_ in x.get("stats") or []][:60], flush=True)
                except Exception:  # noqa: BLE001
                    print("  sem bloco de estatísticas", flush=True)
        data[mid] = {"t": m["status"].get("utcTime"), "h": str(m["home"]["id"]), "a": str(m["away"]["id"]),
                     "hg": hg, "ag": ag, "c": c or [-1, -1, -1, -1]}
        new += 1
        if new % 100 == 0:
            save(p, data)
        time.sleep(collect.DELAY)
    if new:
        save(p, data)
    if verbose:
        print(lid, season, "jogos", len(ms), "novos", new, "sem escanteios", miss, flush=True)
    return new


def backfill(lid):
    lg = collect.fm("leagues", id=lid)
    cur = (lg.get("details") or {}).get("selectedSeason") or ""
    for season in history.past_seasons(lg) + [cur]:
        s_lg = lg if season == cur else collect.fm("leagues", id=lid, season=season.replace("/", "%2F"))
        process_season(lid, season, s_lg)


def all_records(lid):
    """Every cached match of a league with corners: list of dicts with ts."""
    import glob
    from datetime import datetime
    out = []
    for f in glob.glob(os.path.join(DIR, f"{lid}_*.json")):
        for mid, r in load(f).items():
            if r.get("c") and r["c"][0] >= 0 and r.get("t"):
                out.append(dict(r, ts=int(datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp())))
    return sorted(out, key=lambda x: x["ts"])


def model(lid, rat, half_life=240.0, k_prior=6.0, blend=0.4, now=None):
    """Team corner factors for the engine: {team: [attack, defence]} blended with shot ratings (rat from model v2),
    plus league base [home avg, away avg, first-half share] over the last 365 days."""
    import ratings
    recs = all_records(lid)
    if len(recs) < 100:
        return None, None
    now = now or (recs[-1]["ts"] + 86400)
    ms = [{"ts": r["ts"], "h": r["h"], "a": r["a"], "vh": [r["c"][0]] * 3, "va": [r["c"][1]] * 3, "w": 1.0} for r in recs]
    fit, _ = ratings.fit(ms, now, half_life, k_prior, iters=20)
    last = [r for r in recs if now - r["ts"] < 365 * 86400] or recs
    bh = sum(r["c"][0] for r in last) / len(last)
    ba = sum(r["c"][1] for r in last) / len(last)
    half = [r for r in last if r["c"][2] >= 0 and r["c"][3] >= 0]
    c1 = (sum(r["c"][2] + r["c"][3] for r in half) / max(1, sum(r["c"][0] + r["c"][1] for r in half))) if len(half) > 50 else 0.46
    cor = {}
    for t, v in fit.items():
        a, d = v["att"][0], v["def"][0]
        rt = (rat or {}).get(t)
        if rt:  # engine rating: [attSh, defSh, ...]
            a = a ** (1 - blend) * rt[0] ** blend
            d = d ** (1 - blend) * rt[1] ** blend
        cor[t] = [round(a, 4), round(d, 4)]
    return cor, [round(bh, 3), round(ba, 3), round(c1, 3)]


if __name__ == "__main__":
    backfill(int(sys.argv[1]))
