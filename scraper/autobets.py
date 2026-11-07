"""Automatic bet log: compares the model (data/model/*.json, from model_run.js) with Bet365 odds and registers
every market with expected value >= MIN_EV as an automatic bet (1 unit), once per market.

Sources of Bet365 odds (upcoming_odds.py):
  - football-data fixtures.csv: 1X2 and over/under 2.5 goals (European leagues)
  - OddsPapi: every market with a mapping in data/odds/oddspapi/mapping.json (corners, players, shots...)
For bets already registered and not started, the latest price seen is stored as the closing line (CLV).

Output: data/autobets/bets.json = {doc id: bet document in the app's format}
Usage: python scraper/autobets.py
"""
import csv
import difflib
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_build import norm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "data", "odds", "upcoming")
OUT = os.path.join(ROOT, "data", "autobets", "bets.json")
MIN_EV = float(os.environ.get("MIN_EV", "0.08"))  # 07/11: user rule, EV of at least 8%
MIN_P = float(os.environ.get("MIN_P", "0.08"))      # skip long shots: model error dominates there
MIN_ODD = float(os.environ.get("MIN_ODD", "1.6"))  # 07/11: user rule, odds from 1.60 to 2.50 (easier to place)
MAX_ODD = float(os.environ.get("MAX_ODD", "2.5"))  # weekend 24-25/10: odds above 2.5 lost most
# backtest at Bet365: EV above ~25-40% is almost always a model blind spot (promoted teams, missing news), not value
MAX_EV = float(os.environ.get("MAX_EV", "0.25"))
BOOK = "Bet365"
FD_DIV = {"E0": 47, "E1": 48, "SP1": 87, "I1": 55, "D1": 54, "F1": 53, "N1": 57, "P1": 61, "B1": 40, "T1": 71, "SC0": 64, "SP2": 140, "I2": 86, "D2": 146, "F2": 110, "G1": 135}
DOC2LID = {"s325": 268, "s390": 8814, "s17": 47, "s18": 48, "s8": 87, "s23": 55, "s35": 54, "s34": 53, "s37": 57, "s238": 61, "s38": 40, "s52": 71, "s36": 64, "s54": 140, "s53": 86, "s44": 146, "s45": 38, "s215": 69, "s39": 46, "s185": 135, "s202": 196, "s20": 59, "s40": 67, "s155": 112, "s27464": 230, "s242": 130, "s196": 223, "s182": 110}
# market groups the user allowed: corners, players, 1X2 & goals, team shots (correct score left out)
# player markets ("pl") paused after the first weekend: the model added nothing to the bookmaker price there
ALLOWED = {"res", "dc", "hcp", "gols", "btts", "tg", "st", "sh", "sa", "tt", "th", "ta", "ct", "cx", "chc", "ch", "ca", "c1"}
# EV is computed on a blend of the model and the bookmaker's probability without margin (weekend fit: 0.6 model)
W_MODEL = float(os.environ.get("W_MODEL", "0.6"))
MARGIN_FALLBACK = 1.05  # when the other side of the market is missing


def sim(a, b):
    return difflib.SequenceMatcher(None, " ".join(norm(a)), " ".join(norm(b))).ratio()


def fd_odds():
    """[{lid, day, hn, an, odds: {key: price}, src}] from football-data fixtures."""
    out = []
    p = os.path.join(UP, "fd_fixtures.csv")
    if not os.path.exists(p):
        return out
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        lid = FD_DIV.get(r.get("Div", ""))
        if not lid:
            continue
        try:
            day = datetime.strptime(r["Date"], "%d/%m/%Y").date()
        except (KeyError, ValueError):
            continue
        o = {}
        for k, col in (("1", "B365H"), ("X", "B365D"), ("2", "B365A"), ("G>2.5", "B365>2.5"), ("G<2.5", "B365<2.5")):
            try:
                v = float(r.get(col) or 0)
            except ValueError:
                v = 0
            if v > 1:
                o[k] = v
        if o:
            out.append({"lid": lid, "day": day, "hn": r["HomeTeam"], "an": r["AwayTeam"], "odds": o, "src": "football-data"})
    return out


