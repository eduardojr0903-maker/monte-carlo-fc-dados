"""Builds team-level backtest cases from the 2022+ history, with closing odds attached.

For every match of every season after the first, it writes the data the app would have had
right before kickoff (earlier matches of that season + the history summary of earlier seasons),
the result, and the closing odds from football-data.co.uk when the match can be matched.

Usage: python scraper/backtest_build.py OUT.jsonl
"""
import csv
import difflib
import glob
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, "data", "cache", "history_full")
CUR = os.path.join(ROOT, "data", "cache", "fotmob")
ODDS = os.path.join(ROOT, "data", "odds")
FD = {47: "E0", 48: "E1", 87: "SP1", 55: "I1", 54: "D1", 53: "F1"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|ac|sc|afc|ec|se|cr|ca|club|de|the|and|utd|united|city|town|real|sporting|atletico|athletic)\b", " ", s)
    return re.sub(r"[^a-z]", "", s)


def season_key(lid, season):
    y = int(str(season)[:4])
    return f"{y % 100:02d}{(y + 1) % 100:02d}" if lid in FD else str(y)


def load_odds(lid):
    rows = []
    files = glob.glob(os.path.join(ODDS, f"{FD[lid]}_*.csv")) if lid in FD else ([os.path.join(ODDS, "BRA.csv")] if lid == 268 else [])
    for f in files:
        if not os.path.exists(f):
            continue
        with open(f, encoding="utf-8-sig", errors="ignore") as fh:
            for r in csv.DictReader(fh):
                try:
                    d = datetime.strptime(r.get("Date", ""), "%d/%m/%Y" if len(r.get("Date", "")) == 10 else "%d/%m/%y").date()
                except ValueError:
                    continue
                h = r.get("HomeTeam") or r.get("Home")
                a = r.get("AwayTeam") or r.get("Away")
                try:
                    hg, ag = int(r.get("FTHG") or r.get("HG")), int(r.get("FTAG") or r.get("AG"))
                except (TypeError, ValueError):
                    continue

                def num(*keys):
                    for k in keys:
                        try:
                            v = float(r.get(k) or "")
                            if v > 1:
                                return v
                        except ValueError:
                            pass
                    return None
                rows.append({
                    "d": d, "h": norm(h), "a": norm(a), "hg": hg, "ag": ag,
                    "pin": [num("PSCH"), num("PSCD"), num("PSCA")],
                    "avg": [num("AvgCH"), num("AvgCD"), num("AvgCA")],
                    "max": [num("MaxCH"), num("MaxCD"), num("MaxCA")],
                    "ou_pin": [num("PC>2.5"), num("PC<2.5")],
                    "ou_avg": [num("AvgC>2.5"), num("AvgC<2.5")],
                    "ou_max": [num("MaxC>2.5"), num("MaxC<2.5")],
                    # earlier prices (collected days before kickoff)
                    "pin_e": [num("PSH"), num("PSD"), num("PSA")],
                    "avg_e": [num("AvgH"), num("AvgD"), num("AvgA")],
                    "max_e": [num("MaxH"), num("MaxD"), num("MaxA")],
                    "b365_e": [num("B365H"), num("B365D"), num("B365A")],
                    "ou_pin_e": [num("P>2.5"), num("P<2.5")],
                    "ou_avg_e": [num("Avg>2.5"), num("Avg<2.5")],
                    "ou_max_e": [num("Max>2.5"), num("Max<2.5")],
                })
    by_day = {}
    for r in rows:
        by_day.setdefault(r["d"], []).append(r)
    return by_day


def find_odds(by_day, day, hn, an, hg, ag):
    best, score = None, 0.0
    for dd in (day, day - timedelta(1), day + timedelta(1)):
        for r in by_day.get(dd, []):
            if r["hg"] != hg or r["ag"] != ag:
                continue
            s = difflib.SequenceMatcher(None, norm(hn), r["h"]).ratio() + difflib.SequenceMatcher(None, norm(an), r["a"]).ratio()
            if s > score:
                best, score = r, s
    return best if score >= 0.9 else None


def to_rec(r):
    if "t" in r and isinstance(r.get("t"), str):  # history format
        ts = int(datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp())
        s = r.get("s")
        t = [[s[0][0], s[1][0], s[2][0]], [s[0][1], s[1][1], s[2][1]]] if s else None
    else:  # current-season format
        ts = r["ts"]
        t = r.get("t")
    return {"ts": ts, "h": r["h"], "hn": r["hn"], "a": r["a"], "an": r["an"], "hg": r["hg"], "ag": r["ag"], "t": t}


def hist_rec(r):
    t = r["t"]
    return {"h": r["h"], "hn": r["hn"], "a": r["a"], "an": r["an"], "hg": r["hg"], "ag": r["ag"],
            "s": [[t[0][0], t[1][0]], [t[0][1], t[1][1]], [t[0][2], t[1][2]]] if t else None}


def main(out_path):
    out = open(out_path, "w")
    leagues = sorted({int(os.path.basename(f).split("_")[0]) for f in glob.glob(os.path.join(FULL, "*.json"))})
    for lid in leagues:
        seasons = {}
        for f in glob.glob(os.path.join(FULL, f"{lid}_*.json")):
            season = os.path.basename(f)[len(str(lid)) + 1:-5].replace("-", "/")
            c = json.load(open(f))
            seasons[season] = sorted([to_rec(r) for r in c["m"].values() if r], key=lambda r: r["ts"])
        for f in glob.glob(os.path.join(CUR, f"{lid}_*.json")):
            season = os.path.basename(f)[len(str(lid)) + 1:-5].replace("-", "/")
            seasons[season] = sorted([to_rec(r) for r in json.load(open(f)).values()], key=lambda r: r["ts"])
        order = sorted(seasons)
        odds = load_odds(lid)
        n_odds = n = 0
        for si, season in enumerate(order[1:], 1):
            past = {s: {str(i): hist_rec(r) for i, r in enumerate(seasons[s])} for s in order[:si]}
            recs = seasons[season]
            team_ids = sorted({r["h"] for r in recs} | {r["a"] for r in recs})
            tidx = {t: i for i, t in enumerate(team_ids)}
            names = {}
            for r in recs:
                names[r["h"]] = r["hn"]; names[r["a"]] = r["an"]
            for i, m in enumerate(recs):
                prior = recs[:i]
                full_h = history.summarize(past, [hist_rec(r) for r in prior], team_ids)
                hist = {k: full_h[k] for k in ("hfa", "prior", "teamHfa")}
                matches = []
                for r in prior:
                    t = r["t"]
                    matches.append([r["ts"], tidx[r["h"]], tidx[r["a"]], r["hg"], r["ag"]] +
                                   ([int(t[0][0]), int(t[0][1]), t[0][2], int(t[1][0]), int(t[1][1]), t[1][2]] if t else [-1] * 6))
                L = {"v": 3, "teams": [[t, names[t]] for t in team_ids], "matches": matches, "players": [], "next": [],
                     "hist": hist, "hasXg": True}
                day = datetime.fromtimestamp(m["ts"], timezone.utc).date()
                o = find_odds(odds, day, m["hn"], m["an"], m["hg"], m["ag"]) if odds else None
                if o:
                    n_odds += 1
                    o = {k: v for k, v in o.items() if k not in ("d", "h", "a")}
                n += 1
                out.write(json.dumps({"lid": lid, "season": season, "i": i, "L": L,
                                      "m": {"h": m["h"], "a": m["a"], "hg": m["hg"], "ag": m["ag"], "t": m["t"]}, "odds": o}) + "\n")
        print(lid, "jogos", n, "com odds", n_odds, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
