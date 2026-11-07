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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import LEAGUES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "data", "odds", "upcoming")
REF = os.path.join(ROOT, "data", "odds", "oddspapi")
S = http.Session(impersonate="chrome")
API = "https://api.oddspapi.io/v4"
# our league -> OddsPapi tournament id (checked against data/odds/oddspapi/tournaments.json)
PLAN_LIMIT = int(os.environ.get("ODDSPAPI_PLAN_LIMIT", "250"))   # free plan
WARN_AT = int(os.environ.get("ODDSPAPI_WARN_AT", "200"))
# 07/11: one round a day (at most 5 leagues per request: 26 leagues = 6 requests -> ~180 a month, fits the free plan)
DAILY_HOUR = int(os.environ.get("ODDSPAPI_DAILY_HOUR", "9"))
OP_TOURNAMENTS = {268: 325, 8814: 390, 47: 17, 48: 18, 87: 8, 55: 23, 54: 35, 53: 34, 57: 37, 61: 238, 40: 38, 71: 52, 64: 36, 140: 54, 86: 53, 146: 44, 110: 182, 38: 45, 69: 215, 46: 39, 135: 185, 196: 202, 59: 20, 67: 40, 112: 155, 230: 27464, 130: 242, 223: 196}
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
    types, active = {}, {}
    for f in fx:
        for b, v in (f.get("bookmakerOdds") or {}).items():
            st = stats.setdefault(b, {"jogos": 0, "mercados": 0})
            st["jogos"] += 1; st["mercados"] += len(v.get("markets") or {})
            seen = set(mk.get(str(m), "?" + str(m)) for m in (v.get("markets") or {}))
            for t in seen:
                types[t] = types.get(t, 0) + 1
            # markets with at least one price open for betting (suspended prices are ignored by the robot)
            live = set(mk.get(str(m), "?" + str(m)) for m, mv in (v.get("markets") or {}).items()
                       if any(pl.get("active") for o in (mv.get("outcomes") or {}).values() for pl in (o.get("players") or {}).values()))
            for t in live:
                active[t] = active.get(t, 0) + 1
    stats["tipos_de_mercado (jogos)"] = dict(sorted(types.items(), key=lambda x: -x[1]))
    stats["abertos (jogos)"] = dict(sorted(active.items(), key=lambda x: -x[1]))
    json.dump({"fetched": int(time.time()), "book": book, "fixtures": len(fx), "by_bookmaker": stats, "sample": sample},
              open(out, "w"))


def oddspapi():
    key = os.environ.get("ODDSPAPI_KEY", "").strip()
    if not key:
        print("OddsPapi: sem chave (secret ODDSPAPI_KEY), pulando", flush=True)
        return
    # free plan: 250 requests/month, 1 bookmaker and at most 5 tournaments per request -> 2 requests per round.
    # One round a day. GitHub delays scheduled runs by up
    # to an hour, so the rule is based on the time of the last round, not on fixed hours.
    last_p = os.path.join(REF, "last_round.json")
    state = json.load(open(last_p)) if os.path.exists(last_p) else {}
    last = state.get("ts", 0)
    # 07/11: one round a day, the first run from DAILY_HOUR (Brasília time) on; it covers the next 48 h of games
    brt = lambda t: time.gmtime(t - 3 * 3600)
    now_b, last_b = brt(time.time()), brt(last)
    done_today = last and (now_b.tm_year, now_b.tm_yday) == (last_b.tm_year, last_b.tm_yday) and last_b.tm_hour >= DAILY_HOUR
    if (done_today or now_b.tm_hour < DAILY_HOUR) and not os.environ.get("ODDSPAPI_FORCE"):
        print(f"OddsPapi: uma rodada por dia a partir das {DAILY_HOUR}h de Brasília (cota); última há",
              round((time.time() - last) / 3600, 1), "h", flush=True)
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
    # only leagues with a fixture in the robot's window (data/model, written by model_run.js just before)
    import glob as _glob
    comps = set()
    for f in _glob.glob(os.path.join(ROOT, "data", "model", "s*.json")):
        try:
            mdl = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        if any(fx.get("ts", 0) > time.time() for fx in mdl.get("fixtures", [])):
            comps.add(int(mdl.get("comp") or 0))
    comp2lid = {int(v[0][1:]): k for k, v in LEAGUES.items()}
    # leagues switched off in the robot are not queried (saves quota)
    try:
        excl = set(json.load(open(os.path.join(ROOT, "data", "autobets", "config.json"))).get("exclude_leagues") or [])
    except Exception:  # noqa: BLE001
        excl = set()
    comps = {c for c in comps if LEAGUES.get(comp2lid.get(c), (None, None))[1] not in excl}
    ids = {comp2lid[c]: OP_TOURNAMENTS[comp2lid[c]] for c in comps if comp2lid.get(c) in OP_TOURNAMENTS}
    json.dump({str(k): v for k, v in OP_TOURNAMENTS.items()}, open(os.path.join(REF, "tournament_ids.json"), "w"))
    if not ids:
        print("OddsPapi: nenhuma liga com jogo na janela, pulando", flush=True)
        return
    tids = [str(v) for v in ids.values()]
    chunks = [tids[i:i + 5] for i in range(0, len(tids), 5)]
    # monthly quota guard (free plan: 250 requests a month; keep a margin)
    month = time.strftime("%Y-%m", time.gmtime())
    used = state.get("used", 0) if state.get("month") == month else 0
    # no blocking: the user decides on the paid plan; the daily sync warns from WARN_AT on (see last_round.json)
    if used + len(chunks) > WARN_AT:
        print(f"OddsPapi: AVISO, {used + len(chunks)} de {PLAN_LIMIT} consultas do mês", flush=True)
    print(f"OddsPapi: {len(ids)} ligas com jogo, {len(chunks)} consultas (mês: {used} usadas)", flush=True)
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
    if not PROBE:
        n_req = len(chunks) * (BOOKS.index(book) + 1 if book in BOOKS else len(BOOKS))
        tot = used + n_req
        json.dump({"ts": int(time.time()), "month": month, "used": tot, "limit": PLAN_LIMIT,
                   "warn": tot >= WARN_AT}, open(last_p, "w"))
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
