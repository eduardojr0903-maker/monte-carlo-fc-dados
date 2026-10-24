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
MIN_EV = float(os.environ.get("MIN_EV", "0.05"))
MIN_P = float(os.environ.get("MIN_P", "0.08"))      # skip long shots: model error dominates there
MAX_ODD = float(os.environ.get("MAX_ODD", "12"))
# backtest at Bet365: EV above ~25-40% is almost always a model blind spot (promoted teams, missing news), not value
MAX_EV = float(os.environ.get("MAX_EV", "0.25"))
BOOK = "Bet365"
FD_DIV = {"E0": 47, "E1": 48, "SP1": 87, "I1": 55, "D1": 54, "F1": 53, "N1": 57, "P1": 61}
DOC2LID = {"s325": 268, "s390": 8814, "s17": 47, "s18": 48, "s8": 87, "s23": 55, "s35": 54, "s34": 53, "s37": 57, "s238": 61}
# market groups the user allowed: corners, players, 1X2 & goals, team shots (correct score left out)
ALLOWED = {"res", "dc", "hcp", "gols", "btts", "tg", "st", "sh", "sa", "tt", "th", "ta", "ct", "cx", "chc", "ch", "ca", "c1", "pl"}


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
    day = datetime.fromtimestamp(ts, timezone.utc).date()
    best, score = None, 0.0
    for r in src:
        if r["lid"] != lid or abs((r["day"] - day).days) > 1:
            continue
        if r.get("ts") and abs(r["ts"] - ts) > 3 * 3600:
            continue
        s = sim(hn, r["hn"]) + sim(an, r["an"])
        if s > score:
            best, score = r, s
    return best if score >= 1.2 else None


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
    sources = fd_odds() + op_odds()
    print("jogos com odds da casa:", len(sources), flush=True)
    new = upd = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "model", "s*.json"))):
        doc_id = os.path.basename(f)[:-5]
        lid = DOC2LID.get(doc_id)
        M = json.load(open(f))
        for fx in M.get("fixtures", []):
            if fx["ts"] < now + 600:
                continue
            cand = [s for s in sources if s["lid"] == lid]
            # merge odds of every source for this fixture
            odds, players, srcs = {}, {}, set()
            for s in cand:
                hit = find([s], lid, fx["ts"], fx["hn"], fx["an"])
                if hit:
                    for k, v in hit["odds"].items():
                        odds.setdefault(k, v)
                    players.update(hit.get("players") or {})
                    srcs.add(hit["src"])
            if not odds and not players:
                continue
            fo = {"players": players}
            taken = {family(b["key"]) for b in bets.values() if b.get("fx") == fx["fx"]}
            cands = {}
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
                ev = p * o - 1
                if grp not in ALLOWED or ev < MIN_EV or ev > MAX_EV or p < MIN_P or o > MAX_ODD:
                    continue
                fam = family(key)
                if fam in taken or (fam in cands and cands[fam][0] >= ev):
                    continue
                cands[fam] = (ev, bid, {
                    "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "comp": int(doc_id[1:]),
                    "league": M.get("league"), "h": fx["h"], "a": fx["a"], "hn": fx["hn"], "an": fx["an"],
                    "fx": fx["fx"], "ts": fx["ts"], "key": key, "label": label, "p": p, "odd": o, "ev": round(ev, 4),
                    "stake": 1, "book": BOOK, "auto": True, "src": "+".join(sorted(srcs)), "lineup": fx.get("src"),
                    "status": "pending", "profit": None, "close": None, "closeFairP": None, "clv": None, "closeSrc": None,
                    "lastOdd": o, "lastSeen": int(now), "lastP": p,
                })
            for ev, bid, doc in cands.values():
                bets[bid] = doc
                new += 1
                print(f"  nova: {M.get('league')} {fx['hn']} x {fx['an']} · {doc['label']} @ {doc['odd']} (p {doc['p']:.3f}, VE {ev * 100:+.1f}%)", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(bets, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"apostas automáticas: {new} novas, {upd} com odd atualizada, {len(bets)} no total", flush=True)


if __name__ == "__main__":
    main()