# football-data "new leagues" fixtures (one file, Country + League columns, 1X2 only)
FD_NEW = {("Austria", "Bundesliga"): 38, ("Switzerland", "Super League"): 69, ("Denmark", "Superliga"): 46, ("Poland", "Ekstraklasa"): 196, ("Norway", "Eliteserien"): 59, ("Sweden", "Allsvenskan"): 67, ("Argentina", "Liga Profesional"): 112, ("Mexico", "Liga MX"): 230, ("USA", "MLS"): 130, ("Japan", "J1 League"): 223, ("Brazil", "Serie A"): 268}


def fd_new_odds():
    out = []
    p = os.path.join(UP, "fd_new_fixtures.csv")
    if not os.path.exists(p):
        return out
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        lid = FD_NEW.get(((r.get("Country") or "").strip(), (r.get("League") or "").strip()))
        if not lid:
            continue
        try:
            day = datetime.strptime(r["Date"], "%d/%m/%Y").date()
        except (KeyError, ValueError):
            continue
        o = {}
        for k, col in (("1", "B365H"), ("X", "B365D"), ("2", "B365A")):
            try:
                v = float(r.get(col) or 0)
            except ValueError:
                v = 0
            if v > 1:
                o[k] = v
        if o:
            out.append({"lid": lid, "day": day, "hn": r["Home"], "an": r["Away"], "odds": o, "src": "football-data"})
    return out


def op_odds():
    """Same format from OddsPapi, for markets listed in data/odds/oddspapi/mapping.json (built once the reference
    lists are downloaded). Player markets map by player name."""
    p = os.path.join(UP, "oddspapi_bet365.json")
    mp = os.path.join(ROOT, "data", "odds", "oddspapi", "mapping.json")
    if not (os.path.exists(p) and os.path.exists(mp)):
        return []
    try:
        import oddspapi_map
    except ImportError:
        return []
    return oddspapi_map.convert(json.load(open(p)), json.load(open(mp)))


def find(src, lid, ts, hn, an):
    """Same fixture in a bookmaker list. With an exact kick-off time (OddsPapi) the league and time already pin the
    match down, so a looser name match is enough (e.g. 'Atlético-MG' vs 'Atletico Mineiro MG'); without it (football-data)
    the names must match closely."""
    day = datetime.fromtimestamp(ts, timezone.utc).date()
    best, score, timed = None, 0.0, False
    for r in src:
        if r["lid"] != lid or abs((r["day"] - day).days) > 1:
            continue
        if r.get("ts"):
            if abs(r["ts"] - ts) > 20 * 60:
                continue
        s = sim(hn, r["hn"]) + sim(an, r["an"])
        if s > score:
            best, score, timed = r, s, bool(r.get("ts"))
    if best is None:
        return None
    return best if score >= (0.8 if timed else 1.2) else None


def match_score(r, lid, ts, hn, an):
    """Name score of one bookmaker fixture against one of our fixtures, or None when league/date/time rule it out."""
    if r["lid"] != lid:
        return None
    day = datetime.fromtimestamp(ts, timezone.utc).date()
    if abs((r["day"] - day).days) > 1:
        return None
    if r.get("ts") and abs(r["ts"] - ts) > 20 * 60:
        return None
    s = sim(hn, r["hn"]) + sim(an, r["an"])
    return s if s >= (0.8 if r.get("ts") else 1.2) else None


def consistent(hits, tol=0.08):
    """Safety check: when both sources have a fixture, their 1X2 and over/under 2.5 prices must tell the same story
    (implied probabilities within 8 points). A large gap means one of them is a different game."""
    fd = next((h for h in hits if h["src"] == "football-data"), None)
    op = next((h for h in hits if h["src"] == "oddspapi"), None)
    if not fd or not op:
        return True
    for k in ("1", "X", "2", "G>2.5", "G<2.5"):
        a, b = fd["odds"].get(k), op["odds"].get(k)
        if a and b and abs(1 / a - 1 / b) > tol:
            return False
    return True


