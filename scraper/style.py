"""Team match statistics for the playing-style study (possession, passes, long balls, tackles, recoveries, crosses, ...).

Cache: data/cache/style/<league>_<season>.json = {match id: {"t", "h", "a", "hg", "ag", "s": {stat key: [home, away]}}}
Usage: python scraper/style.py LEAGUE_ID   (backfill since 2022, resumable)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import history  # noqa: E402

DIR = os.path.join(collect.ROOT, "data", "cache", "style")


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


def process_season(lid, season, s_lg):
    p = path(lid, season)
    data = load(p)
    ms = [m for m in (s_lg or {}).get("fixtures", {}).get("allMatches") or []
          if (m.get("status") or {}).get("finished") and not (m.get("status") or {}).get("cancelled")]
    new = empty = 0
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
        s = collect.team_stats((d or {}).get("content") or {})
        if not s:
            empty += 1
        elif new == 0:
            print("  chaves:", sorted(s), flush=True)
        data[mid] = {"t": m["status"].get("utcTime"), "h": str(m["home"]["id"]), "a": str(m["away"]["id"]),
                     "hg": hg, "ag": ag, "s": s}
        new += 1
        if new % 100 == 0:
            save(p, data)
        time.sleep(collect.DELAY)
    if new:
        save(p, data)
    print(lid, season, "jogos", len(ms), "novos", new, "sem estatísticas", empty, flush=True)


def backfill(lid):
    lg = collect.fm("leagues", id=lid)
    cur = (lg.get("details") or {}).get("selectedSeason") or ""
    for season in history.past_seasons(lg) + [cur]:
        s_lg = lg if season == cur else collect.fm("leagues", id=lid, season=season.replace("/", "%2F"))
        process_season(lid, season, s_lg)


if __name__ == "__main__":
    backfill(int(sys.argv[1]))
