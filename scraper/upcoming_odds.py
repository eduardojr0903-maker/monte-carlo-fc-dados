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
OP_TOURNAMENTS = {268: 325, 8814: 390, 47: 17, 48: 18, 87: 8, 55: 23, 54: 35, 53: 34, 57: 37, 61: 238}
# Bet365 international: same prices as the Brazilian site in practice, and far more markets on OddsPapi
# (corners and player shots are missing from bet365.bet.br there)
BOOKS = tuple(os.environ.get("ODDSPAPI_BOOKS", "bet365").split(","))
PROBE = bool(os.environ.get("ODDSPAPI_PROBE"))  # probe run: only save the summary sample for each bookmaker
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


def _summary(fx, book, out):
    """Small sample (2 fixtures) plus market types per bookmaker, kept in the repository."""
    # small sample kept in the repository (the full response is used in the run and not committed)
    sample = []
    for f in fx[:400]:
        bo = f.get("bookmakerOdds") or {}
        if any((v.get("markets") or {}) for v in bo.values()):
            sample.append(f)
        if len(sample) >= 2:
            break
    stats = {}
    mk = json.load(open(os.path.join(REF, "markets.json"))) if os.path.exists(os.path.join(REF, "markets.json")) else []
    mk = {str(x.get("marketId")): x.get("marketType") or "" for x in (mk if isinstance(mk, list) else mk.get("data", []))}
    types = {}
    for f in fx:
        for b, v in (f.get("bookmakerOdds") or {}).items():
            st = stats.setdefault(b, {"jogos": 0, "mercados": 0})
            st["jogos"] += 1; st["mercados"] += len(v.get("markets") or {})
            seen = set(mk.get(str(m), "?" + str(m)) for m in (v.get("markets") or {}))
            for t in seen:
                types[t] = types.get(t, 0) + 1
    stats["tipos_de_mercado (jogos)"] = dict(sorted(types.items(), key=lambda x: -x[1]))
    json.dump({"fetched": int(time.time()), "book": book, "fixtures": len(fx), "by_bookmaker": stats, "sample": sample},
              open(out, "w"))


def oddspapi():
    key = os.environ.get("ODDSPAPI_KEY", "").strip()
    if not key:
        print("OddsPapi: sem chave (secret ODDSPAPI_KEY), pulando", flush=True)
        return
    # free plan: 250 requests/month, 1 bookmaker and at most 5 tournaments per request -> 2 requests per round.
    # Three rounds a day (09, 15 and 21 UTC = 06, 12 and 18 in Brasília): about 186 requests a month.
    if time.gmtime().tm_hour not in (9, 15, 21) and not os.environ.get("ODDSPAPI_FORCE") and os.path.exists(os.path.join(REF, "markets.json")):
        print("OddsPapi: fora dos horários (09, 15, 21 UTC), pulando para economizar a cota", flush=True)
        return
    os.makedirs(REF, exist_ok=True)
    # reference lists, downloaded once
    for name, path, params in (("tournaments.json", "tournaments", {"sportId": 10}), ("markets.json", "markets", {"sportId": 10}),
                               ("bookmakers.json", "bookmakers", {}),
                               ("participants.json", "participants", {"sportId": 10})):
        p = os.path.join(REF, name)
        if not os.path.exists(p):
            d = op_get(path, key, **params)
            if d is not None:
                json.dump(d, open(p, "w"))
                print("OddsPapi: salvo", name, flush=True)
            time.sleep(1.2)
    ids = dict(OP_TOURNAMENTS)
    json.dump({str(k): v for k, v in ids.items()}, open(os.path.join(REF, "tournament_ids.json"), "w"))
    tids = [str(v) for v in ids.values()]
    chunks = [tids[i:i + 5] for i in range(0, len(tids), 5)]
    d, book = None, None
    for b in BOOKS:
        allfx = []
        for ch in chunks:
            r = op_get("odds-by-tournaments", key, bookmaker=b, tournamentIds=",".join(ch))
            allfx += (r if isinstance(r, list) else (r or {}).get("data", [])) or []
            time.sleep(1.2)
        if PROBE:
            d, book = allfx, b
            _summary(d, book, os.path.join(REF, f"probe_{b}.json"))
            continue
        if any((f.get("bookmakerOdds") or {}).get(b, {}).get("markets") for f in allfx):
            d, book = allfx, b
            break
        print("OddsPapi:", b, "sem mercados, tentando a próxima", flush=True)
    if d is not None and book and not PROBE:
        os.makedirs(UP, exist_ok=True)
        json.dump({"fetched": int(time.time()), "book": book, "tournaments": {str(k): v for k, v in ids.items()}, "data": d},
                  open(os.path.join(UP, "oddspapi_bet365.json"), "w"))
        fx = d if isinstance(d, list) else d.get("data", [])
        print("OddsPapi: jogos com odds", len(fx), flush=True)
        _summary(fx, book, os.path.join(REF, "sample.json"))


if __name__ == "__main__":
    fd()
    oddspapi()