def assign_sources(sources, fixtures):
    """One-to-one link between bookmaker fixtures and our fixtures: a bookmaker fixture goes only to the fixture it
    matches best, and only if that fixture has no better bookmaker fixture from the same source.
    Without this, when a game was still missing from OddsPapi, another game at the same kick-off with loosely similar
    names (Paderborn x Frankfurt ~ RB Leipzig x FC Augsburg) lent it its prices.
    fixtures: [(key, lid, ts, hn, an)]. Returns {key: [source rows]}."""
    best_fx = {}
    for i, r in enumerate(sources):
        sc = [(match_score(r, lid, ts, hn, an), key) for key, lid, ts, hn, an in fixtures]
        sc = [x for x in sc if x[0] is not None]
        if sc:
            best_fx[i] = max(sc)
    out = {}
    for i, (s, key) in best_fx.items():
        cur = out.setdefault(key, {})
        src = sources[i]["src"]
        if src not in cur or s > cur[src][0]:
            cur[src] = (s, sources[i])
    return {k: [r for _, r in v.values()] for k, v in out.items()}


def player_key_match(key, label, fx_odds):
    """Player market keys carry the FotMob player id; bookmaker player markets carry the name. Match by name."""
    m = re.match(r"^P(\d)_(.+)_(sh|sot|g)(\d)$", key)
    if not m:
        return None
    name = label.split(" · ")[0]
    kind = m.group(3) + m.group(4)
    best, score = None, 0.0
    for (pname, pk), price in (fx_odds.get("players") or {}).items():
        if pk != kind:
            continue
        s = sim(name, pname)
        if s > score:
            best, score = price, s
    return best if score >= 0.82 else None


def bet_id(fx_id, key):
    """Document id of an automatic bet. '>' and '<' must stay distinct (over vs under of the same line)."""
    k = key.replace(">", "o").replace("<", "u")
    return f"auto_{fx_id}_{re.sub(r'[^A-Za-z0-9_.+-]', '_', k)}"


CONFIG = os.path.join(ROOT, "data", "autobets", "config.json")
_CFG = json.load(open(CONFIG)) if os.path.exists(CONFIG) else {}
EXCLUDED = set(_CFG.get("exclude_leagues", []))
EXCL_FAM = {k: set(v) for k, v in _CFG.get("exclude_families", {}).items()}  # league -> market families switched off


def market_prob(key, odds):
    """Bookmaker probability without margin, using the other outcomes of the same market when available."""
    groups = [("1", "X", "2"), ("CX1", "CXX", "CX2"), ("BTTS-S", "BTTS-N"), ("DNB1", "DNB2"),
              ("H-1.5", "A+1.5"), ("A-1.5", "H+1.5"), ("1X", "2"), ("X2", "1"), ("12", "X")]
    m = re.match(r"^(.+)([<>])([\d.]+)$", key)
    if m:
        groups = [(key, m.group(1) + ("<" if m.group(2) == ">" else ">") + m.group(3))]
    for g in groups:
        if key in g and all(odds.get(k) for k in g):
            inv = [1 / odds[k] for k in g]
            return (1 / odds[key]) / sum(inv)
    return 1 / (odds[key] * MARGIN_FALLBACK)


MAX_CORR = float(os.environ.get("MAX_CORR", "0.30"))  # bets on the same fixture must not move together more than this


