"""Full match calendar (every competition) for the teams in our leagues, 2022 onwards.

Used to measure rest days and fixture congestion: cups, continental competitions, state leagues and
super cups all count. Competitions are found in FotMob's league list: every competition of Brazil,
England, Spain, Italy, Germany and France plus the international club competitions.
Output: data/calendar/<competition id>.json = {"name", "seasons": {season: [[match id, utc, home id, away id, finished]]}}
Only matches involving a team from our leagues are kept.
Usage: python scraper/calendar_collect.py [--current]   (--current: only refresh the current season)
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import fm, LEAGUES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "calendar")
COUNTRIES = {"BRA", "ENG", "ESP", "ITA", "GER", "FRA", "NED", "POR"}
INTL_KEYS = ("champions league", "europa league", "conference league", "libertadores", "sudamericana",
             "recopa", "super cup", "club world cup", "supercup", "uefa super", "intercontinental")
FIRST_YEAR = 2022


def our_teams():
    ids = set()
    for f in glob.glob(os.path.join(ROOT, "data", "cache", "history_full", "*.json")) + \
            glob.glob(os.path.join(ROOT, "data", "cache", "fotmob", "*.json")):
        try:
            c = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        for fx in c.get("fixtures") or []:
            if isinstance(fx, list) and len(fx) >= 5:
                ids.add(str(fx[2])); ids.add(str(fx[4]))
        for r in (c.get("m") or {}).values():
            if isinstance(r, dict):
                ids.add(str(r.get("h"))); ids.add(str(r.get("a")))
    for d in glob.glob(os.path.join(ROOT, "data", "db", "s*.json")):
        try:
            p = json.loads(json.load(open(d))["pack"])
            for t in p.get("teams") or []:
                ids.add(str(t[0]))
        except Exception:  # noqa: BLE001
            pass
    ids.discard("None")
    return ids


def competitions():
    al = fm("allLeagues", locale="en") or {}
    out = {}
    for grp in (al.get("countries") or []) + (al.get("international") or []):
        cc = grp.get("ccode") or ""
        for lg in grp.get("leagues") or []:
            name = (lg.get("name") or "").strip()
            lid = lg.get("id")
            if lid is None or lid in LEAGUES:
                continue
            if cc in COUNTRIES:
                if any(w in name.lower() for w in ("women", "u19", "u20", "u21", "u23", "youth", "femin", "frauen", "reserve")):
                    continue
                out[int(lid)] = f"{cc} {name}"
            elif cc == "INT" or grp.get("name", "").lower().startswith("int"):
                if any(k in name.lower() for k in INTL_KEYS) and "women" not in name.lower() and "qual" not in name.lower():
                    out[int(lid)] = f"INT {name}"
    return out


def seasons_of(lg):
    out = []
    for s in lg.get("allAvailableSeasons") or []:
        try:
            if int(str(s)[:4]) >= FIRST_YEAR:
                out.append(str(s))
        except ValueError:
            pass
    return out


def rows_of(lg, teams):
    rows = []
    for m in (lg.get("fixtures") or {}).get("allMatches") or []:
        st = m.get("status") or {}
        if st.get("cancelled"):
            continue
        h, a = str((m.get("home") or {}).get("id")), str((m.get("away") or {}).get("id"))
        if h not in teams and a not in teams:
            continue
        rows.append([str(m.get("id")), st.get("utcTime"), h, a, 1 if st.get("finished") else 0])
    return rows


def main(current_only=False):
    os.makedirs(OUT, exist_ok=True)
    teams = our_teams()
    comps = competitions()
    print("times", len(teams), "competições", len(comps), flush=True)
    for cid, name in sorted(comps.items()):
        path = os.path.join(OUT, f"{cid}.json")
        cur = json.load(open(path)) if os.path.exists(path) else {"name": name, "seasons": {}}
        try:
            lg = fm("leagues", id=cid) or {}
        except Exception as e:  # noqa: BLE001
            print("falhou", cid, name, e, flush=True)
            continue
        sel = str((lg.get("details") or {}).get("selectedSeason") or "")
        todo = [sel] if current_only else seasons_of(lg)
        n = 0
        for s in todo:
            if not s:
                continue
            if s != sel and s in cur["seasons"]:
                continue
            try:
                d = lg if s == sel else (fm("leagues", id=cid, season=s.replace("/", "%2F")) or {})
            except Exception as e:  # noqa: BLE001
                print("falhou", cid, s, e, flush=True)
                continue
            cur["seasons"][s] = rows_of(d, teams)
            n += len(cur["seasons"][s])
            time.sleep(0.6)
        if any(cur["seasons"].values()):
            cur["name"] = name
            json.dump(cur, open(path, "w"), separators=(",", ":"))
            print(cid, name, "jogos", n, flush=True)
        time.sleep(0.4)


if __name__ == "__main__":
    main("--current" in sys.argv)
