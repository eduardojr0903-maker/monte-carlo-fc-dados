"""SportsGameOdds probe: which soccer leagues exist, and which markets Bet365 prices there (looking for team shots).
Needs the SGO_KEY secret. Writes a summary (never the key) to data/odds/sgo/probe.json.
"""
import json
import os
import time
from collections import Counter, defaultdict

from curl_cffi import requests as http

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "odds", "sgo")
API = "https://api.sportsgameodds.com/v2"
S = http.Session(impersonate="chrome")


def get(path, key, **params):
    r = S.get(f"{API}/{path}", params=params, headers={"x-api-key": key}, timeout=60)
    txt = r.text[:300].replace(key, "***")
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} {txt}"
    return r.json(), None


def main():
    key = os.environ.get("SGO_KEY", "").strip()
    os.makedirs(OUT, exist_ok=True)
    out = {"fetched": int(time.time()), "errors": []}
    if not key:
        out["errors"].append("sem chave SGO_KEY")
        json.dump(out, open(os.path.join(OUT, "probe.json"), "w"), indent=1)
        return
    lg, err = get("leagues/", key, sportID="SOCCER")
    if err:
        out["errors"].append("leagues: " + err)
    leagues = (lg or {}).get("data") or []
    out["leagues"] = [[l.get("leagueID"), l.get("name") or l.get("shortName")] for l in leagues]
    wanted = [l.get("leagueID") for l in leagues if any(k in (l.get("leagueID") or "").upper() for k in
              ("EPL", "LA_LIGA", "BUNDESLIGA", "SERIE_A", "LIGUE_1", "BR", "BRA", "CHAMPIONSHIP", "EREDIVISIE", "PRIMEIRA"))]
    wanted = list(dict.fromkeys([l.get("leagueID") for l in leagues] + ["EPL", "BR_SERIE_A"]))
    out["wanted"] = wanted
    markets = defaultdict(Counter)   # league -> statID-entity-period-betType counts (Bet365 available)
    examples = {}
    for lid in wanted[:8]:
        ev, err = get("events/", key, leagueID=lid, oddsAvailable="true", limit=10)
        time.sleep(1.5)
        if err:
            out["errors"].append(f"events {lid}: {err}")
            continue
        for e in (ev or {}).get("data") or []:
            for oid, o in (e.get("odds") or {}).items():
                bb = (o.get("byBookmaker") or {})
                b = bb.get("bet365")
                if not b or not b.get("available", True):
                    continue
                parts = oid.split("-")
                ent = parts[1] if len(parts) > 1 else ""
                ent = ent if ent in ("home", "away", "all") else "player"
                k = f"{parts[0]}|{ent}|{parts[2] if len(parts) > 2 else ''}|{parts[3] if len(parts) > 3 else ''}"
                markets[lid][k] += 1
                if "shot" in parts[0].lower() and ent != "player" and k not in examples:
                    examples[k] = {"oddID": oid, "bet365": {kk: b.get(kk) for kk in ("odds", "overUnder", "spread", "available")},
                                   "event": f"{(e.get('teams') or {}).get('home', {}).get('names', {}).get('long')} x "
                                            f"{(e.get('teams') or {}).get('away', {}).get('names', {}).get('long')}"}
    out["markets_bet365"] = {lid: dict(c.most_common()) for lid, c in markets.items()}
    out["team_shot_examples"] = examples
    json.dump(out, open(os.path.join(OUT, "probe.json"), "w"), indent=1, ensure_ascii=False)
    print("ligas", len(leagues), "consultadas", wanted[:8], "exemplos de chutes de time", len(examples), "erros", out["errors"])


if __name__ == "__main__":
    main()
