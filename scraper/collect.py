"""Monte Carlo FC data collector.

Downloads every finished league match of the current season from Sofascore
(team and player shots, shots on target, xG, goals, minutes, who started),
caches each match once, and writes one compact JSON document per league to
data/db/c<id>.json, ready to be stored in the Monte Carlo FC app.

If Sofascore refuses the connection, it falls back to FBref season totals
(no xG, no match-by-match data) for that league.

Run: python scraper/collect.py            (all leagues)
     python scraper/collect.py 325 17     (only these Sofascore tournament ids)
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "cache")
OUT = os.path.join(ROOT, "data", "db")
RECENT_N = 8          # "recent" player numbers cover each team's last N matches
MAX_NEW_EVENTS = int(os.environ.get("MAX_NEW_EVENTS", "900"))  # per run, keeps runs bounded
DELAY = float(os.environ.get("DELAY", "0.8"))

# Sofascore unique-tournament id -> (app name, FBref comp id, FBref slug)
LEAGUES = {
    325: ("Brasileirão Série A", 24, "Serie-A-Stats"),
    390: ("Brasileirão Série B", 38, "Serie-B-Stats"),
    17: ("Premier League", 9, "Premier-League-Stats"),
    8: ("La Liga", 12, "La-Liga-Stats"),
    23: ("Serie A (Itália)", 11, "Serie-A-Stats"),
    35: ("Bundesliga", 20, "Bundesliga-Stats"),
    34: ("Ligue 1", 13, "Ligue-1-Stats"),
}

# ---------------------------------------------------------------- http
try:
    from curl_cffi import requests as http  # browser-like TLS fingerprint
    SESSION = http.Session(impersonate="chrome")
except Exception:  # pragma: no cover
    import requests as http
    SESSION = http.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
})


class Blocked(Exception):
    pass


def get_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=30)
        except Exception as e:  # network error
            last = e
            time.sleep(2 + 3 * i)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        last = f"HTTP {r.status_code}"
        if r.status_code in (403, 429, 503):
            time.sleep(10 + 20 * i + random.random() * 5)
            continue
        time.sleep(2)
    raise Blocked(f"{url}: {last}")


def sofa(path):
    return get_json("https://www.sofascore.com/api/v1/" + path)


# ---------------------------------------------------------------- sofascore
def load_cache(ut, season_id):
    p = os.path.join(CACHE, str(ut), f"{season_id}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(ut, season_id, cache):
    d = os.path.join(CACHE, str(ut))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{season_id}.json"), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))


def match_record(ev, lineups):
    """Compact cached record for one finished match."""
    rec = {
        "ts": ev["startTimestamp"],
        "h": ev["homeTeam"]["id"], "hn": ev["homeTeam"].get("shortName") or ev["homeTeam"]["name"],
        "a": ev["awayTeam"]["id"], "an": ev["awayTeam"].get("shortName") or ev["awayTeam"]["name"],
        "hg": (ev.get("homeScore") or {}).get("current", 0),
        "ag": (ev.get("awayScore") or {}).get("current", 0),
        "p": [],
    }
    if lineups:
        for side, s in (("home", 0), ("away", 1)):
            for pl in (lineups.get(side) or {}).get("players", []):
                st = pl.get("statistics") or {}
                mins = st.get("minutesPlayed") or 0
                if mins <= 0:
                    continue
                pinfo = pl.get("player") or {}
                rec["p"].append([
                    pinfo.get("id"), pinfo.get("name"), s,
                    pl.get("position") or pinfo.get("position") or "M",
                    0 if pl.get("substitute") else 1, mins,
                    st.get("totalShots", 0) or 0, st.get("onTargetScoringAttempt", 0) or 0,
                    round(st.get("expectedGoals", 0) or 0, 3), st.get("goals", 0) or 0,
                ])
    return rec


def collect_sofascore(ut, budget):
    seasons = sofa(f"unique-tournament/{ut}/seasons")
    season = seasons["seasons"][0]
    sid = season["id"]
    cache = load_cache(ut, sid)
    new = 0
    page = 0
    while True:
        data = sofa(f"unique-tournament/{ut}/season/{sid}/events/last/{page}")
        if not data or not data.get("events"):
            break
        events = [e for e in data["events"] if (e.get("status") or {}).get("type") == "finished"]
        fresh = [e for e in events if str(e["id"]) not in cache]
        for ev in fresh:
            if new >= budget:
                break
            lineups = None
            if ev.get("hasEventPlayerStatistics", True):
                try:
                    lineups = sofa(f"event/{ev['id']}/lineups")
                except Blocked:
                    raise
                time.sleep(DELAY + random.random() * 0.4)
            cache[str(ev["id"])] = match_record(ev, lineups)
            new += 1
            if new % 50 == 0:
                save_cache(ut, sid, cache)
                print(f"  {ut}: {new} novos jogos", flush=True)
        if new >= budget or not data.get("hasNextPage"):
            break
        page += 1
        time.sleep(DELAY)
    save_cache(ut, sid, cache)
    return season, cache, new


def build_v2(ut, season, cache):
    name = LEAGUES[ut][0]
    recs = sorted(cache.values(), key=lambda r: r["ts"])
    teams, tidx = [], {}

    def team(i, n):
        if i not in tidx:
            tidx[i] = len(teams)
            teams.append([i, n])
        return tidx[i]

    matches = []
    team_matches = {}
    for r in recs:
        hi, ai = team(r["h"], r["hn"]), team(r["a"], r["an"])
        if r["p"]:
            agg = [[0, 0, 0.0], [0, 0, 0.0]]
            for p in r["p"]:
                a = agg[p[2]]
                a[0] += p[6]; a[1] += p[7]; a[2] += p[8]
            matches.append([r["ts"], hi, ai, r["hg"], r["ag"], agg[0][0], agg[0][1], round(agg[0][2], 2), agg[1][0], agg[1][1], round(agg[1][2], 2)])
        else:
            matches.append([r["ts"], hi, ai, r["hg"], r["ag"], -1, -1, -1, -1, -1, -1])
        team_matches.setdefault(hi, []).append(r["ts"])
        team_matches.setdefault(ai, []).append(r["ts"])

    # cutoff timestamp for each team's last RECENT_N matches
    cutoff = {t: sorted(ts)[-RECENT_N] if len(ts) >= RECENT_N else 0 for t, ts in team_matches.items()}

    players = {}
    for r in recs:
        hi, ai = tidx[r["h"]], tidx[r["a"]]
        for p in r["p"]:
            ti = hi if p[2] == 0 else ai
            key = (p[0], ti)
            row = players.get(key)
            if row is None:
                row = players[key] = [p[0], p[1], ti, p[3][:1] or "M"] + [0] * 14
            row[3] = (p[3] or "M")[:1]  # latest listed position
            vals = [1, p[4], p[5], p[6], p[7], p[8], p[9]]  # mp, st, min, sh, sot, xg, g
            for k, v in enumerate(vals):
                row[4 + k] += v
                if r["ts"] >= cutoff.get(ti, 0):
                    row[11 + k] += v
    rows = []
    for row in players.values():
        row[9] = round(row[9], 2)
        row[16] = round(row[16], 2)
        rows.append(row)
    rows.sort(key=lambda x: (x[2], -x[6]))
    return {
        "v": 2, "comp": ut, "name": name, "season": season.get("name", ""),
        "source": "sofascore.com", "recentN": RECENT_N,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams, "matches": matches, "players": rows,
    }


# ---------------------------------------------------------------- fbref fallback (v1)
def collect_fbref(ut):
    from lxml import html as lh
    name, comp, slug = LEAGUES[ut]

    def page(u):
        r = SESSION.get("https://fbref.com" + u, timeout=30)
        if r.status_code != 200:
            raise Blocked(f"fbref {u}: HTTP {r.status_code}")
        return lh.fromstring(r.text.replace("<!--", "").replace("-->", ""))

    def rows(doc, xp):
        t = doc.xpath(xp)
        if not t:
            return []
        return [tr for tr in t[0].xpath(".//tbody/tr") if "thead" not in (tr.get("class") or "") and tr.xpath(".//*[@data-stat]")]

    def cell(tr, s):
        c = tr.xpath(f'.//*[@data-stat="{s}"]')
        return c[0] if c else None

    def txt(tr, s):
        c = cell(tr, s)
        return c.text_content().strip() if c is not None else ""

    def num(tr, s):
        try:
            return float(txt(tr, s).replace(",", ""))
        except ValueError:
            return 0

    def tid(tr):
        c = cell(tr, "team")
        a = c.xpath(".//a/@href") if c is not None else []
        m = re.search(r"squads/([0-9a-f]+)/", a[0]) if a else None
        return m.group(1) if m else None

    d1 = page(f"/en/comps/{comp}/{slug}")
    season = re.sub(r"\s+", " ", (d1.xpath("//h1")[0].text_content() if d1.xpath("//h1") else "")).strip().replace(" Stats", "")
    teams = {}
    for tr in rows(d1, '//table[contains(@id,"_home_away")]'):
        i = tid(tr)
        if i:
            teams[i] = [i, txt(tr, "team"), num(tr, "home_games"), num(tr, "home_goals_for"), num(tr, "home_goals_against"),
                        num(tr, "away_games"), num(tr, "away_goals_for"), num(tr, "away_goals_against")]
    sq = lambda tr: [num(tr, k) for k in ("minutes_90s", "goals", "shots", "shots_on_target", "pens_made", "pens_att")]
    f, o = {}, {}
    for tr in rows(d1, '//table[@id="stats_squads_shooting_for"]'):
        f[tid(tr)] = sq(tr)
    for tr in rows(d1, '//table[@id="stats_squads_shooting_against"]'):
        o[tid(tr)] = sq(tr)
    time.sleep(5)
    ds = page(f"/en/comps/{comp}/stats/{slug}")
    pl = {}
    for tr in rows(ds, '//table[@id="stats_standard"]'):
        i, c = tid(tr), cell(tr, "player")
        pid = c.get("data-append-csv") if c is not None else None
        if i and pid:
            pl[pid + "_" + i] = [pid, txt(tr, "player"), i, txt(tr, "position"), num(tr, "games"), num(tr, "games_starts"), num(tr, "minutes"), 0, 0, 0, 0, 0]
    time.sleep(5)
    dsh = page(f"/en/comps/{comp}/shooting/{slug}")
    for tr in rows(dsh, '//table[@id="stats_shooting"]'):
        i, c = tid(tr), cell(tr, "player")
        pid = c.get("data-append-csv") if c is not None else None
        p = pl.get(f"{pid}_{i}")
        if p:
            p[7:12] = [num(tr, "goals"), num(tr, "shots"), num(tr, "shots_on_target"), num(tr, "pens_made"), num(tr, "pens_att")]
    empty = [0] * 6
    return {
        "v": 1, "comp": comp, "name": name, "season": season, "source": "fbref.com",
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": [t + f.get(i, empty) + o.get(i, empty) for i, t in teams.items()],
        "players": [p for p in pl.values() if p[6] > 0],
    }


# ---------------------------------------------------------------- output
def write_doc(doc_id, doc):
    """The app's database does not store arrays of arrays, so each row is wrapped as {"r": [...]}."""
    os.makedirs(OUT, exist_ok=True)
    out = dict(doc)
    for k in ("teams", "matches", "players"):
        if k in out:
            out[k] = [{"r": r} for r in out[k]]
    path = os.path.join(OUT, f"{doc_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    return path, os.path.getsize(path)


def main(argv):
    wanted = [int(a) for a in argv] or list(LEAGUES)
    budget = MAX_NEW_EVENTS
    report = []
    for ut in wanted:
        name = LEAGUES[ut][0]
        print(f"== {name}", flush=True)
        try:
            season, cache, new = collect_sofascore(ut, budget)
            budget -= new
            doc = build_v2(ut, season, cache)
            path, size = write_doc(f"s{ut}", doc)
            report.append(f"{name}: Sofascore, {len(doc['matches'])} jogos ({new} novos), {len(doc['players'])} jogadores, {size // 1024} KB")
            stale = os.path.join(OUT, f"c{LEAGUES[ut][1]}.json")
            if os.path.exists(stale):
                os.remove(stale)  # Sofascore data replaces the FBref fallback for this league
        except Blocked as e:
            print(f"  Sofascore bloqueado: {e}", flush=True)
            try:
                doc = collect_fbref(ut)
                path, size = write_doc(f"c{LEAGUES[ut][1]}", doc)
                report.append(f"{name}: FBref (Sofascore bloqueado), {len(doc['players'])} jogadores")
            except Exception as e2:  # noqa: BLE001
                report.append(f"{name}: FALHOU ({e}; FBref: {e2})")
        if budget <= 0:
            report.append("Limite de jogos novos por execução atingido; o restante entra na próxima execução.")
            break
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n" + "\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main(sys.argv[1:])