def load_sims(doc_id):
    p = os.path.join(ROOT, "data", "model", f"sims_{doc_id}.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def sim_outcomes(key, S):
    """Win (1) / lose (0) / void (None) of a market key in each simulated match (same rules as settle.py)."""
    if not S or not S.get("gH"):
        return None
    gH, gA = S["gH"], S["gA"]
    n = len(gH)
    m = re.match(r"^(G|HG|AG|C|CHt|CAt|C1|S|SH|SA|T|TH|TA)([<>])([\d.]+)$", key)
    if m:
        k, op, line = m.group(1), m.group(2), float(m.group(3))
        arrs = {"G": [gH[i] + gA[i] for i in range(n)], "HG": gH, "AG": gA}
        if S.get("cH"):
            arrs.update({"C": [S["cH"][i] + S["cA"][i] for i in range(n)], "CHt": S["cH"], "CAt": S["cA"],
                         "C1": [S["cH1"][i] + S["cA1"][i] for i in range(n)]})
        if S.get("sH"):
            arrs.update({"S": [S["sH"][i] + S["sA"][i] for i in range(n)], "SH": S["sH"], "SA": S["sA"],
                         "T": [S["tH"][i] + S["tA"][i] for i in range(n)], "TH": S["tH"], "TA": S["tA"]})
        v = arrs.get(k)
        if v is None:
            return None
        return [1 if (x > line if op == ">" else x < line) else 0 for x in v]
    d = [gH[i] - gA[i] for i in range(n)]
    simple = {"1": lambda i: d[i] > 0, "X": lambda i: d[i] == 0, "2": lambda i: d[i] < 0, "1X": lambda i: d[i] >= 0,
              "X2": lambda i: d[i] <= 0, "12": lambda i: d[i] != 0, "BTTS-S": lambda i: gH[i] > 0 and gA[i] > 0,
              "BTTS-N": lambda i: not (gH[i] > 0 and gA[i] > 0), "H-1.5": lambda i: d[i] >= 2, "A+1.5": lambda i: d[i] <= 1,
              "A-1.5": lambda i: d[i] <= -2, "H+1.5": lambda i: d[i] >= -1}
    if key in simple:
        return [1 if simple[key](i) else 0 for i in range(n)]
    if key in ("DNB1", "DNB2"):
        return [None if d[i] == 0 else (1 if (d[i] > 0) == (key == "DNB1") else 0) for i in range(n)]
    if key in ("CX1", "CXX", "CX2") and S.get("cH"):
        c = [S["cH"][i] - S["cA"][i] for i in range(n)]
        f = {"CX1": lambda x: x > 0, "CXX": lambda x: x == 0, "CX2": lambda x: x < 0}[key]
        return [1 if f(x) else 0 for x in c]
    m = re.match(r"^C([HA])(-?[\d.]+)$", key)
    if m and S.get("cH"):
        line = float(m.group(2))
        return [1 if ((S["cH"][i] - S["cA"][i]) if m.group(1) == "H" else (S["cA"][i] - S["cH"][i])) + line > 0 else 0 for i in range(n)]
    return None


def corr(a, b):
    """Correlation (phi) of two win/lose vectors, ignoring simulations where either is void."""
    if a is None or b is None:
        return 0.0
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 50:
        return 0.0
    sx = sum(x for x, _ in pairs); sy = sum(y for _, y in pairs); sxy = sum(x * y for x, y in pairs)
    vx = sx * (n - sx); vy = sy * (n - sy)
    if vx <= 0 or vy <= 0:
        return 0.0
    return (n * sxy - sx * sy) / (vx * vy) ** 0.5


def family(key):
    """Bets on the same thing count once per fixture: one line per total, one per player, one result bet..."""
    m = re.match(r"^P(\d)_(.+)_(sh|sot|g)(\d)$", key)
    if m:
        return "P" + m.group(1) + "_" + m.group(2)
    m = re.match(r"^(G|HG|AG|C|CHt|CAt|C1|S|SH|SA|T|TH|TA)([<>])", key)
    if m:
        return m.group(1) + m.group(2)
    if key in ("1", "X", "2", "1X", "X2", "12", "DNB1", "DNB2", "H-1.5", "A+1.5", "A-1.5", "H+1.5"):
        return "res"
    if key.startswith("BTTS"):
        return "btts"
    if key.startswith("CX"):
        return "cx"
    m = re.match(r"^C([HA])(-?[\d.]+)$", key)
    if m:
        return "chc"
    return key


def main():
    now = time.time()
    bets = json.load(open(OUT)) if os.path.exists(OUT) else {}
    sources = fd_odds() + fd_new_odds() + op_odds()
    print("jogos com odds da casa:", len(sources), flush=True)
    new = upd = dropped = 0
    diag = {}
    allfx = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "model", "s*.json"))):
        lid0 = DOC2LID.get(os.path.basename(f)[:-5])
        for fx in json.load(open(f)).get("fixtures", []):
            allfx.append((fx["fx"], lid0, fx["ts"], fx["hn"], fx["an"]))
    linked = assign_sources(sources, allfx)
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "model", "s*.json"))):
        doc_id = os.path.basename(f)[:-5]
        lid = DOC2LID.get(doc_id)
        M = json.load(open(f))
        if M.get("league") in EXCLUDED:
            continue  # league switched off in data/autobets/config.json
        sims = load_sims(doc_id)
        for fx in M.get("fixtures", []):
            if fx["ts"] < now + 600:
                continue
            # merge odds of every source linked to this fixture (one bookmaker fixture per source)
            odds, players, srcs = {}, {}, set()
            hits = linked.get(fx["fx"], [])
            if not consistent(hits):
                print("OddsPapi diverge da football-data, jogo ignorado:", fx["hn"], "x", fx["an"], flush=True)
                hits = [h for h in hits if h["src"] != "oddspapi"]
            for hit in hits:
                if hit:
                    for k, v in hit["odds"].items():
                        odds.setdefault(k, v)
                    players.update(hit.get("players") or {})
                    srcs.add(hit["src"])
            dg = {"league": M.get("league"), "game": f"{fx['hn']} x {fx['an']}", "ts": fx["ts"], "sources": sorted(srcs),
                  "markets_with_odds": len(odds), "best": None,
                  "matched": [f"{h['src']}: {h['hn']} x {h['an']}" for h in linked.get(fx["fx"], [])]}
            diag[fx["fx"]] = dg
            if not odds and not players:
                continue
            fo = {"players": players}
            taken = {family(b["key"]) for b in bets.values() if b.get("fx") == fx["fx"]}
            cands = []
            for key, label, p, grp in fx["markets"]:
                o = odds.get(key) or (player_key_match(key, label, fo) if grp == "pl" else None)
                if not o:
                    continue
                bid = bet_id(fx["fx"], key)
                if bid in bets:
                    b = bets[bid]
                    if b.get("key") == key and b.get("status") == "pending" and fx["ts"] > now:
                        b["lastOdd"] = o; b["lastSeen"] = int(now); b["lastP"] = p
                        upd += 1
                    continue
                p_model = p
                q = market_prob(key, dict(odds, **{key: o}))
                p = W_MODEL * p_model + (1 - W_MODEL) * q
                ev = p * o - 1
                if grp in ALLOWED and MIN_ODD <= o <= MAX_ODD and (dg["best"] is None or ev > dg["best"][3]):
                    dg["best"] = [label, o, round(p, 4), round(ev, 4)]
                if grp not in ALLOWED or ev < MIN_EV or ev > MAX_EV or p < MIN_P or o > MAX_ODD or o < MIN_ODD:
                    continue
                fam = family(key)
                if fam in taken or fam in EXCL_FAM.get(M.get("league"), ()):
                    continue
                cands.append((ev, bid, fam, {
                    "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "comp": int(doc_id[1:]),
                    "league": M.get("league"), "h": fx["h"], "a": fx["a"], "hn": fx["hn"], "an": fx["an"],
                    "fx": fx["fx"], "ts": fx["ts"], "key": key, "label": label, "p": round(p, 4), "pModel": p_model, "pMarket": round(q, 4), "odd": o, "ev": round(ev, 4),
                    "stake": 1, "book": BOOK, "auto": True, "src": "+".join(sorted(srcs)), "lineup": fx.get("src"),
                    "status": "pending", "profit": None, "close": None, "closeFairP": None, "clv": None, "closeSrc": None,
                    "lastOdd": o, "lastSeen": int(now), "lastP": p,
                }))
            # highest EV first; skip a bet that moves together with one already chosen (or already registered) for this
            # fixture, e.g. "Cruzeiro under 1.5 goals" and "Bahia or draw"
            S = sims.get(fx["fx"])
            chosen = [sim_outcomes(b["key"], S) for b in bets.values() if b.get("fx") == fx["fx"]]
            fams = set()
            for ev, bid, fam, doc in sorted(cands, key=lambda c: -c[0]):
                if fam in fams:
                    continue
                vec = sim_outcomes(doc["key"], S)
                rho = max([abs(corr(vec, c)) for c in chosen] or [0.0])
                if rho > MAX_CORR:
                    dropped += 1
                    continue
                chosen.append(vec); fams.add(fam)
                bets[bid] = doc
                new += 1
                print(f"  nova: {M.get('league')} {fx['hn']} x {fx['an']} · {doc['label']} @ {doc['odd']} (p {doc['p']:.3f}, VE {ev * 100:+.1f}%)", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(bets, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fixtures": diag},
              open(os.path.join(os.path.dirname(OUT), "diag.json"), "w"), ensure_ascii=False, indent=0)
    print(f"apostas automáticas: {new} novas, {dropped} descartadas por correlação, {upd} com odd atualizada, {len(bets)} no total", flush=True)


if __name__ == "__main__":
    main()
