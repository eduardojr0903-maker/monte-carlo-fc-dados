"""Builds data/odds/oddspapi/mapping.json from OddsPapi's market list: market id -> our market keys.
Only lines ending in .5 are used (integer and quarter lines can push, which the model does not price).
Usage: python scraper/oddspapi_build_mapping.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "data", "odds", "oddspapi")


def line(h):
    h = float(h)
    return h if abs(h * 2 - round(h * 2)) < 1e-9 and abs(h - int(h)) > 0.1 else None


def fmt(h):
    return ("%g" % h)


def main():
    ml = json.load(open(os.path.join(REF, "markets.json")))
    ml = ml if isinstance(ml, list) else ml.get("data", [])
    out = {}
    for m in ml:
        t, per, h = m.get("marketType"), m.get("period"), m.get("handicap") or 0.0
        oc = {str(o["outcomeId"]): o["outcomeName"] for o in m.get("outcomes") or []}
        mp = {}
        kind = "team"
        if t == "1x2" and per == "fulltime":
            mp = {k: {"1": "1", "X": "X", "2": "2"}.get(v) for k, v in oc.items()}
        elif t == "doublechance" and per == "fulltime":
            mp = {k: {"1X": "1X", "12": "12", "2X": "X2", "X2": "X2"}.get(v) for k, v in oc.items()}
        elif t == "drawnobet" and per == "fulltime":
            mp = {k: {"1": "DNB1", "2": "DNB2"}.get(v) for k, v in oc.items()}
        elif t == "bothteamsscore" and per == "fulltime":
            mp = {k: {"Yes": "BTTS-S", "No": "BTTS-N"}.get(v) for k, v in oc.items()}
        elif t == "spreads" and per == "fulltime" and float(h) in (-1.5, 1.5):
            # Asian handicap given for team 1
            if float(h) == -1.5:
                mp = {k: {"1": "H-1.5", "2": "A+1.5"}.get(v) for k, v in oc.items()}
            else:
                mp = {k: {"1": "H+1.5", "2": "A-1.5"}.get(v) for k, v in oc.items()}
        elif t == "1x2-corners" and per == "fulltime":
            mp = {k: {"1": "CX1", "X": "CXX", "2": "CX2"}.get(v) for k, v in oc.items()}
        elif t in ("totals", "teamtotals-team1", "teamtotals-team2", "totals-corners", "teamtotals-corners-team1",
                   "teamtotals-corners-team2") and line(h) is not None:
            pref = {("totals", "fulltime"): "G", ("teamtotals-team1", "fulltime"): "HG", ("teamtotals-team2", "fulltime"): "AG",
                    ("totals-corners", "fulltime"): "C", ("totals-corners", "p1"): "C1",
                    ("teamtotals-corners-team1", "fulltime"): "CHt", ("teamtotals-corners-team2", "fulltime"): "CAt"}.get((t, per))
            if pref:
                mp = {k: {"Over": f"{pref}>{fmt(line(h))}", "Under": f"{pref}<{fmt(line(h))}"}.get(v) for k, v in oc.items()}
        elif t in ("players-shots", "players-shotsongoal", "players-anytimegoalscorer"):
            kind = "player"
            stat = {"players-shots": "sh", "players-shotsongoal": "sot", "players-anytimegoalscorer": "g"}[t]
            keep = {"sh": ("1+", "2+", "3+"), "sot": ("1+", "2+"), "g": ("Yes",)}[stat]
            mp = {k: ("1" if v == "Yes" else v[:-1]) for k, v in oc.items() if v in keep}
            mp = {k: v for k, v in mp.items() if v}
            if mp:
                out[str(m["marketId"])] = {"kind": "player", "stat": stat, "outcomes": mp, "name": m.get("marketName")}
            continue
        mp = {k: v for k, v in mp.items() if v}
        if mp:
            out[str(m["marketId"])] = {"kind": kind, "outcomes": mp, "name": m.get("marketName")}
    json.dump(out, open(os.path.join(REF, "mapping.json"), "w"), ensure_ascii=False, indent=0)
    print(len(out), "mercados mapeados")


if __name__ == "__main__":
    main()
