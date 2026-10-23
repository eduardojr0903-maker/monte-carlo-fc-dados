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


def backfill(lid):
    lg = collect.fm("leagues", id=lid)
    cur = (lg.get("details") or {}).get("selectedSeason") or ""
    shown = False
    for season in history.past_seasons(lg) + [cur]:
        p = path(lid, season)
        data = load(p)
        s_lg = lg if season == cur else collect.fm("leagues", id=lid, season=season.replace("/", "%2F"))
        ms = [m for m in (s_lg or {}).get("fixtures", {}).get("allMatches") or []
              if (m.get("status") or {}).get("finished") and not (m.get("status") or {}).get("cancelled")]
        new = miss = 0
        for m in ms:
            mid = str(m["id"])
            if mid in data:
                continue
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
                if not shown:
                    shown = True
                    try:
                        g = d["content"]["stats"]["Periods"]["All"]["stats"]
                        print("  chaves disponíveis:", [s.get("key") for x in g for s in x.get("stats") or []][:60], flush=True)
                    except Exception:  # noqa: BLE001
                        print("  sem bloco de estatísticas", flush=True)
            data[mid] = {"t": m["status"].get("utcTime"), "h": str(m["home"]["id"]), "a": str(m["away"]["id"]),
                         "hg": hg, "ag": ag, "c": c or [-1, -1, -1, -1]}
            new += 1
            if new % 100 == 0:
                save(p, data)
            time.sleep(collect.DELAY)
        save(p, data)
        print(lid, season, "jogos", len(ms), "novos", new, "sem escanteios", miss, flush=True)


if __name__ == "__main__":
    backfill(int(sys.argv[1]))
