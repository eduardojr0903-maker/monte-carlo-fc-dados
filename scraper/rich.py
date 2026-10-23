"""Detailed match records (shot map, goals timeline, red cards, players) for the team model.

One compact record per finished match, kept in data/cache/rich/<league>_<season>.json:
  t   kick-off (UTC ISO)        h, hn, a, an   home/away team id and name
  hg, ag                        final score
  sh  shots: [minute, side, xG, on target, goal, situation, player id]
      situation: R open play, F fast break, C corner, K free kick, P penalty
  gl  goals timeline: [minute, side scoring] (own goals included, credited to the benefiting side)
  rc  red cards: [minute, side]
  pl  players: [player id, side, starter, minutes, xG, xA, shots, on target, goals, position]
"""
import json
import os

SIT = {"RegularPlay": "R", "FastBreak": "F", "FromCorner": "C", "SetPiece": "K", "FreeKick": "K", "ThrowInSetPiece": "K", "Penalty": "P"}
POS = {0: "G", 1: "D", 2: "M", 3: "F"}


def _minute(t, added):
    try:
        t = int(t or 0)
    except (TypeError, ValueError):
        t = 0
    try:
        a = int(added or 0)
    except (TypeError, ValueError):
        a = 0
    return min(t + a * 0.1, 125)


def record(meta, d):
    """meta: {t, h, hn, a, an, hg, ag}; d: FotMob matchDetails JSON."""
    c = (d or {}).get("content") or {}
    home_id = int(meta["h"])
    rec = dict(meta)
    shots = []
    for s in (c.get("shotmap") or {}).get("shots") or []:
        side = 0 if s.get("teamId") == home_id else 1
        shots.append([round(_minute(s.get("min"), s.get("minAdded")), 1), side, round(float(s.get("expectedGoals") or 0), 3),
                      1 if s.get("isOnTarget") else 0, 1 if s.get("eventType") == "Goal" and not s.get("isOwnGoal") else 0,
                      SIT.get(s.get("situation"), "R"), s.get("playerId")])
    rec["sh"] = shots
    goals, reds = [], []
    prev = [0, 0]
    evs = ((c.get("matchFacts") or {}).get("events") or {}).get("events") or []
    for e in evs:
        tp = e.get("type")
        m = round(_minute(e.get("time"), e.get("overloadTime")), 1)
        if tp == "Goal" and not e.get("isPenaltyShootoutEvent"):
            ns = e.get("newScore")
            if isinstance(ns, list) and len(ns) == 2:
                side = 0 if ns[0] > prev[0] else 1
                prev = [int(ns[0]), int(ns[1])]
            else:
                side = 0 if e.get("isHome") else 1
                if e.get("ownGoal"):
                    side = 1 - side
            goals.append([m, side])
        elif tp == "Card" and e.get("card") in ("Red", "YellowRed"):
            reds.append([m, 0 if e.get("isHome") else 1])
    rec["gl"] = goals
    rec["rc"] = reds
    lu = c.get("lineup") or {}
    info = {}
    for side_key, side in (("homeTeam", 0), ("awayTeam", 1)):
        for grp, st in (("starters", 1), ("subs", 0)):
            for p in (lu.get(side_key) or {}).get(grp) or []:
                info[str(p["id"])] = (side, st, POS.get(p.get("usualPlayingPositionId"), "M"))
    players = []
    for pid, ps in (c.get("playerStats") or {}).items():
        f = {}
        for g in ps.get("stats") or []:
            for v in (g.get("stats") or {}).values():
                k = v.get("key")
                stv = v.get("stat") or {}
                if k and k not in f and "value" in stv:
                    f[k] = stv["value"]
        mins = int(f.get("minutes_played") or 0)
        if mins <= 0:
            continue
        side, st, pos = info.get(str(pid), (0 if ps.get("teamId") == home_id else 1, 1 if mins >= 80 else 0, "G" if ps.get("isGoalkeeper") else "M"))
        players.append([str(pid), side, st, mins, round(float(f.get("expected_goals") or 0), 3), round(float(f.get("expected_assists") or 0), 3),
                        int(f.get("total_shots") or 0), int(f.get("ShotsOnTarget") or 0), int(f.get("goals") or 0), pos])
    rec["pl"] = players
    return rec


def path(cache_dir, lid, season):
    return os.path.join(cache_dir, f"{lid}_{str(season).replace('/', '-')}.json")


def load(p):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
