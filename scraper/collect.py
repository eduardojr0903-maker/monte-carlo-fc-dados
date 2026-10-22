"""Monte Carlo FC data collector.

Sources (both answer from GitHub's servers):
- ESPN: every finished league match of the current season, with team and
  player shots, shots on target, goals, who started and minutes played.
- Understat: expected goals (xG) per match and per player for the five big
  European leagues. The Brazilian leagues have no public xG source that
  answers from the cloud, so they run without xG.

Each match is downloaded once and cached in data/cache/. The output is one
compact JSON document per league in data/db/s<id>.json, ready to be stored in
the Monte Carlo FC app.

Run: python scraper/collect.py            (all leagues)
     python scraper/collect.py eng.1      (only these ESPN league codes)
"""
import difflib
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "cache")
OUT = os.path.join(ROOT, "data", "db")
RECENT_N = 8
DELAY = float(os.environ.get("DELAY", "0.4"))
MAX_NEW = int(os.environ.get("MAX_NEW_EVENTS", "3000"))

# ESPN league code -> (doc id, app name, Understat league or None)
LEAGUES = {
    "bra.1": ("s325", "Brasileirão Série A", None),
    "bra.2": ("s390", "Brasileirão Série B", None),
    "eng.1": ("s17", "Premier League", "EPL"),
    "esp.1": ("s8", "La Liga", "La_liga"),
    "ita.1": ("s23", "Serie A (Itália)", "Serie_A"),
    "ger.1": ("s35", "Bundesliga", "Bundesliga"),
    "fra.1": ("s34", "Ligue 1", "Ligue_1"),
}

try:
    from curl_cffi import requests as http
    SESSION = http.Session(impersonate="chrome")
except Exception:  # pragma: no cover
    import requests as http
    SESSION = http.Session()


def get(url, as_json=True, headers=None, tries=4):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=40, headers=headers or {})
            if r.status_code == 200:
                return r.json() if as_json else r.text
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                return None
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(3 + 6 * i + random.random() * 2)
    raise RuntimeError(f"{url}: {last}")


ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/"


# ------------------------------------------------------------------ ESPN
def season_dates(code):
    sb = get(ESPN.format(code) + "scoreboard")
    lg = (sb.get("leagues") or [{}])[0]
    season = lg.get("season") or {}
    cal = lg.get("calendar") or []
    days = sorted({d[:10] for d in cal if isinstance(d, str)})
    if not days:  # fall back to every day from season start
        start = (season.get("startDate") or "")[:10] or f"{date.today().year}-01-01"
        d0 = date.fromisoformat(start)
        days = [(d0 + timedelta(k)).isoformat() for k in range((date.today() - d0).days + 1)]
    today = datetime.now(timezone.utc).date().isoformat()
    return season, [d for d in days if d <= today]


def minute(txt):
    m = re.match(r"\s*(\d+)", txt or "")
    return int(m.group(1)) if m else None


