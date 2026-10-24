"""Results document for the app: everything needed to settle bets in the page itself.

data/db/results.json (synced like the league documents, doc id "results") =
  {"updated", "pack": JSON string {"m": {match id: [ts, home id, away id, hg, ag, corners [h, a, h1, a1] or null,
                                                  team shots [[sh, sot] home, [sh, sot] away] or null,
                                                  players [[pid, side, shots, sot, goals], ...]]},
                                   "close": {auto bet id: Bet365 price last seen before kick-off}}}
Matches finished in the last DAYS days, all leagues.
"""
import glob
import json
import os
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = int(os.environ.get("RESULTS_DAYS", "10"))


def main():
    now = time.time()
    corners = {}
    for f in glob.glob(os.path.join(ROOT, "data", "cache", "corners", "*.json")):
        try:
            for mid, c in json.load(open(f)).items():
                if c.get("c") and c["c"][0] >= 0:
                    corners[mid] = c["c"]
        except Exception:  # noqa: BLE001
            pass
    m = {}
    for f in glob.glob(os.path.join(ROOT, "data", "cache", "fotmob", "*.json")):
        try:
            cache = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        for mid, r in cache.items():
            ts = r.get("ts") or 0
            if ts < now - DAYS * 86400:
                continue
            t = r.get("t")
            shots = [[int(t[0][0]), int(t[0][1])], [int(t[1][0]), int(t[1][1])]] if t else None
            pl = [[str(p[0]), p[2], int(p[6]), int(p[7]), int(p[9])] for p in r.get("p") or []]
            m[mid] = [ts, r["h"], r["a"], r["hg"], r["ag"], corners.get(mid), shots, pl]
    close = {}
    bp = os.path.join(ROOT, "data", "autobets", "bets.json")
    if os.path.exists(bp):
        for bid, b in json.load(open(bp)).items():
            try:
                created = datetime.fromisoformat(str(b.get("created")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if b.get("lastOdd") and (b.get("lastSeen") or 0) > created + 1800 and b.get("ts", 0) <= now:
                close[bid] = b["lastOdd"]
    doc = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "pack": json.dumps({"m": m, "close": close}, separators=(",", ":"), ensure_ascii=False)}
    out = os.path.join(ROOT, "data", "db", "results.json")
    json.dump(doc, open(out, "w"), ensure_ascii=False, separators=(",", ":"))
    print("resultados:", len(m), "jogos,", len(close), "fechamentos,", os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
