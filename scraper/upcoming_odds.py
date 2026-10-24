"""Bookmaker odds for upcoming matches (reference bookmaker: Bet365).

1. football-data.co.uk fixtures.csv (free, no key): Bet365 1X2 and over/under 2.5 for the European leagues.
2. OddsPapi (free key, 250 requests/month): every Bet365 market (corners, players, shots...) for all 8 leagues in
   ONE request (odds-by-tournaments). Runs only when the ODDSPAPI_KEY secret is set. The first run also saves the
   reference lists (tournaments, markets, bookmakers) to data/odds/oddspapi/ so the market mapping can be built.
   The key is never written to disk. The closing line is kept per registered bet by autobets.py (last price seen
   before kick-off), so no full snapshots are stored.
Output: data/odds/upcoming/fd_fixtures.csv, data/odds/upcoming/oddspapi_bet365.json
"""
import json
import os
import sys
import time

from curl_cffi import requests as http

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "data", "odds", "upcoming")
REF = os.path.join(ROOT, "data", "odds", "oddspapi")
S = http.Session(impersonate="chrome")
API = "https://api.oddspapi.io/v4"
# our league -> OddsPapi tournament id (checked against data/odds/oddspapi/tournaments.json)
OP_TOURNAMENTS = {268: 325, 8814: 390, 47: 17, 48: 18, 87: 8, 55: 23, 54: 35, 53: 34}
# up to 3 bookmakers per request: Bet365 Brazil (what the user sees), Bet365 international (fallback), Pinnacle (closing line)
BOOKS = ("bet365.bet.br", "bet365", "pinnacle")
# (old name matching, kept for reference) our league -> how OddsPapi names it
OP_LEAGUES = {
    268: ("brazil", ("brasileiro serie a", "serie a")), 8814: ("brazil", ("brasileiro serie b", "serie b")),
    47: ("england", ("premier league",)), 48: ("england", ("championship",)), 87: ("spain", ("laliga", "la liga")),
    55: ("italy", ("serie a",)), 54: ("germany", ("bundesliga",)), 53: ("france", ("ligue 1",)),
}


def fd():
    os.makedirs(UP, exist_ok=True)
    for name, url in (("fd_fixtures.csv", "https://www.football-data.co.uk/fixtures.csv"),
                      ("fd_new_fixtures.csv", "https://www.football-data.co.uk/new_league_fixtures.csv")):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 100:
                open(os.path.join(UP, name), "wb").write(r.content)
                head = r.content.decode("utf-8-sig", "ignore").splitlines()[0]
                print(name, "ok", len(r.content), "bytes; colunas:", head[:300], flush=True)
            else:
                print(name, "HTTP", r.status_code, flush=True)
        except Exception as e:  # noqa: BLE001
            print(name, "falhou", e, flush=True)


def op_get(path, key, **params):
    params["apiKey"] = key
    for i in range(3):
        r = S.get(f"{API}/{path}", params=params, timeout=90)
        if r.status_code == 200:
            return r.json()
        msg = f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime())} {path} HTTP {r.status_code} {r.text[:500].replace(key, '***')}"
        print(msg, flush=True)
        os.makedirs(UP, exist_ok=True)
        open(os.path.join(UP, "oddspapi_error.txt"), "a").write(msg + "\n")
        if r.status_code == 429:
            time.sleep(5 + 5 * i)
            continue
        return None
    return None


def oddspapi():
    key = os.environ.get("ODDSPAPI_KEY", "").strip()
    if not key:
        print("OddsPapi: sem chave (secret ODDSPAPI_KEY), pulando", flush=True)
        return
    # free plan: 250 requests/month. Skip the 03 UTC run (night in Brazil and Europe): 7 calls a day.
    if time.gmtime().tm_hour in (2, 3, 4) and os.path.exists(os.path.join(REF, "tournament_ids.json")):
        print("OddsPapi: madrugada, pulando para economizar a cota", flush=True)
        return
    os.makedirs(REF, exist_ok=True)
    # reference lists, downloaded once
    for name, path, params in (("tournaments.json", "tournaments", {"sportId": 10}), ("markets.json", "markets", {"sportId": 10}),
                               ("bookmakers.json", "bookmakers", {})):
        p = os.path.join(REF, name)
        if not os.path.exists(p):
            d = op_get(path, key, **params)
            if d is not None:
                json.dump(d, open(p, "w"))
                print("OddsPapi: salvo", name, flush=True)
            time.sleep(1.2)
    ids = dict(OP_TOURNAMENTS)
    json.dump({str(k): v for k, v in ids.items()}, open(os.path.join(REF, "tournament_ids.json"), "w"))
    d = op_get("odds-by-tournaments", key, bookmakers=",".join(BOOKS), tournamentIds=",".join(str(v) for v in ids.values()))
    if d is not None:
        os.makedirs(UP, exist_ok=True)
        json.dump({"fetched": int(time.time()), "tournaments": {str(k): v for k, v in ids.items()}, "data": d},
                  open(os.path.join(UP, "oddspapi_bet365.json"), "w"))
        fx = d if isinstance(d, list) else d.get("data", [])
        print("OddsPapi: jogos com odds", len(fx), flush=True)
        # small sample kept in the repository (the full response is used in the run and not committed)
        sample = []
        for f in fx[:400]:
            bo = f.get("bookmakerOdds") or {}
            if any((v.get("markets") or {}) for v in bo.values()):
                sample.append(f)
            if len(sample) >= 2:
                break
        stats = {}
        for f in fx:
            for b, v in (f.get("bookmakerOdds") or {}).items():
                st = stats.setdefault(b, {"jogos": 0, "mercados": 0})
                st["jogos"] += 1; st["mercados"] += len(v.get("markets") or {})
        json.dump({"fetched": int(time.time()), "fixtures": len(fx), "by_bookmaker": stats, "sample": sample},
                  open(os.path.join(REF, "sample.json"), "w"))


if __name__ == "__main__":
    fd()
    oddspapi()
