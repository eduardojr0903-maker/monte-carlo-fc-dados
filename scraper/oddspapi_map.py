"""Converts an OddsPapi odds-by-tournaments response (Bet365) into the model's market keys.

mapping.json is built from OddsPapi's own market list (data/odds/oddspapi/markets.json), once it has been
downloaded with the API key: {market id: {"kind": ..., "line": ..., "outcomes": {outcome id: our key}}}.
Player markets: {"kind": "player", "stat": "sh"|"sot"|"g", "outcomes": {outcome id: k}}.
Until the mapping exists this returns nothing, and the robot uses football-data (1X2 and goals) only.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fixtures(resp):
    d = resp.get("data", resp)
    return d if isinstance(d, list) else d.get("data", []) if isinstance(d, dict) else []


def _pname(n):
    """'Garcia, Alvaro' -> 'Alvaro Garcia'"""
    if "," in n:
        last, first = [x.strip() for x in n.split(",", 1)]
        return f"{first} {last}".strip()
    return n


def convert(resp, mapping, participants=None):
    out = []
    tours = {str(v): int(k) for k, v in (resp.get("tournaments") or {}).items()}
    names = participants
    if names is None:
        pp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds", "oddspapi", "participants.json")
        names = json.load(open(pp)) if os.path.exists(pp) else {}
    for f in _fixtures(resp):
        lid = tours.get(str(f.get("tournamentId")))
        if not lid:
            continue
        try:
            st = datetime.fromisoformat(str(f.get("startTime")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        day, ts = st.date(), int(st.timestamp())
        hn = f.get("participant1Name") or names.get(str(f.get("participant1Id")), "")
        an = f.get("participant2Name") or names.get(str(f.get("participant2Id")), "")
        bo = (f.get("bookmakerOdds") or {}).get(resp.get("book") or "bet365.bet.br") or {}
        odds, players = {}, {}
        for mid, m in (bo.get("markets") or {}).items():
            mp = mapping.get(str(mid))
            if not mp:
                continue
            for oid, o in (m.get("outcomes") or {}).items():
                target = mp["outcomes"].get(str(oid))
                if target is None:
                    continue
                for _, pl in (o.get("players") or {}).items():
                    price = pl.get("price")
                    if not price or not pl.get("active", True):
                        continue
                    if mp.get("kind") == "player":
                        nm = _pname(pl.get("playerName") or "")
                        if nm:
                            players[(nm, f"{mp['stat']}{target}")] = float(price)
                    else:
                        odds[target] = float(price)
        if odds or players:
            out.append({"lid": lid, "day": day, "ts": ts, "hn": hn, "an": an, "odds": odds, "players": players, "src": "oddspapi"})
    return out