def stat(stats, name):
    for s in stats or []:
        if s.get("name") == name:
            try:
                return float(s.get("value", s.get("displayValue", 0)) or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def pos_of(abbr):
    base = (abbr or "").upper().split("-")[0]
    if base in ("G", "GK"):
        return "G"
    if base in ("", "SUB"):
        return None
    if base in ("F", "CF", "ST", "S", "SS", "LF", "RF", "LW", "RW", "W"):
        return "F"
    if "M" in base:
        return "M"
    if base in ("D", "CD", "CB", "LB", "RB", "LCB", "RCB", "WB", "LWB", "RWB", "SW") or "B" in base or "D" in base:
        return "D"
    return "M"


def match_record(ev, summ):
    comp = ev["competitions"][0]
    teams = {c["homeAway"]: c for c in comp["competitors"]}
    h, a = teams["home"], teams["away"]
    rec = {
        "ts": int(datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp()),
        "d": ev["date"][:10],
        "h": h["team"]["id"], "hn": h["team"].get("shortDisplayName") or h["team"]["displayName"],
        "a": a["team"]["id"], "an": a["team"].get("shortDisplayName") or a["team"]["displayName"],
        "hg": int(float(h.get("score") or 0)), "ag": int(float(a.get("score") or 0)),
        "ts_sh": None, "p": [],
    }
    if not summ:
        return rec
    box = {t.get("homeAway"): t for t in (summ.get("boxscore") or {}).get("teams", [])}
    if "home" in box and "away" in box:
        rec["ts_sh"] = [[stat(box[s].get("statistics"), "totalShots"), stat(box[s].get("statistics"), "shotsOnTarget")] for s in ("home", "away")]
    for ro in summ.get("rosters") or []:
        side = 0 if ro.get("homeAway") == "home" else 1
        for p in ro.get("roster") or []:
            st = p.get("stats") or []
            starter = bool(p.get("starter"))
            if not starter and not p.get("subbedIn"):
                continue
            plays = p.get("plays") or []
            subs = [minute((x.get("clock") or {}).get("displayValue")) for x in plays if x.get("substitution")]
            reds = [minute((x.get("clock") or {}).get("displayValue")) for x in plays if x.get("redCard")]
            subs = [x for x in subs if x is not None]
            if starter:
                off = subs[0] if p.get("subbedOut") and subs else 90
                mins = off
            else:
                on = subs[0] if subs else 75
                off = subs[1] if p.get("subbedOut") and len(subs) > 1 else 90
                mins = max(1, off - on)
            if reds and reds[0] is not None:
                mins = min(mins, max(1, reds[0] - (0 if starter else (subs[0] if subs else 75))))
            ath = p.get("athlete") or {}
            rec["p"].append([
                ath.get("id"), ath.get("displayName"), side,
                pos_of((p.get("position") or {}).get("abbreviation")) if starter else None,
                1 if starter else 0, int(min(90, max(1, mins))),
                int(stat(st, "totalShots")), int(stat(st, "shotsOnTarget")), 0, int(stat(st, "totalGoals")),
            ])
    return rec


def collect_espn(code, budget):
    season, days = season_dates(code)
    sid = str(season.get("year") or date.today().year) + "_" + str(season.get("type") or "")
    path = os.path.join(CACHE, code, f"{sid}.json")
    cache = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"events": {}, "days_done": []}
    done = set(cache["days_done"])
    today = datetime.now(timezone.utc).date()
    new = 0
    for d in days:
        # a day is final once it is 2+ days old and all its matches were stored
        if d in done:
            continue
        sb = get(ESPN.format(code) + "scoreboard?dates=" + d.replace("-", ""))
        time.sleep(DELAY)
        evs = (sb or {}).get("events") or []
        pending = False
        for ev in evs:
            stt = ((ev.get("status") or {}).get("type") or {})
            if not stt.get("completed"):
                if stt.get("state") != "post":
                    pending = True
                continue
            if ev["id"] in cache["events"]:
                continue
            if new >= budget:
                pending = True
                break
            summ = get(ESPN.format(code) + "summary?event=" + ev["id"])
            time.sleep(DELAY + random.random() * 0.3)
            cache["events"][ev["id"]] = match_record(ev, summ)
            new += 1
        if not pending and date.fromisoformat(d) <= today - timedelta(days=2):
            done.add(d)
        if new >= budget:
            break
    cache["days_done"] = sorted(done)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(cache, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return season, cache["events"], new


# ------------------------------------------------------------------ Understat
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s).strip()


def understat(league, year):
    j = get(f"https://understat.com/getLeagueData/{league}/{year}",
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"https://understat.com/league/{league}/{year}"})
    return j or {}


