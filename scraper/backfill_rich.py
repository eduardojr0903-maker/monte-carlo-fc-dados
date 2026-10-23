"""Download detailed records (rich.py) for every finished match since 2022, one league per call.
Run by .github/workflows/backfill.yml in parallel jobs."""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import history  # noqa: E402
import rich  # noqa: E402

RICH = os.path.join(collect.ROOT, "data", "cache", "rich")
lid = int(sys.argv[1])
lg = collect.fm("leagues", id=lid)
cur = (lg.get("details") or {}).get("selectedSeason") or ""
seasons = history.past_seasons(lg) + [cur]
total = 0
for season in seasons:
    p = rich.path(RICH, lid, season)
    data = rich.load(p)
    s_lg = lg if season == cur else collect.fm("leagues", id=lid, season=season.replace("/", "%2F"))
    ms = [m for m in (s_lg or {}).get("fixtures", {}).get("allMatches") or []
          if (m.get("status") or {}).get("finished") and not (m.get("status") or {}).get("cancelled")]
    new = 0
    for m in ms:
        mid = str(m["id"])
        if mid in data:
            continue
        try:
            hg, ag = [int(x.strip()) for x in (m["status"].get("scoreStr") or "").split("-")]
        except ValueError:
            continue
        meta = {"t": m["status"].get("utcTime"), "h": str(m["home"]["id"]), "hn": m["home"].get("shortName") or m["home"]["name"],
                "a": str(m["away"]["id"]), "an": m["away"].get("shortName") or m["away"]["name"], "hg": hg, "ag": ag}
        try:
            d = collect.fm("matchDetails", matchId=mid)
            data[mid] = rich.record(meta, d)
        except Exception as e:  # noqa: BLE001
            print("  falhou", mid, e, flush=True)
            continue
        new += 1
        if new % 50 == 0:
            rich.save(p, data)
        time.sleep(0.5 + random.random() * 0.3)
    rich.save(p, data)
    total += new
    print(lid, season, "jogos", len(data), "novos", new, flush=True)
print("total novos", total)