def attach_xg(recs, us):
    """Match Understat teams to ESPN teams by (date, venue, score), then copy xG.
    Returns {(espn_team_id): understat_team_title} and fills rec['xg'] = [home_xg, away_xg]."""
    teams = (us or {}).get("teams") or {}
    if not teams:
        return {}
    # ESPN side: (date, team, h/a, gf, ga) -> record
    idx = {}
    for r in recs:
        idx[(r["d"], r["h"], "h", r["hg"], r["ag"])] = r
        idx[(r["d"], r["a"], "a", r["ag"], r["hg"])] = r
    espn_ids = {r["h"] for r in recs} | {r["a"] for r in recs}
    mapping = {}
    for t in teams.values():
        best, score = None, 0
        for eid in espn_ids:
            n = 0
            for hst in t.get("history", []):
                d = hst["date"][:10]
                for dd in (d, (date.fromisoformat(d) + timedelta(1)).isoformat(), (date.fromisoformat(d) - timedelta(1)).isoformat()):
                    if (dd, eid, hst["h_a"], int(hst["scored"]), int(hst["missed"])) in idx:
                        n += 1
                        break
            if n > score:
                best, score = eid, n
        if best and score >= max(2, len(t.get("history", [])) // 2):
            mapping[best] = t
    for eid, t in mapping.items():
        for hst in t.get("history", []):
            d = hst["date"][:10]
            for dd in (d, (date.fromisoformat(d) + timedelta(1)).isoformat(), (date.fromisoformat(d) - timedelta(1)).isoformat()):
                r = idx.get((dd, eid, hst["h_a"], int(hst["scored"]), int(hst["missed"])))
                if r:
                    r.setdefault("xg", [None, None])
                    r["xg"][0 if hst["h_a"] == "h" else 1] = round(float(hst["xG"]), 2)
                    break
    return {eid: t["title"] for eid, t in mapping.items()}


def player_xg(us, team_map):
    """{(espn_team_id, normalized name): (xg, shots)} from Understat season totals."""
    title_to_espn = {v: k for k, v in team_map.items()}
    out = {}
    for p in (us or {}).get("players") or []:
        for title in (p.get("team_title") or "").split(","):
            eid = title_to_espn.get(title.strip())
            if eid:
                out.setdefault(eid, []).append((norm(p["player_name"]), float(p.get("xG") or 0), int(p.get("shots") or 0)))
    return out


def find_player(cands, name):
    n = norm(name)
    if not cands:
        return None
    names = [c[0] for c in cands]
    if n in names:
        return cands[names.index(n)]
    last = n.split()[-1] if n.split() else n
    hits = [c for c in cands if c[0].split() and c[0].split()[-1] == last]
    if len(hits) == 1:
        return hits[0]
    m = difflib.get_close_matches(n, names, n=1, cutoff=0.75)
    return cands[names.index(m[0])] if m else None


# ------------------------------------------------------------------ build
def build(code, season, events, us):
    doc_id, name, us_league = LEAGUES[code]
    recs = sorted(events.values(), key=lambda r: r["ts"])
    team_map = attach_xg(recs, us) if us_league else {}
    has_xg = bool(team_map)
    teams, tidx = [], {}

    def team(i, n):
        if i not in tidx:
            tidx[i] = len(teams)
            teams.append([i, n])
        return tidx[i]

    # league xG per shot, to fill the few matches Understat could not be matched to
    tot_sh = tot_xg = 0
    for r in recs:
        if r.get("xg") and None not in r["xg"] and r.get("ts_sh"):
            tot_sh += r["ts_sh"][0][0] + r["ts_sh"][1][0]
            tot_xg += r["xg"][0] + r["xg"][1]
    xg_per_shot = tot_xg / tot_sh if tot_sh else 0.1

    matches, team_ts = [], {}
    for r in recs:
        hi, ai = team(r["h"], r["hn"]), team(r["a"], r["an"])
        sh = r.get("ts_sh")
        if not sh:
            sh = [[0, 0], [0, 0]]
            for p in r["p"]:
                sh[p[2]][0] += p[6]; sh[p[2]][1] += p[7]
            if not r["p"]:
                sh = None
        if sh:
            xg = r.get("xg") or [None, None]
            if has_xg:
                xg = [x if x is not None else round(sh[i][0] * xg_per_shot, 2) for i, x in enumerate(xg)]
            else:
                xg = [-1, -1]
            matches.append([r["ts"], hi, ai, r["hg"], r["ag"], int(sh[0][0]), int(sh[0][1]), xg[0], int(sh[1][0]), int(sh[1][1]), xg[1]])
        else:
            matches.append([r["ts"], hi, ai, r["hg"], r["ag"], -1, -1, -1, -1, -1, -1])
        team_ts.setdefault(hi, []).append(r["ts"])
        team_ts.setdefault(ai, []).append(r["ts"])
    cutoff = {t: sorted(v)[-RECENT_N] if len(v) >= RECENT_N else 0 for t, v in team_ts.items()}

    players, posc = {}, {}
    for r in recs:
        hi, ai = tidx[r["h"]], tidx[r["a"]]
        for p in r["p"]:
            ti = hi if p[2] == 0 else ai
            key = (str(p[0]), ti)
            row = players.setdefault(key, [str(p[0]), p[1], ti, "M"] + [0] * 14)
            if p[3]:
                posc.setdefault(key, {}).setdefault(p[3], 0)
                posc[key][p[3]] += 1
            vals = [1, p[4], p[5], p[6], p[7], 0, p[9]]
            for k, v in enumerate(vals):
                row[4 + k] += v
                if r["ts"] >= cutoff.get(ti, 0):
                    row[11 + k] += v
    pxg = player_xg(us, team_map) if has_xg else {}
    espn_of_idx = {v: k for k, v in tidx.items()}
    for key, row in players.items():
        if key in posc:
            row[3] = max(posc[key].items(), key=lambda kv: kv[1])[0]
        if has_xg:
            hit = find_player(pxg.get(espn_of_idx[row[2]]), row[1])
            per = (hit[1] / hit[2]) if hit and hit[2] > 0 else xg_per_shot
            row[9] = round(hit[1] if hit and abs(hit[2] - row[7]) <= max(3, row[7] * 0.3) else row[7] * per, 2)
            row[16] = round(row[14] * per, 2)
        else:
            row[9] = row[16] = -1
    rows = sorted(players.values(), key=lambda x: (x[2], -x[6]))
    return doc_id, {
        "v": 2, "comp": int(doc_id[1:]), "name": name,
        "season": season.get("displayName") or season.get("name") or str(season.get("year", "")),
        "source": "espn.com" + (" + understat.com" if has_xg else ""), "hasXg": has_xg, "recentN": RECENT_N,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams, "matches": matches, "players": rows,
    }


def write_doc(doc_id, doc):
    """Rows are packed into one JSON string field ("pack"): the app's database counts
    nested arrays at a high per-value cost and caps a document at 256 KiB."""
    os.makedirs(OUT, exist_ok=True)
    out = {k: v for k, v in doc.items() if k not in ("teams", "matches", "players")}
    out["pack"] = json.dumps({k: doc[k] for k in ("teams", "matches", "players")}, ensure_ascii=False, separators=(",", ":"))
    path = os.path.join(OUT, f"{doc_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path)


def main(argv):
    wanted = argv or list(LEAGUES)
    budget = MAX_NEW
    report = []
    for code in wanted:
        doc_id, name, us_league = LEAGUES[code]
        print(f"== {name}", flush=True)
        try:
            season, events, new = collect_espn(code, budget)
            budget -= new
            us = {}
            if us_league:
                y = int(season.get("year") or date.today().year)
                recent = (datetime.now(timezone.utc).date() - timedelta(days=45)).isoformat()
                for year in (y, y - 1):
                    try:
                        cand = understat(us_league, year)
                    except Exception as e:  # noqa: BLE001
                        report.append(f"{name}: Understat indisponível ({e}); seguindo sem xG")
                        break
                    dates = [h["date"][:10] for t in (cand.get("teams") or {}).values() for h in t.get("history", [])]
                    if dates and max(dates) >= recent:
                        us = cand
                        break
            doc_id, doc = build(code, season, events, us)
            size = write_doc(doc_id, doc)
            with_stats = sum(1 for m in doc["matches"] if m[5] >= 0)
            report.append(f"{name}: {len(doc['matches'])} jogos ({new} novos, {with_stats} com chutes), {len(doc['players'])} jogadores, xG {'sim' if doc['hasXg'] else 'não'}, {size // 1024} KB")
        except Exception as e:  # noqa: BLE001
            report.append(f"{name}: FALHOU ({e})")
        if budget <= 0:
            report.append("Limite de jogos novos por execução atingido; o restante entra na próxima execução.")
            break
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n" + "\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main(sys.argv[1:])
